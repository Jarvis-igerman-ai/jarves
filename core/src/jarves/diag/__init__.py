"""Self-Diagnostics 2.0 (SW-V4 package O, issue #17): JARVES notices his own problems early, explains them with
evidence pointing at the exact code, commit and log lines, proposes fixes, and repairs himself only through the
approvals flow. Day to day it costs no tokens.

Modules (each one small and testable on its own; real SQLite in tests, fixed clocks):
- db          the diag_* tables in store.db (metrics time series, log-code counts, events, findings, preflight,
              canary, AI review, remediation, tickets) and the settings defaults (health.*)
- monitor     continuous health monitor inside the Core: event-driven (a log sink + health listener) plus a 60 s
              sampler (CPU, RAM, handles, threads, disk, event-loop lag); 1-minute roll-ups for 7 days, hourly for 90
- anomaly     14-day baselines (median and MAD, hour-of-day buckets where seasonality matters), k*MAD for N
              consecutive samples, new log-code clusters, CUSUM rate steps, 7-day slopes (RAM creep, disk growth)
- regression  the first 24 h after an update against the previous version's last 7 days ("version report")
- logindex    root-cause hints from log_codes.json (built by tools/build_log_index.py: code -> file:line, function,
              last commit), plus 10 redacted surrounding log lines
- preflight   the start-up self-check (budget 1.5 s) before CORE_READY; a failed item degrades its component only
- remediation the closed list of auto-repairs, each ONLY through approvals (pre-check, action, post-check)
- tickets     ticket drafts (local files; filed only when health.ticket_repo is set AND the owner approves, on a private repo); duplicates become comments
- ai_review   the scheduled, SELECTIVE AI review (rule layer picks first; skipped at $0 when nothing is new;
              OpenAI backend = a different model family than the Claude models that wrote the code; caps)
- canary      nightly synthetic user-journey canaries on a throw-away TEST profile
- service     the Core service, voice tools, dashboard API (Health page), `jarvis health` CLI, feature registration
"""
FEATURE = "self_diagnostics_2"
