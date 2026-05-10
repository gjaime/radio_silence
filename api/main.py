#!/usr/bin/env python3
"""
Silence — Radio Auditor Status API
silence.kraken-lab.work

Sirve la página de estado y los endpoints JSON.
Lee las vistas materializadas (refresh cada minuto via refresh_views.py en radio_auditor).

Deploy en CT240:
  WorkingDirectory = /opt/radio_silence
  ExecStart = /opt/radio_auditor/venv/bin/uvicorn api.main:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

DB_DSN = os.environ.get(
    "DB_DSN",
    "host=localhost dbname=radio_auditor user=radio password=radio_auditor_2026",
)
STATIC_DIR = Path(__file__).parent.parent / "static"

app = FastAPI(title="Silence — Radio Auditor", docs_url=None, redoc_url=None)


def get_conn():
    conn = psycopg2.connect(DB_DSN)
    conn.set_client_encoding("UTF8")
    return conn


# ── SQL ───────────────────────────────────────────────────────────────────────

ALL_STATIONS_SQL = """
SELECT station_id, name, network, city, state
FROM   stations
WHERE  active = TRUE
ORDER  BY station_id;
"""

STATUS_SQL = {
    24:  ("SELECT station_id, name, network, city, state, bucket, total, online_cnt, audio_ok_cnt"
          " FROM mv_status_5min WHERE bucket > NOW() - INTERVAL '24 hours'"
          " ORDER BY station_id, bucket;"),
    100: ("SELECT station_id, name, network, city, state, bucket, total, online_cnt, audio_ok_cnt"
          " FROM mv_status_1h  WHERE bucket > NOW() - INTERVAL '100 hours'"
          " ORDER BY station_id, bucket;"),
}

INCIDENTS_SQL = {
    24:  ("SELECT station_id, station_name, city, network,"
          " started_at_utc, started_at_cdmx, ended_at_utc, ended_at_cdmx,"
          " duration_seconds, type, alert_level"
          " FROM mv_incidents_live WHERE started_at_utc > NOW() - INTERVAL '24 hours'"
          " ORDER BY started_at_utc DESC;"),
    100: ("SELECT station_id, station_name, city, network,"
          " started_at_utc, started_at_cdmx, ended_at_utc, ended_at_cdmx,"
          " duration_seconds, type, alert_level"
          " FROM mv_incidents_live WHERE started_at_utc > NOW() - INTERVAL '100 hours'"
          " ORDER BY started_at_utc DESC;"),
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _bucket_status(total: int, online: int, audio_ok: int) -> str:
    if not total:
        return "nodata"
    if online / total < 0.5:
        return "offline"
    if audio_ok / total < 0.95:
        return "silence"
    return "ok"


def _build_station_map(
    station_rows: list, bucket_rows: list, hours: int
) -> dict[str, Any]:
    window_seconds = hours * 3600  # segundos esperados en la ventana completa

    smap: dict[str, Any] = {
        r["station_id"]: {
            "station_id":  r["station_id"],
            "name":        r["name"],
            "network":     r["network"],
            "city":        r["city"],
            "state":       r["state"],
            "buckets":     [],
        }
        for r in station_rows
    }
    for row in bucket_rows:
        sid = row["station_id"]
        if sid not in smap:
            continue
        total    = int(row["total"]        or 0)
        online   = int(row["online_cnt"]   or 0)
        audio_ok = int(row["audio_ok_cnt"] or 0)
        smap[sid]["buckets"].append({
            "time":     row["bucket"].isoformat(),
            "total":    total,
            "online":   online,
            "audio_ok": audio_ok,
            "status":   _bucket_status(total, online, audio_ok),
        })
    for st in smap.values():
        sampled  = sum(b["total"]    for b in st["buckets"])
        online   = sum(b["online"]   for b in st["buckets"])
        audio_ok = sum(b["audio_ok"] for b in st["buckets"])
        # Uptime sobre la ventana COMPLETA: períodos sin datos cuentan como desconocidos
        # → denominador = max(sampled, window_seconds) para no inflar si hay más datos
        denom = max(sampled, window_seconds)
        st["uptime_pct"]    = round(online   / denom * 100, 1) if denom else None
        st["audio_ok_pct"]  = round(audio_ok / denom * 100, 1) if denom else None
        # Cobertura: qué fracción de la ventana tenemos datos
        st["coverage_pct"]  = round(sampled  / window_seconds * 100, 1) if window_seconds else None
        st["sampled_hours"] = round(sampled / 3600, 1)
    return smap


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/api/status")
def api_status(hours: int = 24) -> JSONResponse:
    if hours not in (24, 100):
        raise HTTPException(400, "hours must be 24 or 100")

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(ALL_STATIONS_SQL)
            stations = cur.fetchall()
            cur.execute(STATUS_SQL[hours])
            buckets = cur.fetchall()
    finally:
        conn.close()

    return JSONResponse({
        "hours":          hours,
        "bucket_minutes": 5 if hours == 24 else 60,
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "stations":       list(_build_station_map(stations, buckets, hours).values()),
    })


@app.get("/api/incidents")
def api_incidents(hours: int = 24) -> JSONResponse:
    if hours not in (24, 100):
        raise HTTPException(400, "hours must be 24 or 100")

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(INCIDENTS_SQL[hours])
            rows = cur.fetchall()
    finally:
        conn.close()

    return JSONResponse({
        "hours":        hours,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "incidents": [
            {
                "station_id":       r["station_id"],
                "station_name":     r["station_name"],
                "city":             r["city"],
                "network":          r["network"],
                "started_at":       r["started_at_utc"].isoformat(),
                "started_at_cdmx":  r["started_at_cdmx"].isoformat(),
                "ended_at":         r["ended_at_utc"].isoformat(),
                "ended_at_cdmx":    r["ended_at_cdmx"].isoformat(),
                "duration_seconds": r["duration_seconds"],
                "type":             r["type"],
                "alert_level":      r["alert_level"],
            }
            for r in rows
        ],
    })


# Archivos estáticos — debe ir después de todas las rutas API
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
