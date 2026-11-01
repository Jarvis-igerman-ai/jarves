"""Factory observability: layer beats, timeline, incidents and the supervisor log.

Table-free: beats are ledger events of kind ``layer_beat`` (plus one meta row with the latest beat per layer),
pipeline moves are ``task_flow`` events. Nothing here starts a process or an LLM; incidents only create one
ledger task in the ``ops`` lane, which the existing autopilot (if its ops lane is on) or the manager may pick.
Contract for the web UI: docs/workflow/OBSERVABILITY.md.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

LAYERS = ("supervisor", "autopilot", "wrapper", "manager", "core", "bridge")
THRESHOLDS = {"supervisor": 180, "autopilot": 900, "wrapper": 180, "manager": 1800, "core": 180, "bridge": 180}
BEAT, FLOW = "layer_beat", "task_flow"
BEATS_KEY, DECISION_KEY, INCIDENT_KEY = "layer_beats", "autopilot_decision", "incident_state"
EVENT_EVERY_S = 60                 # a beat becomes a ledger event on state change or at most once a minute
DECISION_EVERY_S = 1800            # same "nothing started" reason is logged again after 30 min
INCIDENT_COOLDOWN_S = 4 * 3600
WRAPPER_DEAD_S = 300
IDLE_WORK_S = 1200
PAUSED_LONG_S = 1800
ERROR_BURST = (5, 1800)
LOG_NAME, LOG_MAX = "supervisor.log", 5 * 1024 * 1024
MARK_KINDS = ("supervisor_status", "supervisor_error", "supervisor", "task_transition", "task_claimed", "task_quarantined",
              "incident_opened", "autopilot_error", "autopilot_paused", "autopilot_stop", "autopilot_idle", "manager_continue")
ERROR_KINDS = ("supervisor_error", "autopilot_error")


def _meta(store, key, default):
    with store._tx() as db:
        row = db.execute("SELECT value FROM workflow_meta WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def _save(store, key, value) -> None:
    with store._tx() as db:
        store._set_meta(db, key, value)


def _is_incident_task(task: dict) -> bool:
    return task.get("lane") == "ops" and str(task.get("title", "")).startswith("Incident triage:")


def ready_count(store, db) -> int:
    """Queued, authorised, dependency-free tasks (the incident tasks themselves do not count as factory work)."""
    return sum(1 for t in store._all(db, "task") if t["state"] == "queued" and t.get("execution_allowed", True)
               and not _is_incident_task(t) and store._ready(db, t))


# ---- beats ----------------------------------------------------------------------------------------------------
def beat(store, layer: str, state: str = "ok", note: str = "") -> dict:
    if layer not in LAYERS:
        raise ValueError("invalid layer")
    state = (state or "ok").strip()[:40] or "ok"
    note = (note or "").strip()[:200]
    if "\x00" in state + note:
        raise ValueError("invalid beat text")
    with store._tx() as db:
        row = db.execute("SELECT value FROM workflow_meta WHERE key=?", (BEATS_KEY,)).fetchone()
        beats = json.loads(row[0]) if row else {}
        now, prev = store.clock(), beats.get(layer, {})
        entry = {"ts": now, "state": state, "note": note, "ev_ts": prev.get("ev_ts", 0)}
        if prev.get("state") != state or now - entry["ev_ts"] >= EVENT_EVERY_S:
            store._event(db, BEAT, json.dumps({"layer": layer, "state": state, "note": note,
                                               "ready": ready_count(store, db)}, ensure_ascii=False))
            entry["ev_ts"] = now
        beats[layer] = entry
        store._set_meta(db, BEATS_KEY, beats)
    return {"layer": layer, "state": state, "at": now}


def _status(name: str, entry: dict | None, now: float) -> tuple[str, float | None]:
    if not entry or not entry.get("ts"):
        return "dead", None
    age = max(0.0, now - entry["ts"])
    if entry.get("state") in {"stopped", "dead"}:
        return "dead", age
    limit = THRESHOLDS[name]
    return ("ok" if age <= limit else "stale" if age <= 2 * limit else "dead"), age


def layers(store, now: float | None = None) -> list[dict]:
    now = store.clock() if now is None else now
    with store._tx() as db:
        row = db.execute("SELECT value FROM workflow_meta WHERE key=?", (BEATS_KEY,)).fetchone()
        beats = json.loads(row[0]) if row else {}
        sup = store._meta(db, "supervisor")
        native = [a.get("heartbeat_at", 0) for a in store._all(db, "agent") if a.get("provider") == "claude-native"]
    if sup.get("heartbeat_at", 0) > beats.get("supervisor", {}).get("ts", 0):
        beats["supervisor"] = {"ts": sup["heartbeat_at"], "state": sup.get("status", "running"), "note": ""}
    if native and max(native) > 0:
        beats["manager"] = {"ts": max(native), "state": "active", "note": "last hook activity"}
    out = []
    for name in LAYERS:
        entry = beats.get(name)
        status, age = _status(name, entry, now)
        out.append({"layer": name, "status": status, "state": (entry or {}).get("state", "never"),
                    "last_beat_at": (entry or {}).get("ts"), "age_s": None if age is None else round(age, 1),
                    "note": (entry or {}).get("note", "")})
    return out


# ---- timeline -------------------------------------------------------------------------------------------------
def _parse(row) -> dict | None:
    try:
        data = json.loads(row["summary"])
        return data if isinstance(data, dict) else None
    except (ValueError, TypeError):
        return None


def _beat_rows(events) -> dict[str, list[tuple]]:
    """layer -> [(ts, state, ready)] from layer_beat events and supervisor_status changes."""
    per: dict[str, list[tuple]] = {name: [] for name in LAYERS}
    for row in events:
        if row["kind"] == BEAT and (data := _parse(row)) and data.get("layer") in per:
            per[data["layer"]].append((row["ts"], str(data.get("state", "ok")), int(data.get("ready") or 0)))
        elif row["kind"] == "supervisor_status":
            per["supervisor"].append((row["ts"], row["summary"].removeprefix("Supervisor ").strip() or "running", 0))
    for rows in per.values():
        rows.sort(key=lambda r: r[0])
    return per


def _segments_and_gaps(layer: str, rows: list[tuple], now: float, until: float, ready_now: int) -> tuple[list, list]:
    limit = THRESHOLDS[layer]
    segs: list[dict] = []
    gaps: list[dict] = []
    for ts, state, ready in rows:
        last = segs[-1] if segs else None
        if last and ts - last["_last"] <= limit:
            if last["state"] == state:
                last["_last"] = last["end"] = ts
                continue
            last["end"] = ts
        elif last:
            gaps.append({"layer": layer, "start": last["_last"], "end": ts, "seconds": round(ts - last["_last"], 1),
                         "ready_work": max(last["_ready"], ready)})
        segs.append({"layer": layer, "state": state, "start": ts, "end": ts, "_last": ts, "_ready": ready})
    if segs:
        last = segs[-1]
        if until - last["_last"] <= limit:
            last["end"] = until
        elif until >= now - 1:      # still silent right now
            gaps.append({"layer": layer, "start": last["_last"], "end": until, "seconds": round(until - last["_last"], 1),
                         "ready_work": max(last["_ready"], ready_now)})
    for seg in segs:
        seg.pop("_last"), seg.pop("_ready")
    return segs, gaps


def _layer_of(kind: str) -> str:
    for prefix in ("supervisor", "autopilot", "manager"):
        if kind.startswith(prefix):
            return prefix
    return "ledger"


def timeline(store, since: float | None = None, until: float | None = None, now: float | None = None) -> dict:
    now = store.clock() if now is None else now
    until = now if until is None else until
    since = until - 86400 if since is None else since
    with store._tx() as db:
        events = [dict(r) for r in db.execute("SELECT ts,kind,summary FROM workflow_events WHERE ts>=? AND ts<=? ORDER BY id",
                                              (since, until))]
        ready_now = ready_count(store, db)
        tasks = {t["id"]: t for t in store._all(db, "task")}
    segments, gaps = [], []
    for layer, rows in _beat_rows(events).items():
        segs, g = _segments_and_gaps(layer, rows, now, until, ready_now)
        segments += segs
        gaps += g
    marks = [{"ts": e["ts"], "kind": e["kind"], "layer": _layer_of(e["kind"]), "summary": e["summary"]}
             for e in events if e["kind"] in MARK_KINDS or e["kind"].startswith(("autopilot_", "manager_"))][-500:]
    pipeline = []
    for e in events:
        data = _parse(e) if e["kind"] == FLOW else None
        if data:
            task = tasks.get(data.get("task_id"), {})
            pipeline.append({"ts": e["ts"], "task_id": data.get("task_id"), "title": data.get("title") or task.get("title", ""),
                             "lane": data.get("lane", ""), "from_state": data.get("from"), "to_state": data.get("to"),
                             "agent": data.get("agent")})
    return {"since": since, "until": until, "segments": sorted(segments, key=lambda s: (s["start"], s["layer"])),
            "marks": marks, "gaps": sorted(gaps, key=lambda g: g["start"]), "pipeline": pipeline[-500:]}


def _hm(ts: float) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(ts))


def _dur(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m" if seconds >= 3600 else f"{seconds // 60}m{seconds % 60:02d}s"


def timeline_text(tl: dict, rows: list[dict] | None = None, limit: int = 60) -> str:
    lines = [f"Timeline {_hm(tl['since'])} -> {_hm(tl['until'])} (local time)"]
    for row in rows or []:
        age = "never" if row["age_s"] is None else _dur(row["age_s"]) + " ago"
        lines.append(f"  {row['layer']:<10} {row['status']:<5} {row['state']:<10} last beat {age} {row['note']}".rstrip())
    lines.append(f"Gaps ({len(tl['gaps'])}):")
    lines += [f"  {g['layer']:<10} {_hm(g['start'])} -> {_hm(g['end'])} ({_dur(g['seconds'])}) ready work: {g['ready_work']}"
              for g in tl["gaps"]] or ["  none"]
    lines.append(f"Events (last {limit}):")
    lines += [f"  {_hm(m['ts'])} {m['layer']:<10} {m['kind']}: {m['summary'][:140]}" for m in tl["marks"][-limit:]] or ["  none"]
    lines.append(f"Pipeline (last {min(limit, len(tl['pipeline']))}):")
    lines += [f"  {_hm(p['ts'])} {p['task_id']} {p['from_state']}->{p['to_state']} ({p['lane']}) {p['agent'] or ''}"
              for p in tl["pipeline"][-limit:]] or ["  none"]
    return "\n".join(lines)


# ---- autopilot "nothing started" decisions ----------------------------------------------------------------------
def record_decision(store, policy: dict, actions: list[dict]) -> str:
    """Log why a tick started nothing; only on reason change or every 30 min. Returns the reason ('' when work started)."""
    if any(a.get("action") == "started" for a in actions):
        reason = ""
    else:
        reason = _idle_reason(store, policy, actions)
    if not reason:
        _save(store, DECISION_KEY, {"reason": "", "since": store.clock(), "logged_at": store.clock()})
        return ""
    now, prev = store.clock(), _meta(store, DECISION_KEY, {})
    changed = prev.get("reason") != reason
    state = {"reason": reason, "since": now if changed else prev.get("since", now),
             "logged_at": now if changed or now - prev.get("logged_at", 0) >= DECISION_EVERY_S else prev.get("logged_at", now)}
    if state["logged_at"] == now:
        store.record_event("autopilot_idle", f"Nothing started: {reason}")
    _save(store, DECISION_KEY, state)
    return reason


def _idle_reason(store, policy: dict, actions: list[dict]) -> str:
    from . import autopilot_loop as loop
    if not policy.get("enabled") or policy.get("kill"):
        return "POLICY_OFF"
    state = loop._load(store)
    if store.clock() < state.get("paused_until", 0):
        return "NO_CORE" if state.get("pause_reason") == "NO_CORE" else "PAUSED"
    if any(a.get("action") == "claim_failed" for a in actions):
        return "CLAIM_FAILED"
    snap = store.snapshot()
    running = [a for a in snap["agents"] if a.get("autopilot") and a.get("task_id")]
    starts = [t for t in state.get("starts", []) if store.clock() - t < 3600]
    open_lanes = [lane for lane, conf in policy.get("lanes", {}).items()
                  if isinstance(conf, dict) and int(conf.get("workers", 0)) > 0]
    if any(loop._pick(snap, policy, lane) for lane in open_lanes):
        return "LANE_FULL"
    return "NO_READY_TASK"


# ---- incidents --------------------------------------------------------------------------------------------------
def _last_activity(store, db) -> float:
    agents = max([max(a.get("heartbeat_at", 0), a.get("progress_at", 0)) for a in store._all(db, "agent")] or [0])
    row = db.execute("SELECT MAX(ts) FROM workflow_events WHERE kind IN ('task_claimed','task_transition','manager_continue')").fetchone()
    return max(agents, row[0] or 0)


def detect_incidents(store, now: float | None = None) -> list[dict]:
    now = store.clock() if now is None else now
    found: list[dict] = []
    rows = {r["layer"]: r for r in layers(store, now)}

    def add(key, kind, since, summary):
        found.append({"key": key, "kind": kind, "since": since, "summary": summary, "task_id": None})

    sup = rows["supervisor"]
    if sup["last_beat_at"] and sup["age_s"] > 300:
        add("supervisor_dead", "supervisor_dead", sup["last_beat_at"], f"Supervisor silent for {_dur(sup['age_s'])}")
    wrapper = rows["wrapper"]
    if wrapper["last_beat_at"] and wrapper["state"] != "stopped" and wrapper["age_s"] > WRAPPER_DEAD_S:
        add("wrapper_dead", "wrapper_dead", wrapper["last_beat_at"], f"Wrapper silent for {_dur(wrapper['age_s'])}")
    from . import autopilot
    policy, _ = autopilot.load(store.path.parent)
    with store._tx() as db:
        last = _last_activity(store, db)
        errors = {k: db.execute("SELECT COUNT(*) FROM workflow_events WHERE kind=? AND ts>=?", (k, now - ERROR_BURST[1])).fetchone()[0]
                  for k in ERROR_KINDS}
        queued = [t for t in store._all(db, "task") if t["state"] == "queued" and t.get("execution_allowed", True)
                  and not _is_incident_task(t) and store._ready(db, t)]
    if policy.get("enabled") and not policy.get("kill") and queued and now - last > IDLE_WORK_S:
        add("factory_idle_with_ready_work", "factory_idle_with_ready_work", last,
            f"{len(queued)} authorised task(s) ready, no worker or manager activity for {_dur(now - last)}")
    decision = _meta(store, DECISION_KEY, {})
    if decision.get("reason") in {"PAUSED", "NO_CORE"} and now - decision.get("since", now) > PAUSED_LONG_S:
        add("autopilot_paused_long", "autopilot_paused_long", decision["since"],
            f"Autopilot paused ({decision['reason']}) for {_dur(now - decision['since'])}")
    for kind, count in errors.items():
        if count >= ERROR_BURST[0]:
            add(f"repeated_{kind}", "repeated_error", now - ERROR_BURST[1], f"{count} {kind} events in 30 minutes")
    return found


def open_incident(store, incident: dict, now: float | None = None) -> str:
    """One ops ledger task with the last 2 h of timeline; the supervisor itself runs no LLM."""
    now = store.clock() if now is None else now
    excerpt = timeline_text(timeline(store, now - 7200, now, now), layers(store, now), limit=40)
    key = re.sub(r"[^A-Za-z0-9_.:-]", "-", incident["key"])
    task = store.create_task({
        "id": f"incident-{key}-{int(now)}"[:120], "title": f"Incident triage: {incident['key']}", "lane": "ops",
        "priority": 1, "version": "ops", "required_validation": "automated",
        "acceptance": [
            {"label": "Read workflow_console.py timeline and health first. If it is a real defect, search `gh issue list --label "
                      "showstopper` before anything else, then open ONE issue with `gh issue create --label showstopper,P1` "
                      "including the timeline excerpt."},
            {"label": "Never merge, never restart processes by name or pattern. Record the evidence (issue number or why none "
                      "is needed) and finish."}],
        "scope_note": f"{incident['summary']}\n\n{excerpt}"[:12000]})
    store.record_event("incident_opened", f"{incident['key']}: {incident['summary']}"[:300])
    from .autopilot_watch import raise_alert
    raise_alert(store, "incident", f"{incident['key']}: {incident['summary']}", task_id=task["id"])
    return task["id"]


def process_incidents(store, now: float | None = None) -> list[dict]:
    """Open an ops task for each NEW incident (dedupe by key, 4 h cooldown). Returns the incidents opened."""
    now = store.clock() if now is None else now
    state, opened = _meta(store, INCIDENT_KEY, {}), []
    for incident in detect_incidents(store, now):
        known = state.get(incident["key"])
        if known and now - known["opened_at"] < INCIDENT_COOLDOWN_S:
            continue
        incident["task_id"] = open_incident(store, incident, now)
        state[incident["key"]] = {"opened_at": now, "task_id": incident["task_id"]}
        opened.append(incident)
    if opened:
        _save(store, INCIDENT_KEY, state)
    return opened


def health(store, now: float | None = None) -> dict:
    now = store.clock() if now is None else now
    state = _meta(store, INCIDENT_KEY, {})
    incidents = [{**i, "task_id": state.get(i["key"], {}).get("task_id")} for i in detect_incidents(store, now)]
    return {"layers": layers(store, now), "incidents": incidents, "now": now}


# ---- supervisor wiring ----------------------------------------------------------------------------------------
def core_probe(profile_home: Path) -> tuple[bool, bool, str]:
    try:
        from ..orchestrator.bridge import try_request
        reply = try_request(Path(profile_home), "ping", timeout=3)
    except Exception as exc:  # noqa: BLE001
        return False, False, f"probe failed: {type(exc).__name__}"
    if reply is None:
        return False, False, "NO_CORE"
    return True, bool(reply.get("ok", True)), ""


def _autopilot_state(store, home: Path) -> tuple[str, str]:
    from . import autopilot
    policy, error = autopilot.load(home)
    if error or not policy.get("enabled"):
        return "off", error[:120]
    decision = _meta(store, DECISION_KEY, {})
    with store._tx() as db:
        recent = db.execute("SELECT COUNT(*) FROM workflow_events WHERE kind='autopilot_error' AND ts>=?",
                            (store.clock() - 120,)).fetchone()[0]
    if recent:
        return "error", "autopilot_error in the last 2 min"
    reason = decision.get("reason", "")
    return ("paused" if reason in {"PAUSED", "NO_CORE"} else "ok"), reason


def supervisor_tick(store, home: Path, profile_home: Path, *, probe=core_probe) -> list[dict]:
    """Cheap per-tick bookkeeping: beats, wrapper watch, incidents. Never raises into the supervisor loop."""
    try:
        beat(store, "supervisor", "ok")
        beat(store, "autopilot", *_autopilot_state(store, home))
        core = _meta(store, BEATS_KEY, {}).get("core", {})
        if store.clock() - core.get("ts", 0) >= 60:
            core_ok, bridge_ok, note = probe(profile_home)
            beat(store, "core", "ok" if core_ok else "down", note)
            beat(store, "bridge", "ok" if bridge_ok else "down", note)
        wrapper = next(r for r in layers(store) if r["layer"] == "wrapper")
        if wrapper["last_beat_at"] and wrapper["state"] != "stopped" and wrapper["age_s"] > WRAPPER_DEAD_S:
            from .autopilot_watch import raise_alert
            raise_alert(store, "watcher_dead:wrapper", f"Wrapper beat is stale ({_dur(wrapper['age_s'])})")
        return process_incidents(store)
    except Exception as exc:  # noqa: BLE001
        log_line(home, f"observability tick failed: {type(exc).__name__}: {str(exc)[:200]}")
        return []


# ---- supervisor.log ---------------------------------------------------------------------------------------------
def log_path(home: Path) -> Path:
    return Path(home) / LOG_NAME


def log_line(home: Path, text: str, *, stamp: bool = True) -> None:
    """Append one timestamped line; rotate to supervisor.log.1 at 5 MB. Logging must never break the supervisor."""
    try:
        path = log_path(home)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size >= LOG_MAX:
            path.replace(path.with_name(LOG_NAME + ".1"))
        prefix = time.strftime("%Y-%m-%d %H:%M:%S ", time.localtime()) if stamp else ""
        with path.open("a", encoding="utf-8", errors="replace") as f:
            f.write(prefix + text.rstrip("\n") + "\n")
    except OSError:
        pass


class _StderrTee:
    """Replaces sys.stderr for the supervisor: every line also goes, timestamped, to supervisor.log."""

    def __init__(self, home: Path, original):
        self.home, self.original = home, original

    def write(self, data):
        for line in str(data).splitlines():
            if line.strip():
                log_line(self.home, "stderr: " + line)
        try:
            return self.original.write(data)
        except (OSError, ValueError, AttributeError):
            return len(data)

    def flush(self):
        try:
            self.original.flush()
        except (OSError, ValueError, AttributeError):
            pass

    def __getattr__(self, name):
        return getattr(self.original, name)


def capture_stderr(home: Path) -> None:
    if not isinstance(sys.stderr, _StderrTee):
        sys.stderr = _StderrTee(Path(home), sys.stderr)


def on_start(store, home: Path, reason: str = "supervise") -> None:
    """Record why this supervisor started and, if the previous one never stopped cleanly, how long it was silent."""
    import os
    previous = _meta(store, "supervisor", {})
    note = f"start pid={os.getpid()} reason={reason}"
    prev_hb = previous.get("heartbeat_at", 0)
    if previous.get("status") not in {None, "stopped", "offline"} and prev_hb:
        note += f"; previous supervisor ended without a clean stop (status={previous.get('status')}, silent {_dur(store.clock() - prev_hb)})"
    elif previous.get("status") == "stopped":
        note += "; previous supervisor stopped cleanly"
    log_line(home, "supervisor " + note)
    store.record_event("supervisor", ("Supervisor " + note)[:300])
    if prev_hb and store.clock() - prev_hb > 300 and previous.get("status") != "stopped":
        outage = {"key": f"supervisor_dead:{int(prev_hb)}", "kind": "supervisor_dead", "since": prev_hb,
                  "summary": f"Supervisor was silent for {_dur(store.clock() - prev_hb)} before this restart", "task_id": None}
        state = _meta(store, INCIDENT_KEY, {})
        if not any(k.startswith("supervisor_dead") and store.clock() - v["opened_at"] < INCIDENT_COOLDOWN_S for k, v in state.items()):
            state[outage["key"]] = {"opened_at": store.clock(), "task_id": open_incident(store, outage)}
            _save(store, INCIDENT_KEY, state)


def on_stop(home: Path, cause: str, store=None) -> None:
    log_line(home, f"supervisor stop: {cause}")
    if store is not None:
        try:
            store.record_event("supervisor", f"Supervisor stopped: {cause}"[:300])
        except Exception:  # noqa: BLE001
            pass
        try:
            beat(store, "supervisor", "stopped", cause[:100])
        except Exception:  # noqa: BLE001
            pass
