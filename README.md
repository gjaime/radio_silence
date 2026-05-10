# radio-silence

> "If a station goes silent, who notices first?"

**radio-silence** is an open-source radio broadcast monitoring system. It continuously probes internet radio streams, stores per-second health telemetry in a time-series database, and exposes a public status dashboard — similar in spirit to a cloud-service status page, but for radio stations.

---

## What problem does this solve?

Radio stations stream their signal over the internet as a backup or primary distribution channel. When a stream goes down — or worse, goes silent (audio stops but the connection stays up) — it may take minutes or hours before anyone inside the organization notices.

**radio-silence** solves this by:

1. **Polling every station every second** — detecting both `offline` (connection failure) and `silence` (audio absence) conditions.
2. **Classifying incidents by severity** — from a 3-second blip to a full station outage.
3. **Publishing a live status dashboard** — any operator, journalist, or listener can see uptime and incident history at a glance.

---

## Live demo

**[silence.kraken-lab.work](https://silence.kraken-lab.work)**

### Dashboard — vista de emisoras (últimas 24 h)

![Dashboard — vista de emisoras](docs/img/screenshot-dashboard.png)

### Tabla de incidentes

![Tabla de incidentes](docs/img/screenshot-incidents.png)

---

## Architecture overview

```
┌─────────────────────────────────┐
│  radio_auditor (monitor daemon) │
│  – polls N stations every 1 s   │
│  – writes to TimescaleDB        │
└────────────────┬────────────────┘
                 │ INSERT (1 row/station/s)
                 ▼
┌─────────────────────────────────┐
│  PostgreSQL 16 + TimescaleDB    │
│  hypertable: radio_monitor      │
│  materialized views (1-min refresh)│
└────────────────┬────────────────┘
                 │ SELECT (pre-computed)
                 ▼
┌─────────────────────────────────┐
│  radio_silence (this repo)      │
│  FastAPI — /api/status          │
│           — /api/incidents      │
│  Static HTML status dashboard   │
└────────────────┬────────────────┘
                 │ HTTPS
                 ▼
             Browser
```

The monitor daemon (`radio_auditor`) and this API (`radio_silence`) are **separate repositories** that share the same PostgreSQL database. This repo only reads from the DB.

See [`docs/architecture.md`](docs/architecture.md) for a full breakdown.

---

## Repository layout

```
radio_silence/
├── api/
│   ├── main.py            # FastAPI app — status + incidents endpoints
│   └── requirements.txt
├── static/
│   └── index.html         # Single-page status dashboard
├── systemd/
│   └── silence-api.service
├── docs/
│   ├── architecture.md
│   ├── data-model.md
│   ├── data-governance.md
│   └── fault-taxonomy.md
└── README.md
```

---

## Quick start (local development)

### Prerequisites

- Python 3.11+
- PostgreSQL 16 with [TimescaleDB](https://docs.timescale.com/install/latest/) extension
- A running instance of `radio_auditor` (or historical data already loaded)

### 1 — Clone and install

```bash
git clone https://github.com/youruser/radio-silence.git
cd radio-silence
python -m venv .venv && source .venv/bin/activate
pip install -r api/requirements.txt
```

### 2 — Configure the database connection

```bash
export DB_DSN="host=localhost dbname=radio_auditor user=radio password=YOUR_PASSWORD"
```

Or create `/etc/radio_auditor/db.env`:

```
DB_DSN=host=localhost dbname=radio_auditor user=radio password=YOUR_PASSWORD
```

### 3 — Create materialized views

Run the view definitions against your PostgreSQL instance. The views are maintained in `radio_auditor`'s `db/views.sql`. They require the `classify_alert()` function defined there as well.

### 4 — Start the API

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8080
```

Visit `http://localhost:8080`.

---

## Deploying to production

See [`docs/architecture.md`](docs/architecture.md) for the recommended deployment topology.

A systemd unit is provided at `systemd/silence-api.service`. Copy it to `/etc/systemd/system/`, adjust paths, then:

```bash
systemctl daemon-reload
systemctl enable --now silence-api
```

### Materialized view refresh

The views must be refreshed periodically so the dashboard shows current data. Add a cron job on the database host:

```
* * * * * /path/to/venv/bin/python /path/to/radio_auditor/scripts/refresh_views.py
```

The refresh cycle takes under 2 seconds for a fleet of ~20 stations.

### Reverse proxy / TLS

The API binds on HTTP port 8080. Terminate TLS at a reverse proxy (Nginx, Caddy, Cloudflare Tunnel, etc.) before exposing to the internet.

---

## API reference

### `GET /api/status?hours=24|100`

Returns per-station uptime buckets for the requested window.

| Field | Description |
|---|---|
| `uptime_pct` | % of *monitored* seconds the connection was online |
| `audio_ok_pct` | % of *monitored* seconds audio was present |
| `coverage_pct` | % of the window for which data exists |
| `sampled_hours` | Actual hours of telemetry in the window |

### `GET /api/incidents?hours=24|100`

Returns all detected incidents (silence + offline) in the window.

See [`docs/fault-taxonomy.md`](docs/fault-taxonomy.md) for severity definitions.

---

## Roadmap

- [ ] **Grafana dashboard** — time-series graphs, alert rules, on-call integration
- [ ] **Physical RF validation** — connect a software-defined radio (SDR) receiver to validate the over-the-air signal directly, independent of internet connectivity
- [ ] **Multi-site deployment** — run monitor agents from geographically separate locations; cross-correlate results to distinguish station outages from ISP/connectivity issues at the monitoring site (see [Known Limitations](#known-limitations))
- [ ] **Alert notifications** — webhook / email / messaging integration on incident open/close
- [ ] **Historical reports** — weekly/monthly uptime summaries per station and network

---

## Known limitations

### ISP dependency — false negatives

The current architecture monitors streams **from a single location**. If the monitoring site's internet connection degrades, all stations will appear to go offline simultaneously — a false alarm.

A robust production deployment should:
- Run monitor agents from **at least two independent network connections**
- Require **consensus** across agents before declaring an incident
- Consider a **cloud-hosted agent** (outside the local ISP) as an authoritative reference

### One-second polling granularity

The monitor probes each station once per second. Very brief interruptions (< 3 s) are filtered as noise. Events between 1–2 s in duration may not be captured.

---

## License

MIT — see `LICENSE`.

---

## Contributing

Issues and pull requests welcome. Please open an issue before submitting large changes.
