# Perf log soak (JAR-90, follow-up to the 2026-11-04 perf baseline)

Hermetic: fake clock, tmp dirs, no Core, mic, network or Pi. Tool: `PYTHONPATH=core/src python core/tools/log_soak.py`;
test: `core/tests/test_log_soak.py`. Base: origin/main `e52fe5c4`.

## 1. Simulated idle hour (health sampler 60 s, 5-min store flush, health heartbeat)
- store.db growth over the hour (WAL folded in): 4 KB (4096 B); store.db total 240 KB.
- health.json rewrites: 30 per hour (about every 2 min in the simulation).
- No jarves.log line is written by these idle writers.

## 2. Rotation caps (flood far above a real day)
| Log | Cap | Before | After |
|---|---|---|---|
| jarves.log (RotatingFileHandler, test caps 200 KB x 3) | 602 KB | 496 KB | 496 KB |
| approvals/audit.jsonl (+ .1) | about 2 MB | 1.14 MB | 1.14 MB |
| instagram/audit.jsonl | none | 4.51 MB for 16 000 events, unbounded | 512 KB + one .1 file (cap 1 MB) |

Other append-only files read in the code and left alone: dashboard/mobile/dialing/approvals/browser-pilot audits and
crash.txt already rotate; tasks.jsonl is trimmed; the GitHub write audit is a tamper-evident hash chain, unbounded
by design (event-driven, owner-approved writes only; not changed, Tier R-adjacent); daily notes are one file per day.

## Change
`instagram_channel.audit()` rotates at 500 KB (`AUDIT_MAX_BYTES`), one older file kept, same pattern as the other audits.
Not user-facing, so no guides entry. Tier N.

## UNVERIFIED
Wake-on bench, Pi 5 numbers, idle CPU/RSS on a real Core, and the real jarves.log rate with live voice: not measured here.
