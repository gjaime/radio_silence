# Fault Taxonomy

## Overview

radio-silence detects and classifies two types of broadcast faults, each assessed at one-second resolution. Incidents are then assigned a severity level based on their duration.

---

## Fault types

### `silence` — Audio absence

| Field | Value |
|---|---|
| `online` | TRUE |
| `audio_ok` | FALSE |
| Meaning | The stream endpoint is reachable, but no audio frames are present in the response body |

**What this typically means:**
- The transmitter or encoding equipment has failed
- The audio source (studio, automation system) has gone silent
- A silence detector at the encoder has triggered
- The encoder is streaming a carrier but no audio content

**Why this matters:** A silent stream is the most operationally dangerous failure mode. The CDN and distribution infrastructure appear healthy — monitoring tools that only check HTTP connectivity will not catch this. Listeners experience dead air. The station may not be aware unless an internal silence relay or a monitor like this one is in place.

**Detection method:** The monitor decodes the first N bytes of the audio response and checks for non-zero audio frame content. The exact threshold is configurable.

---

### `offline` — Connection failure

| Field | Value |
|---|---|
| `online` | FALSE |
| `audio_ok` | FALSE |
| Meaning | The HTTP/TCP connection to the stream URL failed, timed out, or returned an error status code |

**What this typically means:**
- The streaming server is down
- The CDN or edge node serving the stream is unreachable
- A network path between the monitoring host and the stream is broken
- The stream URL has changed or been discontinued

**Caveat — ISP false positives:** If the monitoring host's own internet connection fails, all stations will simultaneously appear `offline`. This is a false positive. See [Known Limitations in the README](../README.md#known-limitations) and the multi-site roadmap item.

---

## Severity levels

Each detected incident (a continuous sequence of anomalous seconds, ≥ 3 s) is assigned a severity level by the `classify_alert()` database function.

| Level | Duration range | Description |
|---|---|---|
| `low` | 3 – 5 s | Transient — likely encoder hiccup, brief network jitter, or probe artifact. Usually self-corrects. |
| `medium_low` | 6 – 15 s | Short interruption — noticeable to listeners, may indicate an intermittent connectivity issue. |
| `medium` | 16 – 30 s | Moderate interruption — clearly audible dead air or stream gap. Warrants attention. |
| `high` | 31 – 120 s | Significant incident — listeners are noticing and likely switching stations. Requires investigation. |
| `critical` | 2 – 5 min | Extended outage — measurable audience impact. Operational response recommended. |
| `severe` | 5 – 30 min | Major outage — substantial broadcast interruption. Active incident management required. |
| `outage` | > 30 min | Full outage — the station has been off-air or silent for a prolonged period. Emergency response. |

### Dashboard color mapping

| Level | Color | Badge |
|---|---|---|
| `low` | Blue | LOW |
| `medium_low` | Teal | MED-LOW |
| `medium` | Yellow | MEDIUM |
| `high` | Orange | HIGH |
| `critical` | Red-orange | CRITICAL |
| `severe` | Red | SEVERE |
| `outage` | Dark red | OUTAGE |

---

## Noise floor

Events shorter than 3 seconds are silently discarded. This is implemented in the SQL view:

```sql
HAVING COUNT(*) >= 3
```

The 3-second threshold is calibrated to eliminate:
- Single-probe failures caused by momentary network jitter
- Encoder restart artifacts (brief silence during codec reload)
- HTTP keep-alive timeout reconnections
- Probe-side CPU load spikes causing delayed reads

Events of 1–2 seconds are logged in the raw `radio_monitor` table but do not appear in the incidents view.

---

## Incident lifecycle

An incident begins when the first anomalous sample is recorded and ends at the **last consecutive anomalous sample** before a return to normal. The gap-and-island SQL assigns each uninterrupted sequence a unique group ID.

```
Time:     1  2  3  4  5  6  7  8  9  10
State:    ok ok sl sl sl ok ok sl sl ok
                  ^^^^^^^^           ^^^
                  incident 1         incident 2
                  (3 s, low)         (2 s, discarded)
```

In this example:
- Seconds 3–5: silence for 3 consecutive seconds → **`low` incident**
- Seconds 8–9: silence for 2 consecutive seconds → **discarded** (below noise floor)

---

## Currently open incidents

An incident is considered **open** if its `ended_at` equals the timestamp of the most recently refreshed data point. Since the view captures the maximum anomalous timestamp as `ended_at`, a station that is still down will have `ended_at` advance with each refresh.

The dashboard does not explicitly distinguish open from closed incidents in the incidents table — the recency of `ended_at` relative to the current time is the indicator.

---

## Future enhancements

### Alert routing by severity

Planned: when an incident crosses a configurable threshold (e.g., `high`), trigger a notification via webhook, email, or a messaging platform. Notification channels and severity thresholds would be configurable per station.

### Incident acknowledgment

Planned: allow operators to acknowledge an active incident, suppressing repeat notifications and recording who responded and when.

### Physical RF validation

A `silence` detection via internet stream does not necessarily mean the transmitter is off the air — the stream encoder may have failed while the antenna continues broadcasting. Conversely, a healthy stream may be re-airing recorded content while the transmitter is down.

Physical validation using an SDR receiver co-located with the monitoring host would add a third verification layer:

| Condition | Stream | RF | Interpretation |
|---|---|---|---|
| Normal | healthy | signal present | All systems nominal |
| Encoder failure | silence | signal present | Studio/encoder problem |
| Transmitter failure | healthy | no signal | Transmitter/antenna problem |
| Full outage | offline | no signal | Complete broadcast failure |
| ISP issue | offline | signal present | Monitoring host connectivity problem (false positive) |

This cross-referencing would dramatically reduce false positives and enable precise fault localization.
