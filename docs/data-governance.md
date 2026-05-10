# Data Governance

## Overview

This document describes the policies that govern data quality, freshness, retention, and access in the radio-silence monitoring system.

---

## Data freshness

### Monitor cadence

The `radio_auditor` daemon polls each station once per second. Each poll produces exactly one row in `radio_monitor`, regardless of the result (success or failure).

| Metric | Value |
|---|---|
| Poll interval | 1 second |
| Granularity | 1 row per station per second |
| Lag (DB write) | < 100 ms after poll |

### Materialized view refresh

Three materialized views are refreshed once per minute via a cron job. The total refresh cycle takes approximately 1–2 seconds for a fleet of ~20 stations.

| View | Refresh mode | Typical duration | Purpose |
|---|---|---|---|
| `mv_status_5min` | `CONCURRENTLY` | ~0.4 s | 24-hour tab |
| `mv_status_1h` | `CONCURRENTLY` | ~0.5 s | 100-hour tab |
| `mv_incidents_live` | Blocking | ~0.15 s | Incidents table |

**`CONCURRENTLY`** means the view remains readable during refresh — queries are never blocked. The unique index on `(station_id, bucket)` is required for this mode.

**`mv_incidents_live`** uses a blocking refresh because gap-and-island detection cannot produce stable intermediate results; however, it is fast enough (< 0.2 s) that blocking is acceptable.

### Dashboard staleness

The dashboard auto-refreshes every 60 seconds. In the worst case, a user may see data that is up to ~2 minutes old (view refresh lag + dashboard poll interval). For a radio monitoring use case, this is acceptable — incident response does not require sub-minute precision at the dashboard layer.

---

## Data quality

### Noise filtering

Very short anomalies (< 3 seconds) are excluded from the incidents view:

```sql
HAVING COUNT(*) >= 3  -- at least 3 consecutive anomalous seconds
```

This filters transient network jitter, brief encoder hiccups, and probe artifacts that do not constitute operationally meaningful events.

### Unknown vs. downtime

Periods with no data in `radio_monitor` are treated as **unknown**, not as downtime. This distinction matters for uptime calculation:

- **Uptime denominator = sampled seconds** (only periods where data exists)
- **Coverage % = sampled seconds / window seconds** — a separate signal that indicates how complete the monitoring was

A station that was monitored for 10 of 24 hours and was online for all 10 hours reports **100% uptime, 42% coverage** — not 42% uptime.

Operators should treat uptime figures with low coverage (< 95%) as provisional.

### Active vs. inactive stations

The `stations.active` flag controls whether a station appears in the dashboard and materialized views. When a station is deactivated:
- `active = FALSE` is set in the `stations` table
- All three materialized views filter it out via `AND s.active = TRUE`
- Historical rows in `radio_monitor` are preserved for audit/archival purposes
- The station disappears from the dashboard after the next view refresh (within 1 minute)

This pattern allows stations to be temporarily removed from monitoring (e.g., while a correct stream URL is located) without losing historical data.

---

## Data retention

| Data | Retention | Enforcement |
|---|---|---|
| `radio_monitor` raw rows | 30 days | TimescaleDB retention policy |
| Compressed chunks (> 7 days old) | 30 days | TimescaleDB compression policy |
| Materialized view data | Rolling window (25 h / 101 h) | View definition + refresh |

Raw data older than 30 days is automatically dropped by TimescaleDB's `add_retention_policy`. This keeps the hypertable at a manageable size while preserving a full month of second-level resolution for forensic analysis.

To change the retention period:

```sql
SELECT alter_job(
    (SELECT job_id FROM timescaledb_information.jobs
     WHERE application_name LIKE 'Retention%'
     LIMIT 1),
    config => '{"drop_after": "60 days"}'
);
```

### Archival (roadmap)

Long-term incident history (beyond 30 days) can be preserved by:
- Exporting `mv_incidents_live` to a flat file or object storage before the raw data ages out
- Maintaining a separate `incidents_archive` table populated from the live view

This is not yet implemented.

---

## Access control

### Principle of least privilege

The API connects to PostgreSQL with a **read-only application user** (`radio` by default). This user has:

- `CONNECT` on the database
- `SELECT` on `stations`, `radio_monitor`, and all three materialized views
- No write privileges

The monitor daemon uses a separate user with `INSERT` on `radio_monitor` only.

### Secrets management

Database credentials are passed via environment variable (`DB_DSN`) sourced from a protected file at `/etc/radio_auditor/db.env` (mode `0600`, owned by the service user). Credentials must never be committed to source control.

The `.gitignore` excludes `.env` files. The `db.env` path is outside the repository.

### Public API

The `/api/status` and `/api/incidents` endpoints are unauthenticated and public. They expose only:
- Station names, cities, states, and networks
- Aggregated uptime statistics
- Incident timestamps, types, and severities

They do **not** expose:
- Stream URLs
- Internal infrastructure details
- Raw per-second telemetry

---

## Operational runbook

### Check if views are being refreshed

```sql
SELECT schemaname, matviewname, last_refresh
FROM pg_stat_user_tables
WHERE relname LIKE 'mv_%';
-- Or:
SELECT * FROM timescaledb_information.jobs WHERE application_name LIKE 'Refresh%';
```

### Force a manual refresh

```bash
python /path/to/radio_auditor/scripts/refresh_views.py
```

### Check cron job

```bash
crontab -l | grep refresh_views
```

### Verify data is flowing

```sql
SELECT station_id, MAX(time) AS last_seen
FROM radio_monitor
GROUP BY station_id
ORDER BY last_seen DESC;
```

### Add a new station

```sql
INSERT INTO stations (station_id, name, network, city, state, stream_url)
VALUES ('mx_xx_city_1234', 'Radio Example 123.4 FM', 'Network Name', 'City', 'State',
        'https://stream.example.com/radio1234');
```

The station will appear in the dashboard after the next view refresh.

### Deactivate a station

```sql
UPDATE stations SET active = FALSE WHERE station_id = 'mx_xx_city_1234';
```
