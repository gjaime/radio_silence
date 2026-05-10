# Architecture

## System overview

radio-silence is the **read-only status layer** of a two-component radio monitoring system:

| Component | Role |
|---|---|
| `radio_auditor` | Monitor daemon — polls streams, writes telemetry |
| `radio_silence` *(this repo)* | Status API + dashboard — reads pre-computed views |

Both components share the same PostgreSQL database but are deployed as independent services.

---

## Logical architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        Internet Radio Streams                     │
│          station-A.example.com:8000   station-B.example.com:8000  │
└──────────────────────────────────┬───────────────────────────────┘
                                   │ HTTP probe (1 s interval)
                                   ▼
┌──────────────────────────────────────────────────────────────────┐
│                    radio_auditor  (monitor daemon)                │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  monitor.py                                                  │ │
│  │  – asyncio HTTP client                                       │ │
│  │  – audio frame decoder (Icecast/Shoutcast/HLS)              │ │
│  │  – writes 1 row per station per second to radio_monitor      │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  refresh_views.py  (cron, every minute)                      │ │
│  │  – REFRESH CONCURRENTLY mv_status_5min                       │ │
│  │  – REFRESH CONCURRENTLY mv_status_1h                         │ │
│  │  – REFRESH mv_incidents_live                                  │ │
│  └─────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────┬───────────────────────────────┘
                                   │ INSERT / REFRESH
                                   ▼
┌──────────────────────────────────────────────────────────────────┐
│           PostgreSQL 16 + TimescaleDB                             │
│                                                                   │
│  Tables          │  Materialized views                            │
│  ───────────     │  ──────────────────────────────────────────── │
│  stations        │  mv_status_5min    (5-min buckets, last 25 h)  │
│  radio_monitor   │  mv_status_1h      (1-h buckets,  last 101 h)  │
│  (hypertable)    │  mv_incidents_live (gap-and-island incidents)   │
└──────────────────────────────────┬───────────────────────────────┘
                                   │ SELECT (pre-computed, < 50 ms)
                                   ▼
┌──────────────────────────────────────────────────────────────────┐
│                    radio_silence  (this repo)                     │
│                                                                   │
│  FastAPI (uvicorn)                                                │
│  ├── GET /api/status?hours=24|100                                 │
│  ├── GET /api/incidents?hours=24|100                              │
│  └── /  → static/index.html  (single-page dashboard)             │
└──────────────────────────────────┬───────────────────────────────┘
                                   │ HTTPS (reverse proxy / tunnel)
                                   ▼
                              Browser / API clients
```

---

## Deployment topology

The recommended deployment uses:

- A **Linux server or VM** hosting PostgreSQL + TimescaleDB and both application processes
- A **reverse proxy** (Nginx, Caddy, or a Cloudflare Tunnel) for TLS termination and public exposure

```
Internet ──► [Cloudflare / Reverse Proxy] ──► radio_silence API :8080
                                                      │
                                               PostgreSQL :5432
                                                      │
                                          radio_auditor (same host)
```

For a homelab or single-server deployment, all three services can run on the same machine. For production use at scale, consider separating the database from the application tier.

### Network exposure

Only the reverse proxy port (443/HTTPS) needs to be internet-facing. The API (`:8080`) and database (`:5432`) should remain on a private network.

---

## Data flow timeline

```
T+0s    monitor.py polls all N stations in parallel
T+0s    1 row inserted per station into radio_monitor (hypertable)
        ...
T+60s   cron triggers refresh_views.py
T+61s   mv_status_5min refreshed CONCURRENTLY  (~0.4 s)
T+61s   mv_status_1h   refreshed CONCURRENTLY  (~0.5 s)
T+62s   mv_incidents_live refreshed (blocking)  (~0.15 s)
        ...
T+any   Browser polls /api/status → query hits materialized view → < 50 ms
```

The dashboard auto-refreshes every 60 seconds, aligned with the view refresh cadence.

---

## Scalability considerations

| Dimension | Current | Notes |
|---|---|---|
| Stations | ~20 | Tested; asyncio scales to hundreds |
| Poll interval | 1 second | Lower bound constrained by DB write throughput |
| Data retention | 30 days | Configurable via TimescaleDB retention policy |
| API response time | < 50 ms | Reads only pre-computed views |
| View refresh time | ~1.2 s total | Scales with station count and data volume |

For large fleets (100+ stations), consider:
- Increasing the poll interval to 5–10 seconds
- Partitioning materialized views by network or region
- Deploying a read replica for dashboard queries

---

## Multi-site architecture (roadmap)

The current single-site design has an inherent blind spot: if the monitoring host's internet connection fails, all stations appear to go offline simultaneously — a false negative.

The planned multi-site architecture addresses this:

```
Site A (primary)          Site B (secondary)         Cloud probe
monitor_A.py ──► DB ◄── monitor_B.py             monitor_cloud.py
                  │                                     │
                  └─────────── consensus engine ─────────┘
                               │
                       only declare incident if
                       ≥ 2 sites agree
```

This eliminates ISP-level false positives and allows geographic differentiation (e.g., a CDN edge issue that only affects one region).

---

## Physical RF validation (roadmap)

Internet streams are a secondary distribution channel; the primary signal is broadcast over the air. A stream can be healthy while the transmitter is down (pre-recorded content re-streaming), or vice versa.

Future versions may integrate a **software-defined radio (SDR)** receiver co-located with the monitoring station to:
- Tune directly to each station's assigned FM frequency
- Measure signal strength (RSSI) and audio presence on the analog signal
- Cross-reference with the stream probe to distinguish transmitter issues from stream/CDN issues

This would require additional hardware (SDR dongle + antenna) and a signal processing pipeline, but would provide ground-truth validation independent of internet infrastructure.
