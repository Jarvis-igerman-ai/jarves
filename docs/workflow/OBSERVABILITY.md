# Factory observability (contract for the console UI)

Why: the factory once stalled for about 13 hours with no way to see when or why each layer stopped.
Everything below is local, model-free and read-only over HTTP. All timestamps are epoch seconds.

## Layers and beats
Layers: `supervisor, autopilot, wrapper, manager, core, bridge`. A beat is the ledger event kind `layer_beat`
(plus one meta row with the latest beat per layer). Write one with
`workflow_console.py layer-beat --layer L --state ok|nudged|restarted|stopped --note TEXT`.
The supervisor beats supervisor/autopilot each tick and core/bridge every 60 s; the claude-j wrapper beats every 60 s.
Status: `ok` age <= threshold, `stale` <= 2x, else `dead` (never beaten = `dead`, state `never`; state `stopped` = `dead`).
Thresholds: supervisor 3 min, autopilot 15 min, wrapper 3 min, manager 30 min (last native hook activity), core and bridge 3 min.
The supervisor watches the wrapper: a wrapper beat older than 5 min raises the alert `watcher_dead:wrapper`.

## GET /api/health
`{"layers":[{"layer","status","state","last_beat_at","age_s","note"}],"incidents":[{"key","kind","since","summary","task_id"}],"now":ts}`
`task_id` is the ops triage task once one was opened (else null).

## GET /api/timeline?hours=24 (0..336)
`{"since","until","segments":[{"layer","state","start","end"}],"marks":[{"ts","kind","layer","summary"}],
"gaps":[{"layer","start","end","seconds","ready_work"}],"pipeline":[{"ts","task_id","title","lane","from_state","to_state","agent"}]}`
A gap is silence longer than the layer threshold; `ready_work` = ready authorised tasks waiting during it.
Mark layers: supervisor, autopilot, manager, ledger. Both endpoints need the same session as `/api/state`.
CLI for terminals and agents: `workflow_console.py timeline --hours 24 [--json]` and `workflow_console.py health`.

## Incidents
Detected: `supervisor_dead` (silent > 5 min, or found at restart), `factory_idle_with_ready_work` (autopilot on, ready work,
no worker or manager activity > 20 min), `autopilot_paused_long` (> 30 min), `repeated_supervisor_error` /
`repeated_autopilot_error` (>= 5 in 30 min), `wrapper_dead`. A new key (4 h cooldown) creates ONE ledger task in lane
`ops` titled `Incident triage: <key>` (timeline of the last 2 h in `scope_note`) and an alert. The supervisor runs no LLM;
the autopilot `lanes.ops` is OFF (0 workers) by default, so the manager or the owner picks the task.

## Logs
`<workflow home>/supervisor.log`: timestamped start/stop reasons, web child exits, loop errors and the supervisor's stderr
(rotated to `supervisor.log.1` at 5 MB). The autopilot records why a tick started nothing as the event `autopilot_idle`
(reason NO_CORE, PAUSED, LANE_FULL, NO_READY_TASK, CLAIM_FAILED, POLICY_OFF), only on a change or every 30 min.
