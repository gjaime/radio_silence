# Data Model

All tables and views live in the `public` schema of the `radio_auditor` PostgreSQL database.

---

## Tables

### `stations`

Station registry — one row per monitored stream.

```sql
CREATE TABLE stations (
    station_id  TEXT        PRIMARY KEY,   -- e.g. "mx_gto_leon_1071"
    name        TEXT        NOT NULL,      -- display name
    network     TEXT,                      -- broadcast group / chain
    city        TEXT,
    state       TEXT,
    stream_url  TEXT        NOT NULL,      -- Icecast / HLS / Shoutcast URL
    active      BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**`station_id` convention:** `{country}_{state_abbr}_{city}_{freq_without_dot}`

Example: `mx_gto_leon_1071` → Mexico, Guanajuato, León, 107.1 FM

Only rows with `active = TRUE` appear in the dashboard and materialized views. Deactivated stations are preserved for historical queries.

---

### `radio_monitor`

Core time-series table — one row per station per second.

```sql
CREATE TABLE radio_monitor (
    time        TIMESTAMPTZ NOT NULL,   -- UTC timestamp (hypertable partition key)
    time_cdmx   TIMESTAMP   NOT NULL,   -- Local time (America/Mexico_City, naive)
    station_id  TEXT        NOT NULL REFERENCES stations(station_id),
    online      BOOLEAN     NOT NULL,   -- TCP/HTTP connection succeeded
    audio_ok    BOOLEAN     NOT NULL,   -- Audio frames present in response body
    latency_ms  INTEGER,                -- HTTP response time in milliseconds
    status_code INTEGER                 -- HTTP status code (NULL if connection failed)
);

SELECT create_hypertable('radio_monitor', 'time');
```

**Compression policy:** chunks older than 7 days are compressed automatically.

**Segment by:** `station_id` (improves per-station range scans).

**Approximate volume:** 1 row/station/second × N stations × 86 400 s/day.
For 20 stations: ~1.7 M rows/day, ~50 M rows/month.

#### Field semantics

| Condition | `online` | `audio_ok` |
|---|---|---|
| Stream up, audio present | TRUE | TRUE |
| Stream up, audio absent (silence) | TRUE | FALSE |
| Connection refused / timeout | FALSE | FALSE |
| HTTP error (4xx, 5xx) | FALSE | FALSE |

`audio_ok = FALSE` with `online = TRUE` is the **silence** condition — the most operationally significant fault type, as it indicates the transmitter or encoder has failed while the CDN/server remains up.

---

## Materialized views

Materialized views are refreshed every minute by `refresh_views.py`. They are the **only** data sources queried by the API; raw `radio_monitor` is never read on request.

### `mv_status_5min`

Pre-aggregated 5-minute buckets, last 25 hours. Serves the 24-hour dashboard tab.

```sql
CREATE MATERIALIZED VIEW mv_status_5min AS
SELECT
    s.station_id,
    s.name, s.network, s.city, s.state,
    time_bucket('5 minutes', r.time)           AS bucket,
    COUNT(*)                                   AS total,
    SUM(CASE WHEN r.online   THEN 1 ELSE 0 END) AS online_cnt,
    SUM(CASE WHEN r.audio_ok THEN 1 ELSE 0 END) AS audio_ok_cnt
FROM radio_monitor r
JOIN stations s ON s.station_id = r.station_id
WHERE r.time > NOW() - INTERVAL '25 hours'
  AND s.active = TRUE
GROUP BY s.station_id, s.name, s.network, s.city, s.state,
         time_bucket('5 minutes', r.time);

CREATE UNIQUE INDEX mv_status_5min_pk ON mv_status_5min (station_id, bucket);
```

Refreshed with `REFRESH CONCURRENTLY` — no read lock on the view during refresh.

### `mv_status_1h`

Pre-aggregated 1-hour buckets, last 101 hours. Serves the 100-hour dashboard tab.

Same structure as `mv_status_5min` with `time_bucket('1 hour', ...)` and `INTERVAL '101 hours'`.

### `mv_incidents_live`

Gap-and-island incident detection, last 101 hours. Covers both silence and offline events.

```sql
CREATE MATERIALIZED VIEW mv_incidents_live AS
WITH classified AS (
    SELECT r.station_id,
           r.time       AS t_utc,
           r.time_cdmx  AS t_cdmx,
           CASE
               WHEN NOT r.online   THEN 'offline'
               WHEN NOT r.audio_ok THEN 'silence'
           END AS evt
    FROM radio_monitor r
    JOIN stations s ON s.station_id = r.station_id AND s.active = TRUE
    WHERE r.time > NOW() - INTERVAL '101 hours'
      AND (NOT r.online OR NOT r.audio_ok)
),
grouped AS (
    SELECT *,
        ROW_NUMBER() OVER (PARTITION BY station_id       ORDER BY t_utc)
      - ROW_NUMBER() OVER (PARTITION BY station_id, evt  ORDER BY t_utc) AS grp
    FROM classified
),
islands AS (
    SELECT station_id, evt,
           MIN(t_utc)    AS started_at_utc,
           MIN(t_cdmx)   AS started_at_cdmx,
           MAX(t_utc)    AS ended_at_utc,
           MAX(t_cdmx)   AS ended_at_cdmx,
           COUNT(*)::INT AS duration_seconds
    FROM grouped
    GROUP BY station_id, evt, grp
    HAVING COUNT(*) >= 3          -- discard events shorter than 3 s (noise)
)
SELECT i.station_id,
       s.name AS station_name, s.city, s.network,
       i.started_at_utc, i.started_at_cdmx,
       i.ended_at_utc,   i.ended_at_cdmx,
       i.duration_seconds,
       i.evt AS type,
       classify_alert(i.duration_seconds) AS alert_level
FROM islands i
JOIN stations s ON s.station_id = i.station_id
ORDER BY i.started_at_utc DESC;
```

**Gap-and-island technique:** The double `ROW_NUMBER()` subtraction assigns a constant group number (`grp`) to every consecutive sequence of the same event type for a given station. Gaps in the sequence (moments when the station was healthy) produce a different `grp`, splitting the sequence into separate islands.

---

## Functions

### `classify_alert(duration_seconds INTEGER) → TEXT`

Maps a duration to one of seven severity levels. See [`fault-taxonomy.md`](fault-taxonomy.md) for the full classification table.

```sql
CREATE OR REPLACE FUNCTION classify_alert(duration_seconds INTEGER)
RETURNS TEXT LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN duration_seconds <=     5 THEN 'low'
        WHEN duration_seconds <=    15 THEN 'medium_low'
        WHEN duration_seconds <=    30 THEN 'medium'
        WHEN duration_seconds <=   120 THEN 'high'
        WHEN duration_seconds <=   300 THEN 'critical'
        WHEN duration_seconds <=  1800 THEN 'severe'
        ELSE                                'outage'
    END
$$;
```

---

## Uptime calculation

Uptime is calculated over **monitored time only** — periods with no data (monitor stopped, network gap) are treated as unknown, not as downtime.

```
uptime_pct    = online_samples   / sampled_seconds × 100
audio_ok_pct  = audio_ok_samples / sampled_seconds × 100
coverage_pct  = sampled_seconds  / window_seconds  × 100
```

Where `sampled_seconds = SUM(total)` across all buckets for a station in the window, and `window_seconds = hours × 3600`.

A station with `coverage_pct < 95%` should be interpreted with caution — the uptime figure only reflects the fraction of the window for which data exists.

---

## Indexes

| Index | Table / View | Columns | Purpose |
|---|---|---|---|
| Primary key | `stations` | `station_id` | Lookup by ID |
| Hypertable PK | `radio_monitor` | `time, station_id` | TimescaleDB partition + sort |
| Unique | `mv_status_5min` | `station_id, bucket` | CONCURRENT refresh |
| Unique | `mv_status_1h` | `station_id, bucket` | CONCURRENT refresh |

---

## Entity-relationship summary

```
stations (1) ──────────── (N) radio_monitor
    │                             │
    │                             └── aggregated into ──► mv_status_5min
    │                             └── aggregated into ──► mv_status_1h
    │                             └── gap-and-island  ──► mv_incidents_live
    │
    └── station_id is the join key for all views
```
