"""The orchestrator service: registry + inbox + adapters + policy, one per profile home.

The Core runs one (`Orchestrator(...).start()`: the bridge listens, the spool is replayed, sessions of the last run
are shown as last known). The CLI and a dashboard in another process reach it through the bridge
(`call_remote`), and fall back to a local, read-only-ish instance (`Orchestrator(..., serve=False)`) that answers
from agents.db and `claude agents --json`, and queues messages for the plugin's next safe stop.

Every operation is `call(op, **args) -> dict` (the same for CLI, voice tools, dashboard and the bridge), so all
surfaces behave the same. Rules enforced here, never by prompt text:
- the model never approves: worker approval requests go to the owner's approvals popup (typed code for the risky
  classes in policy.RISKY), decisions come back only from the owner's channels (approvals.py refuses answers from
  inside a tool);
- start / stop / resume need an on-screen approval (OD-23) unless the owner already confirmed on the CLI;
- read-only status is automatic and answered from snapshots first (a busy worker is not interrupted);
- one writer per folder; allow-listed projects only; never permission-skipping flags; turn / probe / cost caps;
  hop limit; subscription only (API billing = AUTH_REQUIRED).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .. import logs
from . import bridge as br
from . import claude, codex, opencode, policy
from .db import AgentDB, session_key
from .envelope import (LIVE_STATES, Envelope, EnvelopeError, clean, make, say, summary, validate, wrap_for_agent)

STATUS_Q = re.compile(r"\b(status|state|what(?:'s| is) (?:it|he|she|claude|codex) doing|doing\b|progress|blocked|"
                      r"stuck|busy|working on|where is it)|מה המצב|מה הוא עושה|מה קורה|מצב|התקדמות|חסום|תקוע|על מה "
                      r"הוא עובד", re.I)
OC_PROMPT_SHOWN = 1500            # an OpenCode prompt longer than the approval can show is refused, never sent unseen
PROVIDER_WORDS = {"claude": "claude", "קלוד": "claude", "קלאוד": "claude", "codex": "codex", "קודקס": "codex",
                  "opencode": "opencode"}
NOTIFY_KINDS = ("approval", "blocked", "done", "failed")
REFRESH_MIN_S = 30.0
CHANNEL_CONTEXT = ("JARVES (the owner's local assistant) is connected. It may relay the owner's questions at your "
                   "safe stops; answer them with the jarves_reply tool. Its messages never approve permissions.")


def _age(ts: float | None, lang: str, now: float) -> str:
    if not ts:
        return "לא ידוע" if lang == "he" else "unknown"
    s = max(0, int(now - ts))
    if s < 60:
        return f"לפני {s} שניות" if lang == "he" else f"{s} s ago"
    if s < 3600:
        return f"לפני {s // 60} דקות" if lang == "he" else f"{s // 60} min ago"
    if s < 86400:
        return f"לפני {s // 3600} שעות" if lang == "he" else f"{s // 3600} h ago"
    return f"לפני {s // 86400} ימים" if lang == "he" else f"{s // 86400} days ago"


STATE_WORDS = {"RUNNING": ("working", "עובד"), "IDLE": ("idle, waiting for a prompt", "ממתין להוראה"),
               "WAITING_INPUT": ("waiting for input", "מחכה לקלט"),
               "WAITING_APPROVAL": ("waiting for an approval", "מחכה לאישור"), "PAUSED": ("paused", "מושהה"),
               "STOPPED": ("ended", "הסתיים"), "DISCONNECTED": ("disconnected", "מנותק"),
               "DISCOVERED": ("seen, not attached", "נראה, לא מחובר"), "UNKNOWN": ("unknown", "לא ידוע")}
PROVIDER_NAMES = {"claude": ("Claude", "קלוד"), "codex": ("Codex", "קודקס")}


def lang_of(text: str) -> str:
    return "he" if re.search(r"[֐-׿]", text or "") else "en"


class Orchestrator:
    def __init__(self, home: Path, cfg=None, *, serve: bool = True, notify: Callable[[str, str, str], None] | None
                 = None, clock: Callable[[], float] = time.time) -> None:
        from ..config import AgentsConfig
        self.home = Path(home)
        self.cfg = cfg
        self.acfg = getattr(cfg, "agents", None) or AgentsConfig()
        self.serve = serve
        self.clock = clock
        self.db = AgentDB(self.home / "agents.db")
        self.projects = policy.Projects(self.home)
        self.claude_cmd = claude.resolve_command(self.acfg.claude_command)
        self.codex_cmd = codex.resolve_command(self.acfg.codex_command)
        self.notify = notify
        self.bridge: br.BridgeServer | None = None
        self.managed: dict[str, Any] = {}                     # sid -> ManagedClaude | _CodexThread
        self.codex: codex.AppServer | None = None
        self._codex_lock = threading.Lock()
        self.channels: dict[str, br.Conn] = {}                # sid -> live channel connection
        self._cv = threading.Condition()
        self._lock = threading.RLock()
        self._last_refresh = 0.0
        self._codex_requests: dict[str, tuple[Any, str, str]] = {}   # request_id -> (rpc id, method, sid)
        self.loop: asyncio.AbstractEventLoop | None = None
        self.approvals = None                                 # the Core's ApprovalService
        self._timer: threading.Timer | None = None
        self.started = False

    # ================================================================== lifecycle
    def start(self) -> "Orchestrator":
        if self.serve:
            self.bridge = br.BridgeServer(self.home, self._handle, on_close=self._conn_closed).start()
        self._recover()
        self.started = True
        self._schedule_refresh()
        logs.info("agents", "AGENTS_STARTED", "agent orchestrator started", sessions=len(self.db.sessions()))
        return self

    def stop(self) -> None:
        self.started = False
        if self._timer is not None:
            self._timer.cancel()
        for sid, m in list(self.managed.items()):
            try:
                if isinstance(m, claude.ManagedClaude):
                    m.close(grace_s=5)
                self.db.upsert_session(sid, state="DISCONNECTED", last_seen_at=self.clock())
            except Exception:
                logs.warn("agents", "AGENTS_STOP_FAILED", "a managed session did not close cleanly")
        self.managed.clear()
        if self.codex is not None:
            self.codex.close()
            self.codex = None
        if self.bridge is not None:
            self.bridge.stop()
            self.bridge = None
        self.db.close()
        logs.info("agents", "AGENTS_STOPPED", "agent orchestrator stopped")

    def _recover(self) -> None:
        """UC6: sessions of the last run show as last known (never "running" from an old row); managed processes
        died with the old Core; the hook spool is replayed once, in order, deduplicated by message_id."""
        now = self.clock()
        for s in self.db.sessions():
            if s["origin"] == "managed" and s["state"] in LIVE_STATES and s["id"] not in self.managed:
                self.db.upsert_session(s["id"], state="DISCONNECTED")
                self.db.add_event(s["id"], "disconnected", "JARVES restarted: this managed session's process ended")
            elif s["state"] in LIVE_STATES and s.get("status_source") in ("hook", "channel", "agents_json"):
                self.db.upsert_session(s["id"], meta={**(s.get("meta") or {}), "last_known": True})
        spool = self.home / "agents" / "spool"
        n = 0
        for p in sorted(spool.glob("*.json")) if spool.is_dir() else []:
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                d.pop("_ppid", None)
                anc, ev = d.pop("_anc", None) or [], d.pop("_event", "")
                self._ingest(validate(d), ancestors=anc, event=ev, replay=True)
                n += 1
            except (OSError, ValueError, EnvelopeError):
                pass
            try:
                p.unlink()
            except OSError:
                pass
        if n:
            logs.info("agents", "AGENTS_SPOOL_REPLAYED", "hook events from while JARVES was down replayed", events=n)
        self.db.set_meta("started_at", str(now))

    def _schedule_refresh(self) -> None:
        """Event-driven: only while some session is live, one `claude agents --json` every agents.refresh_s
        (>= 30 s) keeps states honest; nothing runs when nothing is live."""
        if not self.started or not self.serve:
            return
        period = max(REFRESH_MIN_S, float(self.acfg.refresh_s))

        def tick() -> None:
            try:
                if any(s["state"] in LIVE_STATES for s in self.db.sessions()):
                    self.refresh(force=True)
            except Exception:
                logs.warn("agents", "AGENTS_REFRESH_FAILED", "agent refresh failed")
            self._schedule_refresh()
        self._timer = threading.Timer(period, tick)
        self._timer.daemon = True
        self._timer.start()

    # ================================================================== discovery
    def refresh(self, force: bool = False, codex_too: bool = False) -> dict:
        now = self.clock()
        if not force and now - self._last_refresh < REFRESH_MIN_S:
            return {"ok": True, "cached": True}
        self._last_refresh = now
        out: dict = {"ok": True, "claude": None, "codex": None}
        if self.claude_cmd:
            rows, err = claude.list_live(self.claude_cmd)
            out["claude"] = err or len(rows or [])
            if rows is not None:
                seen = set()
                for r in rows:
                    sid = session_key("claude", r["provider_session_id"])
                    seen.add(sid)
                    cur = self.db.session(sid) or {}
                    fields = {"cwd": r["cwd"] or cur.get("cwd"), "kind": cur.get("kind") or r["kind"],
                              "name": r["name"] or cur.get("name"), "pid": r["pid"],
                              "started_at": cur.get("started_at") or r["started_at"], "waiting_for": r["waiting_for"],
                              "last_seen_at": now, "status_source": cur.get("status_source") or "agents_json",
                              "origin": cur.get("origin") or "manual", "ended_at": None}
                    if sid not in self.managed:              # a managed session's own events are more precise
                        fields["state"] = "PAUSED" if cur.get("state") == "PAUSED" and r["state"] == "IDLE" \
                            else r["state"]
                    self._place(fields)
                    self.db.upsert_session(sid, **fields)
                for s in self.db.sessions():
                    if s["provider"] == "claude" and s["id"] not in seen and s["state"] in LIVE_STATES \
                            and s["id"] not in self.managed:
                        self.db.upsert_session(s["id"], state="STOPPED", ended_at=s.get("ended_at") or now)
                        self.db.add_event(s["id"], "ended", "the session is no longer running")
        if codex_too and self.codex_cmd and self.acfg.codex_observe:
            out["codex"] = self._refresh_codex()
        if codex_too and self._opencode_on():
            out["opencode"] = self._refresh_opencode()
        return out

    # ---- OpenCode (V7 D1): read-only, switch agent_opencode (off by default), sessions land in the same registry
    def _opencode_on(self) -> bool:
        from .. import features
        try:
            return features.Features(self.cfg, self.home).enabled(opencode.FEATURE)
        except Exception:
            return False

    def _opencode_client(self) -> opencode.Client:
        return opencode.Client(self.acfg.opencode_url, password_env=self.acfg.opencode_password_env)

    def _refresh_opencode(self) -> int | str:
        try:
            rows = self._opencode_client().sessions()
        except opencode.OpenCodeError as e:
            return e.code
        now = self.clock()
        for v in rows:
            sid = session_key("opencode", v["provider_session_id"])
            cur = self.db.session(sid) or {}
            if cur.get("origin") == "managed" and cur.get("state") == "STOPPED":
                continue                                 # D2: a session JARVES stopped is not shown as live again
            if sid in self.managed:                      # D2: a session JARVES started keeps its managed row; only liveness
                self.db.upsert_session(sid, state=v["state"], last_seen_at=now)
                continue
            fields = {"cwd": v["cwd"], "name": v["name"] or cur.get("name"), "kind": v["kind"], "state": v["state"],
                      "started_at": v["started_at"], "last_seen_at": now, "origin": cur.get("origin") or "manual",
                      "last_progress_at": v["updated_at"] or cur.get("last_progress_at"), "attachable": 0,
                      "auth": "password", "status_source": "opencode_http"}
            self._place(fields)
            self.db.upsert_session(sid, **fields)
        return len(rows)

    def opencode_op(self, action: str = "status", limit: int = 20, lang: str = "en") -> dict:
        """`jarvis agents opencode status|sessions|events`: three GET calls, nothing else."""
        if not self._opencode_on():
            return {"ok": False, "code": "error", "text": f"{opencode.FEATURE} is off: jarvis features on {opencode.FEATURE}"}
        try:
            c = self._opencode_client()
            if action == "events":
                return {"ok": True, "events": c.events(max(1, min(int(limit), 100)), timeout=5.0)}
            if action == "sessions":
                return {"ok": True, "sessions": c.sessions()}
            return {"ok": True, "health": c.health(), "model": self.opencode_profile()}
        except opencode.OpenCodeError as e:
            return {"ok": False, "code": e.code, "text": say(e.code, lang), "detail": e.detail}

    def _place(self, fields: dict) -> None:
        proj = self.projects.for_path(fields.get("cwd"))
        fields["project"] = proj.name if proj else ""
        fields["project_root"] = proj.path if proj else ""

    def _codex_server(self) -> codex.AppServer:
        with self._codex_lock:
            if self.codex is not None and self.codex.alive():
                return self.codex
            if not self.codex_cmd:
                raise LookupError("PROVIDER_MISSING")
            proxy = codex.daemon_running(self.codex_cmd) if not self.acfg.codex_command else False
            srv = codex.AppServer(self.codex_cmd, proxy=proxy, on_notification=self._codex_event,
                                  on_server_request=self._codex_request, on_exit=self._codex_exit)
            srv.start()
            srv.read_account()
            self.codex = srv
            logs.info("agents", "AGENTS_CODEX_CONNECTED", "codex app-server connected",
                      transport="daemon" if proxy else "stdio", account=srv.account.get("type"))
            return srv

    def _refresh_codex(self) -> int | str:
        try:
            srv = self._codex_server()
            threads = srv.list_threads(limit=25)
            loaded = set(srv.loaded())
        except (LookupError, codex.RpcError, OSError) as e:
            return str(e)[:80]
        now = self.clock()
        for t in threads:
            v = codex.thread_view(t)
            sid = session_key("codex", v["provider_session_id"])
            cur = self.db.session(sid) or {}
            attach = v["provider_session_id"] in loaded
            fields = {"cwd": v["cwd"], "name": v["name"] or cur.get("name"), "kind": v["kind"],
                      "branch": v["branch"], "started_at": v["started_at"], "last_seen_at": now,
                      "last_progress_at": v["updated_at"] or cur.get("last_progress_at"),
                      "origin": cur.get("origin") or "manual", "attachable": 1 if (attach or sid in self.managed)
                      else 0, "auth": "subscription" if srv.account.get("type") == "chatgpt" else
                      (srv.account.get("type") or "unknown"), "status_source": cur.get("status_source") or "app_server"}
            if sid not in self.managed:
                fields["state"] = v["state"] if attach else ("DISCOVERED" if v["state"] == "DISCOVERED" else v["state"])
            self._place(fields)
            self.db.upsert_session(sid, **fields)
        return len(threads)

    # ================================================================== views
    def _visible(self, s: dict) -> bool:
        return bool(s.get("project"))

    def view(self, s: dict, lang: str = "en") -> dict:
        now = self.clock()
        managed = s["id"] in self.managed
        ch = s["id"] in self.channels and bool(self.channels[s["id"]].meta.get("channel_live"))
        caps = ["observe"]
        if managed or ch:
            caps += ["ask", "steer"]
        elif s.get("plugin"):
            caps += ["ask_at_stop", "tell_at_stop"]
        if managed:
            caps += ["interrupt", "stop"]
        if s["provider"] == "claude" and s.get("plugin"):
            caps.append("approve")
        if s["provider"] == "codex" and managed:
            caps.append("approve")
        if s["provider"] != "opencode" and (s["state"] in ("STOPPED", "DISCONNECTED")
                                            or (s["provider"] == "codex" and not managed)):
            caps.append("resume")
        attach = "managed" if managed else ("live_channel" if ch else ("plugin" if s.get("plugin") else
                                                                          ("observe_only" if s["provider"] != "codex"
                                                                           else "UNSUPPORTED_ATTACH")))
        last = s.get("last_progress_at") or s.get("last_seen_at")
        return {"id": s["id"], "provider": s["provider"], "session": s["provider_session_id"],
                "requested_model": (s.get("meta") or {}).get("requested_model") or "",
                "project": s.get("project") or "", "cwd": s.get("cwd") or "", "branch": s.get("branch") or "",
                "name": s.get("name") or "", "origin": s.get("origin"), "state": s["state"],
                "state_text": STATE_WORDS.get(s["state"], ("?", "?"))[1 if lang == "he" else 0],
                "waiting_for": s.get("waiting_for") or "", "attach": attach, "capabilities": caps,
                "last_progress_at": last, "age": _age(last, lang, now),
                "last_known": bool((s.get("meta") or {}).get("last_known")) and not managed,
                "untrusted_summary": s.get("last_summary") or "", "untrusted_activity": s.get("last_activity") or "",
                "files": (s.get("files") or [])[-5:], "blocker": s.get("blocker") or "",
                "next_step": s.get("next_step") or "", "cost_usd": round(float(s.get("cost_usd") or 0), 4),
                "tokens": int(s.get("tokens_total") or 0), "auth": s.get("auth") or "unknown",
                "plugin": bool(s.get("plugin")), "channel": ch, "status_source": s.get("status_source") or "",
                "started_at": s.get("started_at"), "ended_at": s.get("ended_at")}

    def list(self, *, refresh: bool = True, project: str = "", include_stopped: bool = False,
             lang: str = "en", codex_too: bool = True, force: bool = False) -> dict:
        if refresh:                                   # the dashboard polls: at most one real refresh per 30 s
            self.refresh(force=force, codex_too=codex_too)
        rows = [s for s in self.db.sessions()]
        visible = [s for s in rows if self._visible(s)]
        hidden = len([s for s in rows if not self._visible(s) and s["state"] in LIVE_STATES and s["state"] != "DISCOVERED"])
        if not include_stopped:
            visible = [s for s in visible if s["state"] not in ("STOPPED",) or s["id"] in self.managed]
        if project:
            ps = {p.name for p in self.projects.by_word(project)} or {project}
            visible = [s for s in visible if s.get("project") in ps]
        return {"ok": True, "sessions": [self.view(s, lang) for s in visible], "hidden_outside_allowlist": hidden,
                "projects": [p.name for p in self.projects.load()], "at": self.clock()}

    # ================================================================== resolution
    def resolve(self, ref: str, *, live_only: bool = False) -> tuple[dict | None, str, list[dict]]:
        """(session, code, candidates). ref = registry id, provider session id (prefix >= 6), name, project name /
        alias, provider word ("claude" / "קלוד"), or "provider project" ("codex jarvis")."""
        ref = (ref or "").strip()
        rows = [s for s in self.db.sessions() if self._visible(s)]
        if live_only:
            rows = [s for s in rows if s["state"] in LIVE_STATES]
        if not ref:
            live = [s for s in rows if s["state"] in LIVE_STATES]
            return (live[0], "ok", live) if len(live) == 1 else (None, "AMBIGUOUS_SESSION" if live else
                                                                 "NO_SUCH_SESSION", live)
        exact = [s for s in rows if s["id"] == ref or s["provider_session_id"] == ref]
        if exact:
            return exact[0], "ok", exact
        if len(ref) >= 6:
            pre = [s for s in rows if s["provider_session_id"].startswith(ref) or s["id"].startswith(ref)]
            if len(pre) == 1:
                return pre[0], "ok", pre
        words = [w for w in re.split(r"\s+", ref.lower().replace("בפרויקט", " ").replace("project", " ")) if w]
        prov = next((PROVIDER_WORDS[w] for w in words if w in PROVIDER_WORDS), "")
        rest = " ".join(w for w in words if w not in PROVIDER_WORDS)
        cand = [s for s in rows if not prov or s["provider"] == prov]
        if rest:
            projs = {p.name for p in self.projects.by_word(rest)}
            cand = [s for s in cand if s.get("project") in projs or (s.get("name") or "").lower() == rest
                    or rest in (s.get("name") or "").lower()]
        live = [s for s in cand if s["state"] in LIVE_STATES]
        pool = live or cand
        if len(pool) == 1:
            return pool[0], "ok", pool
        return None, ("AMBIGUOUS_SESSION" if pool else "NO_SUCH_SESSION"), pool

    def _resolved(self, ref: str, lang: str, live_only: bool = False) -> tuple[dict | None, dict | None]:
        s, code, cands = self.resolve(ref, live_only=live_only)
        if s is None:
            return None, {"ok": False, "code": code, "text": say(code, lang),
                          "candidates": [self.view(c, lang) for c in cands[:6]]}
        return s, None

    # ================================================================== status / ask
    def status_text(self, s: dict, lang: str = "en") -> str:
        v = self.view(s, lang)
        pn = PROVIDER_NAMES.get(s["provider"], (s["provider"], s["provider"]))[1 if lang == "he" else 0]
        proj = v["project"] or Path(v["cwd"]).name
        parts = []
        if lang == "he":
            parts.append(f"{pn} בפרויקט {proj}: {v['state_text']}")
            if v["last_known"] or v["state"] in ("STOPPED", "DISCONNECTED"):
                parts.append(f"(מצב אחרון ידוע, {v['age']})")
            else:
                parts.append(f"(עדכון אחרון {v['age']})")
            if v["untrusted_summary"]:
                parts.append(f"לפי הדיווח שלו: {summary(v['untrusted_summary'], 220)}")
            if v["files"]:
                parts.append("קבצים: " + ", ".join(Path(f).name for f in v["files"][-3:]))
            if v["blocker"] or v["waiting_for"]:
                parts.append(f"חוסם: {v['blocker'] or v['waiting_for']}")
            if v["next_step"]:
                parts.append(f"הצעד הבא: {v['next_step']}")
        else:
            parts.append(f"{pn} in {proj}: {v['state_text']}")
            parts.append(f"(last known, {v['age']})" if v["last_known"] or v["state"] in ("STOPPED", "DISCONNECTED")
                         else f"(updated {v['age']})")
            if v["untrusted_summary"]:
                parts.append(f"Its own report: {summary(v['untrusted_summary'], 220)}")
            if v["files"]:
                parts.append("Files: " + ", ".join(Path(f).name for f in v["files"][-3:]))
            if v["blocker"] or v["waiting_for"]:
                parts.append(f"Blocker: {v['blocker'] or v['waiting_for']}")
            if v["next_step"]:
                parts.append(f"Next: {v['next_step']}")
        return " ".join(parts[:2]) + (". " + ". ".join(p.rstrip(".") for p in parts[2:]) if parts[2:] else "") + "."

    def status(self, ref: str = "", lang: str = "en") -> dict:
        self.refresh()
        s, err = self._resolved(ref, lang)
        if err:
            return err
        v = self.view(s, lang)
        code = "WAITING_FOR_APPROVAL" if s["state"] == "WAITING_APPROVAL" else \
            ("SESSION_CLOSED" if s["state"] == "STOPPED" else "ok")
        return {"ok": True, "code": code, "source": "snapshot", "session": v,
                "text": self.status_text(s, lang), "timeline": self.db.events(s["id"], 8)}

    def ask(self, ref: str, question: str, *, live: bool | None = None, wait_s: float = 0.0, by: str = "owner",
            lang: str | None = None) -> dict:
        lang = lang or lang_of(question)
        self.refresh()
        s, err = self._resolved(ref, lang)
        if err:
            return err
        snap = self.status_text(s, lang)
        if live is None:
            live = not STATUS_Q.search(question or "")
        if not live:
            self.db.audit(by, "ask_snapshot", session_id=s["id"], result="snapshot")
            return {"ok": True, "code": "ok", "source": "snapshot", "text": snap, "session": self.view(s, lang)}
        if s["state"] == "STOPPED":
            return {"ok": True, "code": "SESSION_CLOSED", "source": "snapshot",
                    "text": say("SESSION_CLOSED", lang) + " " + snap, "session": self.view(s, lang)}
        if self.db.count("ask_probe") >= self.acfg.max_ask_probes_per_day or \
                self.db.count("turn") >= self.acfg.max_turns_per_day:
            return {"ok": True, "code": "BUDGET_REACHED", "source": "snapshot",
                    "text": say("BUDGET_REACHED", lang) + " " + snap, "session": self.view(s, lang)}
        cid = uuid.uuid4().hex[:16]
        r = self._deliver(s, "status_request", question, by=by, correlation_id=cid, lang=lang)
        if not r["ok"]:
            return {**r, "source": "snapshot", "text": r["text"] + " " + snap}
        self.db.bump("ask_probe")
        out = {"ok": True, "code": r["code"], "source": "snapshot", "delivery": r["delivery"], "correlation_id": cid,
               "text": f"{r['text']} {snap}", "session": self.view(s, lang)}
        if wait_s > 0 and r["code"] == "SENT":
            rep = self.wait_reply(cid, wait_s)
            if rep:
                out.update(source="live", reply=rep, text=self._reply_text(s, rep, lang))
        return out

    def _reply_text(self, s: dict, reply: dict, lang: str) -> str:
        pn = PROVIDER_NAMES.get(s["provider"], (s["provider"],) * 2)[1 if lang == "he" else 0]
        body = summary(reply.get("text") or "", 400)
        return (f"{pn} ענה: {body}" if lang == "he" else f"{pn} replied: {body}")

    def wait_reply(self, correlation_id: str, wait_s: float) -> dict | None:
        deadline = time.monotonic() + max(0.0, wait_s)
        with self._cv:
            while True:
                rows = self.db.replies(correlation_id)
                if rows:
                    return {"text": (rows[-1]["payload"] or {}).get("reply") or (rows[-1]["payload"] or {}).get("text")
                            or "", "at": rows[-1]["created"]}
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                self._cv.wait(min(left, 1.0))

    def reply(self, correlation_id: str) -> dict:
        r = self.wait_reply(correlation_id, 0)
        return {"ok": True, "reply": r, "code": "ok" if r else "QUEUED"}

    # ================================================================== tell / continue / pause / stop
    def _cap(self, lang: str) -> dict | None:
        if self.db.count("turn") >= self.acfg.max_turns_per_day:
            return {"ok": False, "code": "BUDGET_REACHED", "text": say("BUDGET_REACHED", lang)}
        if self._cost_today() >= self.acfg.max_cost_usd_per_day > 0:
            return {"ok": False, "code": "BUDGET_REACHED", "text": say("BUDGET_REACHED", lang)}
        return None

    def _budget_left(self) -> float:
        """What is left of today's cost cap (0 = no cap); passed to claude as --max-budget-usd (#362). At least one cent."""
        cap = self.acfg.max_cost_usd_per_day
        return max(0.01, cap - self._cost_today()) if cap and cap > 0 else 0.0

    def _cost_today(self) -> float:
        return self.db.count("cost_microusd") / 1e6

    def _deliver(self, s: dict, kind: str, text: str, *, by: str, correlation_id: str = "", lang: str = "en",
                 hops: int = 0) -> dict:
        """Route one owner message to a session: managed (a turn / steer), live channel (push), plugin (queued for
        the next Stop / prompt), else NO_LIVE_CHANNEL / UNSUPPORTED_ATTACH."""
        if not s.get("project"):
            return {"ok": False, "code": "NOT_ALLOWED_PROJECT", "text": say("NOT_ALLOWED_PROJECT", lang)}
        if hops > 1:
            return {"ok": False, "code": "HOP_LIMIT", "text": say("HOP_LIMIT", lang)}
        capped = self._cap(lang)
        if capped:
            return capped
        try:
            env = make(kind, provider=s["provider"], session_id=s["id"], project=s.get("project") or "",
                       payload={"text": text}, correlation_id=correlation_id, origin="owner", hops=hops)
        except EnvelopeError as e:
            return {"ok": False, "code": "HOP_LIMIT" if "HOP" in str(e) else "error", "text": str(e)}
        m = self.managed.get(s["id"])
        sid = s["id"]
        if isinstance(m, _OpenCodeSession):            # D2: EVERY prompt to OpenCode is approved on screen, whoever asked
            # the EXACT string that will be sent (a status question is wrapped with its header and reply rule), cleaned
            # once with code blocks kept, shown in full and sent unchanged: shown == sent
            prof = self.opencode_profile((s.get("meta") or {}).get("requested_model") or None)
            if not prof["ok"]:
                return {"ok": False, "code": "MODEL_NOT_ALLOWED", "text": say("MODEL_NOT_ALLOWED", lang)}
            why = self._verify_opencode_config(prof, m.directory)
            if why:
                self.db.audit(by, kind, session_id=sid, decision="refused", detail=f"opencode config: {why[:120]}")
                return {"ok": False, "code": "CONFIG_NOT_VERIFIED", "text": say("CONFIG_NOT_VERIFIED", lang), "detail": why}
            raw_out = wrap_for_agent([{**env.to_dict(), "payload": env.payload}]) if kind == "status_request" else text
            shown = clean(raw_out, OC_PROMPT_SHOWN + 1, keep_code=True)
            if len(shown) > OC_PROMPT_SHOWN:
                return {"ok": False, "code": "error", "text": f"The prompt is too long to approve on screen "
                                                              f"(max {OC_PROMPT_SHOWN} characters)."}
            text = shown
            if not self._ask_owner(self._opencode_prompt_text(s, shown, prof), title="agents_opencode_prompt", risk="medium",
                                   voice=False, details=self._opencode_details(s, prof)):
                self.db.audit(by, kind, session_id=sid, decision="denied", detail="opencode prompt")
                return {"ok": False, "code": "NOT_APPROVED", "text": say("NOT_APPROVED", lang)}
        if m is not None:
            ok = self._managed_send(sid, m, text if isinstance(m, _OpenCodeSession) else
                                    (wrap_for_agent([{**env.to_dict(), "payload": env.payload}])
                                     if kind == "status_request" else text), steer=(kind == "command"),
                                    model=prof["spec"] if isinstance(m, _OpenCodeSession) else "")
            if not ok:
                return {"ok": False, "code": "SESSION_CLOSED", "text": say("SESSION_CLOSED", lang)}
            self.db.enqueue(env)
            self.db.ack([env.message_id], delivery="turn")
            self.db.bump("turn")
            self.db.audit(by, kind, session_id=sid, result="sent", detail="managed")
            return {"ok": True, "code": "SENT", "delivery": "turn", "text": say("SENT", lang),
                    "message_id": env.message_id}
        ch = self.channels.get(sid)
        if ch is not None and ch.meta.get("channel_live") and not ch.closed:
            self.db.enqueue(env)
            content = wrap_for_agent([{**env.to_dict(), "payload": env.payload}])
            if ch.push({"push": "message", "content": content, "message_ids": [env.message_id],
                        "meta": {"from": "jarves", "correlation_id": correlation_id or env.message_id,
                                 "kind": kind}}):
                self.db.bump("turn")
                self.db.audit(by, kind, session_id=sid, result="pushed", detail="channel")
                return {"ok": True, "code": "SENT", "delivery": "channel", "text": say("SENT", lang),
                        "message_id": env.message_id}
        if s["provider"] == "claude" and s.get("plugin"):
            self.db.enqueue(env)
            self.db.audit(by, kind, session_id=sid, result="queued", detail="next safe stop")
            return {"ok": True, "code": "QUEUED", "delivery": "stop_hook", "text": say("QUEUED", lang),
                    "message_id": env.message_id}
        if s["provider"] in ("codex", "opencode"):
            return {"ok": False, "code": "UNSUPPORTED_ATTACH", "text": say("UNSUPPORTED_ATTACH", lang)}
        return {"ok": False, "code": "NO_LIVE_CHANNEL", "text": say("NO_LIVE_CHANNEL", lang) +
                (" Install the JARVES plugin: jarvis agents install-plugin claude" if lang == "en" else
                 " אפשר להתקין את התוסף: jarvis agents install-plugin claude")}

    def _managed_send(self, sid: str, m, text: str, steer: bool = False, model: str = "") -> bool:
        if isinstance(m, claude.ManagedClaude):
            ok = m.send(text)
            if ok:
                self.db.upsert_session(sid, state="RUNNING", last_progress_at=self.clock())
            return ok
        if isinstance(m, _CodexThread):
            try:
                srv = self._codex_server()
                if m.turn_id and m.busy:
                    srv.steer(m.thread_id, m.turn_id, text)
                else:
                    # busy BEFORE the request: the server may send turn/started .. turn/completed before its reply
                    m.busy = True
                    self.db.upsert_session(sid, state="RUNNING", last_progress_at=self.clock())
                    t = srv.start_turn(m.thread_id, text)
                    if str(t.get("id") or "") not in m.done:
                        m.turn_id = str(t.get("id") or "")
                return True
            except (codex.RpcError, LookupError, OSError):
                m.busy = False
                self.db.upsert_session(sid, state="IDLE")
                return False
        if isinstance(m, _OpenCodeSession):
            try:
                self._opencode_client().prompt(m.session_id, text, m.directory, model)       # the APPROVED spec, not a re-read
                self.db.upsert_session(sid, state="RUNNING", last_progress_at=self.clock())
                return True
            except opencode.OpenCodeError:
                return False
        return False

    def tell(self, ref: str, text: str, *, by: str = "owner", lang: str | None = None) -> dict:
        lang = lang or lang_of(text)
        s, err = self._resolved(ref, lang, live_only=True)
        if err:
            return err
        if s["state"] == "PAUSED" and s["id"] in self.managed:
            self.db.upsert_session(s["id"], state="IDLE")
        r = self._deliver(s, "command", text, by=by, lang=lang)
        return {**r, "session": self.view(s, lang)}

    def continue_(self, ref: str, *, by: str = "owner", lang: str = "en") -> dict:
        s, err = self._resolved(ref, lang, live_only=True)
        if err:
            return err
        if s["state"] == "PAUSED":
            self.db.upsert_session(s["id"], state="IDLE")
            s = self.db.session(s["id"])
        return self.tell(s["id"], "Continue with your task." if lang == "en" else "המשך במשימה שלך.", by=by,
                         lang=lang)

    def pause(self, ref: str, *, by: str = "owner", lang: str = "en") -> dict:
        s, err = self._resolved(ref, lang, live_only=True)
        if err:
            return err
        m = self.managed.get(s["id"])
        if m is not None:
            self._interrupt(s["id"], m)
            self.db.upsert_session(s["id"], state="PAUSED", last_progress_at=self.clock())
            self.db.add_event(s["id"], "paused", "paused by the owner (current turn interrupted)")
            self.db.audit(by, "pause", session_id=s["id"], result="interrupted")
            return {"ok": True, "code": "ok", "text": "Paused." if lang == "en" else "הושהה.",
                    "session": self.view(self.db.session(s["id"]), lang)}
        r = self._deliver(s, "command", "Please pause after your current step and wait until the owner says "
                                        "continue.", by=by, lang=lang)
        if r["ok"]:
            self.db.audit(by, "pause", session_id=s["id"], result=r["code"])
        return {**r, "session": self.view(s, lang)}

    def _interrupt(self, sid: str, m) -> bool:
        if isinstance(m, claude.ManagedClaude):
            return m.interrupt()
        if isinstance(m, _CodexThread) and m.turn_id:
            try:
                self._codex_server().interrupt(m.thread_id, m.turn_id)
                return True
            except (codex.RpcError, LookupError, OSError):
                return False
        if isinstance(m, _OpenCodeSession):
            try:
                self._opencode_client().abort(m.session_id, m.directory)
                return True
            except opencode.OpenCodeError:
                return False
        return False

    def interrupt(self, ref: str, *, by: str = "owner", lang: str = "en") -> dict:
        s, err = self._resolved(ref, lang, live_only=True)
        if err:
            return err
        m = self.managed.get(s["id"])
        if m is None:
            return {"ok": False, "code": "UNSUPPORTED_ATTACH" if s["provider"] == "codex" else "NO_LIVE_CHANNEL",
                    "text": ("I can only interrupt sessions JARVES started; press Esc in that terminal." if lang == "en"
                             else "אני יכול לעצור רק סשן שג'רוויס הפעיל; לחץ Esc בטרמינל הזה.")}
        ok = self._interrupt(s["id"], m)
        self.db.audit(by, "interrupt", session_id=s["id"], result="ok" if ok else "failed")
        return {"ok": ok, "code": "ok" if ok else "SESSION_CLOSED", "text": say("ok" if ok else "SESSION_CLOSED", lang)}

    def stop_session(self, ref: str, *, by: str = "owner", approved: bool = False, lang: str = "en") -> dict:
        s, err = self._resolved(ref, lang, live_only=True)
        if err:
            return err
        m = self.managed.get(s["id"])
        if m is None and s["provider"] == "opencode":
            return {"ok": False, "code": "UNSUPPORTED_ATTACH",
                    "text": ("JARVES does not control this OpenCode session (it was started elsewhere, or JARVES restarted "
                             "and lost track of it); stop it in OpenCode itself." if lang == "en" else
                             "ג'רוויס לא שולט בסשן OpenCode הזה (הופעל במקום אחר, או שג'רוויס הופעל מחדש); עצרו אותו ב-OpenCode.")}
        if m is None:
            r = self._deliver(s, "command", "The owner asks you to wrap up: finish or checkpoint the current step, "
                                            "summarize, and stop.", by=by, lang=lang)
            return {**r, "text": ("I can't stop a session you started yourself; " if lang == "en" else
                                  "אני לא עוצר סשן שהפעלת בעצמך; ") + r["text"]}
        if by == "autopilot":                       # only a session the autopilot itself started; never an owner's own
            if not (s.get("meta") or {}).get("autopilot") or s["provider"] != "claude":
                return {"ok": False, "code": "NOT_APPROVED", "text": say("NOT_APPROVED", lang)}
            approved = True
        if not approved and not self._ask_owner(f"Stop the {s['provider']} session in {s.get('project')}?",
                                                title="agents_stop", risk="medium", voice=False,
                                                details={"project": s.get("project"), "session": s["id"]}):
            return {"ok": False, "code": "NOT_APPROVED", "text": say("NOT_APPROVED", lang)}
        stopped = self._interrupt(s["id"], m)
        if isinstance(m, _OpenCodeSession) and not stopped:       # the abort failed: do not claim it stopped, keep the session
            self.db.audit(by, "stop", session_id=s["id"], decision="approved", result="abort_failed")
            return {"ok": False, "code": "SERVER_ERROR",
                    "text": ("Could not stop the OpenCode session (the server did not accept the abort); it is still tracked."
                             if lang == "en" else "לא הצלחתי לעצור את סשן OpenCode (השרת לא קיבל את העצירה); הוא עדיין במעקב.")}
        if isinstance(m, claude.ManagedClaude):
            m.close(grace_s=10)
        self.managed.pop(s["id"], None)
        self.db.upsert_session(s["id"], state="STOPPED", ended_at=self.clock())
        self.db.add_event(s["id"], "stopped", "stopped gracefully by the owner")
        self.db.audit(by, "stop", session_id=s["id"], decision="approved", result="stopped")
        return {"ok": True, "code": "ok", "text": "Stopped." if lang == "en" else "נעצר."}

    # ---- Workflow autopilot owner grant (Tier R): lets by="autopilot" start/stop Claude workers without a popup
    # ONLY while an unexpired owner grant matches the request exactly. Tool approvals are never covered.
    terminal_ask = None      # set only by the CLI when no Core runs and stdin/stdout are terminals (typed code in the terminal)
    GRANT_KEY = "autopilot_grant"
    GRANT_MAX_DAYS = 365.0
    GRANT_MAX_WORKERS = 5
    GRANT_MAX_MODELS = 3
    ENV_PREFIX = "JARVES_WORKFLOW_"

    def _autopilot_env(self, env) -> dict | None:
        if not env:
            return {}
        if not isinstance(env, dict) or len(env) > 4:
            return None
        out = {}
        for k, v in env.items():
            if not (isinstance(k, str) and k.startswith(self.ENV_PREFIX) and k.replace("_", "").isalnum()
                    and isinstance(v, str) and len(v) <= 200 and not any(c in v for c in "\r\n\x00")):
                return None
            out[k] = v
        return out

    def autopilot_grant(self) -> dict | None:
        """The active grant, or None (missing, malformed or expired)."""
        try:
            g = json.loads(self.db.get_meta(self.GRANT_KEY) or "null")
            if not isinstance(g, dict) or g.get("provider") != "claude" or float(g.get("expires_at") or 0) <= self.clock():
                return None
        except (ValueError, TypeError):
            return None
        return g

    def _autopilot_killed(self) -> bool:
        """The kill switch in <home>/workflow/autopilot.json ends the grant at once; an unreadable file fails closed."""
        path = self.home / "workflow" / "autopilot.json"
        if not path.exists():
            return False
        try:
            return bool(json.loads(path.read_text(encoding="utf-8")).get("kill"))
        except (OSError, ValueError, AttributeError):
            return True

    def _grant_refusal(self, provider: str, proj, model: str, here: bool) -> str:
        """"" = the grant covers this start exactly; "limit" = covered but at max_concurrent; else "nomatch"."""
        g = self.autopilot_grant()
        if g is None or provider != "claude" or here or not model or self._autopilot_killed():
            return "nomatch"
        if proj.name != g.get("project") or model not in (g.get("models") or []):
            return "nomatch"
        running = [x for x in self.db.sessions() if x["state"] in LIVE_STATES and (x.get("meta") or {}).get("autopilot")]
        if len(running) >= int(g.get("max_concurrent") or 0):
            return "limit"
        return ""

    def autopilot_op(self, action: str, *, by: str = "owner", project: str = "", models=None, days=None,
                     max_concurrent=None, lang: str = "en") -> dict:
        if action == "status":
            g = self.autopilot_grant()
            return {"ok": True, "code": "ok", "grant": g, "text": "grant active" if g else "no active grant"}
        if by in ("autopilot", "voice"):
            return {"ok": False, "code": "NOT_APPROVED", "text": say("NOT_APPROVED", lang)}
        if action == "revoke":
            self.db.set_meta(self.GRANT_KEY, "")
            self.db.audit(by, "autopilot_revoke", decision="approved")
            return {"ok": True, "code": "ok", "text": "Autopilot grant revoked."}
        if action != "grant":
            return {"ok": False, "code": "error", "text": "Unknown autopilot action."}
        projs = self.projects.by_word(project)
        if len(projs) != 1 or not projs[0].allow_start:
            return {"ok": False, "code": "NOT_ALLOWED_PROJECT", "text": say("NOT_ALLOWED_PROJECT", lang)}
        ms = models if isinstance(models, (list, tuple)) else str(models or "").split(",")
        ms = sorted({str(m).strip() for m in ms if str(m).strip()})
        if not ms or len(ms) > self.GRANT_MAX_MODELS or any(len(m) > 200 or any(c in m for c in "\r\n\x00") for m in ms):
            return {"ok": False, "code": "MODEL_NOT_ALLOWED", "text": say("MODEL_NOT_ALLOWED", lang)}
        try:
            d = float(days if days not in (None, "") else self.GRANT_MAX_DAYS)
            n = int(max_concurrent if max_concurrent not in (None, "") else 1)
        except (TypeError, ValueError):
            return {"ok": False, "code": "error", "text": "Invalid days or worker count."}
        if not 0 < d <= self.GRANT_MAX_DAYS or not 1 <= n <= self.GRANT_MAX_WORKERS:
            return {"ok": False, "code": "error",
                    "text": f"days must be 0-{self.GRANT_MAX_DAYS:g} and workers 1-{self.GRANT_MAX_WORKERS}."}
        proj = projs[0]
        ask = self.terminal_ask or self._ask_owner
        if not ask(
                f"Let the workflow autopilot start and stop Claude sessions without asking, for {d:g} day(s):\n"
                f"project {proj.name}, models {', '.join(ms)}, up to {n} at once. Each runs in a new worktree; "
                "every tool approval still asks you. Type the code to allow.",
                title="agents_autopilot_grant", risk="high", voice=False, typed=True,
                details={"project": proj.name, "models": ms, "days": d, "max_concurrent": n}):
            self.db.audit(by, "autopilot_grant", decision="denied", detail=proj.name)
            return {"ok": False, "code": "NOT_APPROVED", "text": say("NOT_APPROVED", lang)}
        g = {"provider": "claude", "project": proj.name, "models": ms, "max_concurrent": n,
             "granted_at": self.clock(), "expires_at": self.clock() + d * 86400}
        self.db.set_meta(self.GRANT_KEY, json.dumps(g))
        self.db.audit(by, "autopilot_grant", decision="approved", detail=f"{proj.name} {','.join(ms)} {d:g}d x{n}")
        return {"ok": True, "code": "ok", "grant": g, "text": "Autopilot grant saved."}

    # ================================================================== start / resume
    def _ask_owner(self, prompt: str, *, title: str, risk: str = "high", voice: bool = False, typed: bool = False,
                   details: dict | None = None, kind: str = "tool", timeout: float | None = None) -> bool:
        """An on-screen approval through the Core's ApprovalService (popup / dashboard / CLI). Called from a
        worker thread, never from the event loop. Without a Core: approvals.ask_owner (dashboard banner / CLI)."""
        from .. import approvals as appr
        return self._ask_owner_state(prompt, title=title, risk=risk, voice=voice, typed=typed, details=details,
                                     kind=kind, timeout=timeout)[0] == "approved"

    def _ask_owner_state(self, prompt: str, *, title: str, risk: str, voice: bool, typed: bool, details: dict | None,
                         kind: str = "tool", timeout: float | None = None) -> tuple[str, str]:
        """(state, approval id): approved | denied (the owner said no) | timeout | no_channel | cancelled."""
        from .. import approvals as appr
        svc = self.approvals or appr.current()
        if svc is None or svc.loop is None or not svc.loop.is_running() or \
                Path(svc.home).resolve() != self.home.resolve():
            ok, _ = appr.ask_owner(self.home, prompt, title=title, kind=kind, risk=risk, details=details,
                                   timeout=timeout, typed=typed)
            return ("approved" if ok else "denied"), ""
        try:
            if asyncio.get_running_loop() is svc.loop:
                raise RuntimeError("_ask_owner must not run on the event loop thread")
        except RuntimeError as e:
            if "must not" in str(e):
                raise
        marker = secrets.token_hex(6)
        seen: dict = {}

        def on_ask(a) -> None:
            if a.details.get("_req") == marker:
                seen["id"] = a.id

        def on_done(a) -> None:
            if a.details.get("_req") == marker:
                seen["state"] = a.state
        svc.on_ask.append(on_ask)
        svc.listeners.append(on_done)
        try:
            fut = asyncio.run_coroutine_threadsafe(
                svc.ask(prompt, title=title, kind=kind, risk=risk, details={**(details or {}), "_req": marker},
                        typed=typed, voice=voice, source="agent", timeout=timeout), svc.loop)
            ok = fut.result((timeout or svc.timeout_s) + 30)
        except Exception:
            ok = False
        finally:
            for lst, fn in ((svc.on_ask, on_ask), (svc.listeners, on_done)):
                try:
                    lst.remove(fn)
                except ValueError:
                    pass
        return ("approved" if ok else seen.get("state") or "no_channel"), seen.get("id", "")

    def start_session(self, provider: str, project: str, task: str, *, by: str = "owner", approved: bool = False,
                      here: bool = False, lang: str | None = None, model: str = "",
                      env: dict | None = None) -> dict:
        lang = lang or lang_of(task)
        if by == "autopilot" and PROVIDER_WORDS.get(provider.lower(), provider.lower()) != "claude":
            return {"ok": False, "code": "NOT_ALLOWED_PROJECT", "text": "The autopilot may only start Claude sessions."}
        if by != "autopilot":
            env = None                                # env pass-through is for the autopilot only
        else:
            env = self._autopilot_env(env)
            if env is None:
                return {"ok": False, "code": "error", "text": "Invalid autopilot environment."}
        if not isinstance(model, str) or len(model) > 200 or any(c in model for c in "\r\n\x00"):
            return {"ok": False, "code": "error", "text": "Invalid model identifier."}
        model = model.strip()
        provider = PROVIDER_WORDS.get(provider.lower(), provider.lower())
        if provider not in ("claude", "codex", "opencode"):
            return {"ok": False, "code": "PROVIDER_MISSING", "text": say("PROVIDER_MISSING", lang)}
        if provider == "opencode":                    # D2: the server is external; the switch must be on, password set
            if not self._opencode_on():
                return {"ok": False, "code": "PROVIDER_MISSING", "text": say("PROVIDER_MISSING", lang)}
            approved = False                          # never trusted from a caller / the bridge: the full popup always shows
            prof = self.opencode_profile(model or None)
            if not prof["ok"]:
                return {"ok": False, "code": "MODEL_NOT_ALLOWED", "text": say("MODEL_NOT_ALLOWED", lang), "model": prof}
            task = clean(task, OC_PROMPT_SHOWN + 1, keep_code=True)
            if len(task) > OC_PROMPT_SHOWN:
                return {"ok": False, "code": "error", "text": f"The task is too long to approve on screen "
                                                              f"(max {OC_PROMPT_SHOWN} characters)."}
        else:
            cmd = self.claude_cmd if provider == "claude" else self.codex_cmd
            if not cmd:
                return {"ok": False, "code": "PROVIDER_MISSING", "text": say("PROVIDER_MISSING", lang)}
            if not self.serve:
                return {"ok": False, "code": "CORE_NOT_RUNNING", "text": say("CORE_NOT_RUNNING", lang)}
        projs = self.projects.by_word(project) or [p for p in self.projects.load()
                                                    if policy.norm(p.path) == policy.norm(project)]
        if len(projs) != 1:
            return {"ok": False, "code": "AMBIGUOUS_SESSION" if len(projs) > 1 else "NOT_ALLOWED_PROJECT",
                    "text": say("AMBIGUOUS_SESSION" if len(projs) > 1 else "NOT_ALLOWED_PROJECT", lang)}
        proj = projs[0]
        if not proj.allow_start:
            return {"ok": False, "code": "NOT_ALLOWED_PROJECT", "text": say("NOT_ALLOWED_PROJECT", lang)}
        capped = self._cap(lang)
        if capped:
            return capped
        self.refresh(force=True)
        live = [s for s in self.db.sessions() if s["state"] in LIVE_STATES]
        if here and policy.writers_at(proj.path, live):
            return {"ok": False, "code": "SECOND_WRITER", "text": say("SECOND_WRITER", lang),
                    "offer": "worktree"}
        if provider == "opencode":                    # fail before asking when the password / server is missing
            try:
                self._opencode_client().health()
            except opencode.OpenCodeError as e:
                return {"ok": False, "code": e.code, "text": say(e.code, lang), "detail": e.detail}
            why = self._verify_opencode_config(prof, proj.path)      # OpenCode's resolved settings, read just before the popup
            if why:
                self.db.audit(by, "start", decision="refused", detail=f"opencode config: {why[:120]}")
                return {"ok": False, "code": "CONFIG_NOT_VERIFIED", "text": say("CONFIG_NOT_VERIFIED", lang), "detail": why}
        auto = False
        if by == "autopilot":
            approved = False                          # a caller's flag never counts for the autopilot: only the grant does
            why = self._grant_refusal(provider, proj, model, here)
            if why == "limit":
                return {"ok": False, "code": "BUDGET_REACHED", "text": "The autopilot grant's worker limit is reached."}
            auto = why == ""                          # an exact grant match: no popup; anything else shows the normal one
            approved = auto
        if not approved and not self._ask_owner(
                f"Start a new {provider} session in {proj.name}" + (" (project folder)" if here else
                                                                     " (new worktree)")
                + (f"\nThe prompt goes to the OpenCode server at {self.acfg.opencode_url}, repo {proj.name}, branch: "
                   + ("the project's current branch" if here else "a NEW jarves/ branch in a new worktree")
                   + f", session: new.\n{self._opencode_model_line(prof)}\nPrompt (untrusted text, shown in full):\n{task}"
                   if provider == "opencode"
                   else (f"\nRequested model: {model}" if model else "") + f":\n{task[:600]}"),
                title="agents_start", risk="medium", voice=False,
                details={"provider": provider, "project": proj.name, "folder": proj.path, "requested_model": model,
                         **({"server": self.acfg.opencode_url, "branch": "current" if here else "new jarves/ worktree branch",
                             "session": "new", "model": prof["model"], "model_kind": prof["kind"]}
                            if provider == "opencode" else {})}):
            self.db.audit(by, "start", decision="denied", detail=f"{provider} {proj.name}")
            return {"ok": False, "code": "NOT_APPROVED", "text": say("NOT_APPROVED", lang)}
        try:
            cwd = proj.path if here else self._new_worktree(proj, task)
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            return {"ok": False, "code": "error", "text": f"worktree failed: {clean(e, 200)}"}
        if policy.writers_at(cwd, live):
            return {"ok": False, "code": "SECOND_WRITER", "text": say("SECOND_WRITER", lang), "offer": "worktree"}
        if provider == "claude":
            r = self._start_claude(proj, cwd, task, by=by, model=model, lang=lang, env=env)
            if auto and r.get("ok") and r.get("session", {}).get("id"):
                sid = r["session"]["id"]
                cur = self.db.session(sid) or {}
                self.db.upsert_session(sid, meta={**(cur.get("meta") or {}), "autopilot": True,
                                                  "workflow_agent": (env or {}).get("JARVES_WORKFLOW_AGENT", "")})
                self.db.audit(by, "start_by_grant", session_id=sid, decision="approved", detail=f"{proj.name} {model}")
        elif provider == "opencode":
            r = self._start_opencode(proj, cwd, task, by=by, lang=lang, model=prof["spec"])
        else:
            r = self._start_codex(proj, cwd, task, by=by, model=model, lang=lang)
        return self._remember_requested_model(r, model, lang)

    def _remember_requested_model(self, r: dict, model: str, lang: str) -> dict:
        if r.get("ok") and model and r.get("session", {}).get("id"):
            sid = r["session"]["id"]
            current = self.db.session(sid)
            current = self.db.upsert_session(sid, meta={**(current.get("meta") or {}), "requested_model": model})
            r["session"] = self.view(current, lang)
        return r

    # ---- OpenCode model profile (V7 D3): which model JARVES asks for, and whether it may be used
    def opencode_profile(self, requested_model: str | None = None) -> dict:
        """kind: free | local | paid | unknown | default. `ok` = may be used now: free/local always; paid, unknown or the
        default (OpenCode's own choice, price unknown to JARVES) only with agents.opencode_allow_paid."""
        from .. import model_policy as mp
        spec = (self.acfg.opencode_model if requested_model is None else requested_model) or ""
        spec = spec.strip()
        out = {"model": spec or "(OpenCode's own default)", "kind": "default", "ok": False,
               "cost_cap_note": "the USD cap is NOT enforced for OpenCode (it reports no cost); only the daily turn limit applies",
               "allow_paid": bool(self.acfg.opencode_allow_paid), "error": ""}
        if spec:
            out["kind"], out["error"] = self._classify_model(spec)
        paid_ok = bool(self.acfg.opencode_allow_paid) and not out["error"] and self.acfg.max_cost_usd_per_day > 0
        if self.acfg.opencode_allow_paid and self.acfg.max_cost_usd_per_day <= 0:
            out["error"] = out["error"] or "agents.opencode_allow_paid needs agents.max_cost_usd_per_day above 0"
        out["ok"] = out["kind"] in ("free", "local") or paid_ok
        out["spec"] = spec                                  # exactly what was classified; this is what gets sent
        return out

    def _classify_model(self, spec: str) -> tuple[str, str]:
        """("free"|"local"|"paid"|"unknown", error) for an OpenCode "providerID/modelID". The registry id is the MODEL part
        (OpenCode names OpenRouter's free router providerID openrouter, modelID openrouter/free, so the spec is
        openrouter/openrouter/free) and the registry entry's provider must equal the providerID: another provider with the
        same model name is unknown, never free."""
        from .. import model_policy as mp
        try:
            prov, mid = opencode.parse_model(spec)
        except opencode.OpenCodeError as e:
            return "unknown", e.detail
        try:
            reg = mp.load_registry(self.home / "models.toml")
        except mp.ModelConfigError:
            reg = mp.ModelRegistry()
        info = reg.get(mid)
        if info is None or info.provider.lower() != prov.lower():
            return "unknown", ""
        return ("local" if info.local else ("paid" if info.paid else "free")), ""

    def _verify_opencode_config(self, prof: dict, directory: str) -> str:
        """"" = OpenCode's RESOLVED config for `directory` keeps to the free model; else a short reason. Only in free mode
        (a paid model is allowed explicitly, so there is nothing to protect). Fail closed: unreadable config = refusal.
        Refuses when: the chosen provider has a baseURL / models override, `small_model` or any agent / mode model is set
        to anything that is not free or local. (Models left unset are OpenCode's own choice: documented, not controlled.)"""
        if prof["kind"] not in ("free", "local") or prof.get("allow_paid"):
            return ""
        try:
            cfg = self._opencode_client().config(directory)
        except opencode.OpenCodeError as e:
            return f"the settings could not be read ({e.code})"
        prov = opencode.parse_model(prof["spec"])[0].lower()
        provs = cfg.get("provider") if isinstance(cfg.get("provider"), dict) else {}
        for pid, pv in provs.items():
            if str(pid).lower() != prov or not isinstance(pv, dict):
                continue
            opts = pv.get("options") if isinstance(pv.get("options"), dict) else {}
            if pv.get("baseURL") or opts.get("baseURL") or pv.get("models") or pv.get("npm"):
                return f"the provider {pid} is overridden in opencode.json (baseURL / models / package)"
        models = [("small_model", cfg.get("small_model"))]
        for group in ("agent", "mode"):
            g = cfg.get(group)
            for name, a in (g.items() if isinstance(g, dict) else []):
                models.append((f"{group}.{name}", a.get("model") if isinstance(a, dict) else None))
        for where, m in models:
            if m in (None, ""):
                continue
            kind = self._classify_model(str(m))[0] if isinstance(m, str) else "unknown"
            if kind not in ("free", "local"):
                return f"{where} is set to a model that is not free or local ({str(m)[:60]})"
        return ""

    def _opencode_model_line(self, prof: dict) -> str:
        cost = ("no cost expected: JARVES checked OpenCode's resolved settings for this folder just now (no provider "
                "override, no paid agent model); it still cannot measure spend, and models left unset are OpenCode's choice"
                if prof["kind"] in ("free", "local") and not prof.get("allow_paid") else
                "COST UNKNOWN to JARVES: it cannot measure or cap OpenCode spend (only the daily turn limit applies)")
        return f"Model: {prof['model']} ({prof['kind']}); {cost}."

    # ---- OpenCode (V7 D2): third harness. Approval-gated start in its own worktree; every later prompt is approved too.
    def _opencode_details(self, s: dict, prof: dict | None = None) -> dict:
        prof = prof or self.opencode_profile((s.get("meta") or {}).get("requested_model") or None)
        return {"provider": "opencode", "project": s.get("project") or "", "folder": s.get("cwd") or "",
                "branch": s.get("branch") or "worktree branch", "session": s.get("provider_session_id") or s["id"],
                "server": self.acfg.opencode_url, "model": prof["model"],
                "model_kind": prof["kind"]}

    def _opencode_prompt_text(self, s: dict, text: str, prof: dict | None = None) -> str:
        prof = prof or self.opencode_profile((s.get("meta") or {}).get("requested_model") or None)
        return (f"{self._opencode_model_line(prof)}\nSend this to OpenCode session {s.get('provider_session_id')} (repo {s.get('project')}, "
                f"branch {s.get('branch') or 'worktree branch'}, server {self.acfg.opencode_url})?"
                f"\nExact text that will be sent (untrusted):\n{text}\n(folder {s.get('cwd')})")

    def _start_opencode(self, proj, cwd: str, task: str, *, by: str, lang: str, model: str = "") -> dict:
        try:
            c = self._opencode_client()
            raw = c.create_session(cwd, task[:80])
            try:
                if task.strip():
                    c.prompt(raw, task, cwd, model)
            except opencode.OpenCodeError:
                try:
                    c.abort(raw, cwd)                    # never leave an unmanaged server session behind
                except opencode.OpenCodeError:
                    pass
                raise
        except opencode.OpenCodeError as e:
            return {"ok": False, "code": e.code, "text": say(e.code, lang), "detail": e.detail}
        sid = session_key("opencode", raw)
        self.managed[sid] = _OpenCodeSession(raw, cwd)
        now = self.clock()
        branch = ""
        if cwd != proj.path:
            branch = "jarves/" + Path(cwd).name
        self.db.upsert_session(sid, origin="managed", kind="managed", state="RUNNING" if task.strip() else "IDLE", cwd=cwd,
                               project=proj.name, project_root=proj.path, worktree=cwd if cwd != proj.path else "",
                               branch=branch, auth="password", started_at=now, last_seen_at=now,
                               status_source="managed", attachable=1, ended_at=None, name=clean(task, 120))
        self.db.add_event(sid, "started", "OpenCode session started under JARVES")
        self.db.bump("turn")                               # the daily turn limit is the one real limit on OpenCode (no cost data)
        self.db.audit(by, "start", session_id=sid, decision="approved", result="started", detail=f"opencode {proj.name}")
        logs.info("agents", "AGENTS_SESSION_STARTED", "managed coding session started", provider="opencode")
        return {"ok": True, "code": "ok", "session": self.view(self.db.session(sid), lang),
                "text": ("Started OpenCode in " if lang == "en" else "הפעלתי את OpenCode בפרויקט ") + proj.name + "."}

    def _new_worktree(self, proj: policy.Project, task: str) -> str:
        root = Path(proj.path)
        slug = re.sub(r"[^a-z0-9]+", "-", task.lower())[:24].strip("-") or "task"
        slug = f"{time.strftime('%m%d-%H%M%S')}-{slug}"
        if not (root / ".git").exists():
            raise ValueError("not a git repository: start it in the project folder (--here) instead")
        wt = root / ".jarves" / "worktrees" / slug
        wt.parent.mkdir(parents=True, exist_ok=True)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        # Workers start from the freshest jarves/main when that remote exists (best effort, never blocks a start).
        base: list[str] = []
        try:
            subprocess.run(["git", "-C", str(root), "fetch", "jarves", "main"], capture_output=True, text=True,
                           timeout=60, creationflags=flags)
            if subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", "-q", "jarves/main"],
                              capture_output=True, text=True, timeout=20, creationflags=flags).returncode == 0:
                base = ["jarves/main"]
        except (OSError, subprocess.SubprocessError):
            pass
        p = subprocess.run(["git", "-C", str(root), "worktree", "add", "-b", f"jarves/{slug}", str(wt), *base],
                           capture_output=True, text=True, timeout=120, creationflags=flags)
        if p.returncode != 0:
            raise ValueError(clean(p.stderr or p.stdout, 200))
        excl = root / ".git" / "info" / "exclude"
        try:
            cur = excl.read_text(encoding="utf-8") if excl.exists() else ""
            if ".jarves/" not in cur:
                excl.parent.mkdir(parents=True, exist_ok=True)
                excl.write_text(cur + ("\n" if cur and not cur.endswith("\n") else "") + ".jarves/\n",
                                encoding="utf-8")
        except OSError:
            pass
        return str(wt)

    def _worker_tools(self) -> list[str]:
        """Allow rules for an autopilot worker: the owner's `worker_allowed_tools` policy key, else the narrow default."""
        try:
            from ..workflow import autopilot
            pol, err = autopilot.load(self.home)
            override = None if err else pol.get("worker_allowed_tools")
        except Exception:                                  # never block a start on a policy read
            override = None
        return claude.autopilot_allowed_tools(sys.executable, override)

    def _start_claude(self, proj, cwd: str, task: str, *, by: str, model: str, lang: str, resume: str = "",
                      env: dict | None = None) -> dict:
        pdir = str(claude.write_plugin(self.home))
        tmp_sid = f"claude:pending-{uuid.uuid4().hex[:8]}"
        holder: dict = {"sid": tmp_sid}
        started = threading.Event()

        def on_event(kind: str, d: dict) -> None:
            self._managed_claude_event(holder, kind, d, started)
        m = claude.ManagedClaude(self.claude_cmd, cwd, plugin_dir=pdir, on_event=on_event, resume=resume, model=model,
                                 env=env, max_budget_usd=self._budget_left(), restricted=(by == "diag"),
                                 allowed_tools=self._worker_tools() if by == "autopilot" else None,
                                 permission_mode=claude.WORKER_PERMISSION_MODE
                                 if by == "autopilot" and cwd != proj.path else "default")
        try:
            m.start()
        except OSError as e:
            return {"ok": False, "code": "PROVIDER_MISSING", "text": f"{say('PROVIDER_MISSING', lang)} ({clean(e, 80)})"}
        holder["m"] = m
        self.managed[tmp_sid] = m
        if task:
            m.send(task)
            self.db.bump("turn")
        started.wait(60)
        sid = holder["sid"]
        if sid == tmp_sid:
            self.managed.pop(tmp_sid, None)
            m.close(grace_s=2)
            return {"ok": False, "code": "SESSION_CLOSED", "text": "the new Claude session did not start"}
        s = self.db.session(sid) or {}
        if s.get("auth") == "api_key" and not self.acfg.allow_api_billing:
            m.close(grace_s=2)
            self.managed.pop(sid, None)
            self.db.upsert_session(sid, state="STOPPED", ended_at=self.clock())
            return {"ok": False, "code": "AUTH_REQUIRED", "text": say("AUTH_REQUIRED", lang)}
        self.db.upsert_session(sid, project=proj.name, project_root=proj.path, cwd=cwd, origin="managed",
                               kind="managed", worktree=cwd if cwd != proj.path else "", plugin=1)
        self.db.audit(by, "start", session_id=sid, decision="approved", result="started", detail=f"claude {proj.name}")
        logs.info("agents", "AGENTS_SESSION_STARTED", "managed coding session started", provider="claude")
        return {"ok": True, "code": "ok", "session": self.view(self.db.session(sid), lang),
                "text": (("Started Claude in " if lang == "en" else "הפעלתי את קלוד בפרויקט ") + proj.name
                         + (f" (session {sid[:8]})." if lang == "en" else f" (סשן {sid[:8]})."))}

    def _managed_claude_event(self, holder: dict, kind: str, d: dict, started: threading.Event) -> None:
        now = self.clock()
        sid = holder["sid"]
        if kind == "init" and d.get("session_id"):
            new = session_key("claude", d["session_id"])
            if new != sid:
                m = self.managed.pop(sid, None)
                if m is not None:
                    self.managed[new] = m
                holder["sid"] = sid = new
            self.db.upsert_session(sid, origin="managed", kind="managed", state="RUNNING", auth=d.get("auth"),
                                   started_at=now, last_seen_at=now, status_source="managed",
                                   capabilities=d.get("capabilities"), ended_at=None)
            self.db.add_event(sid, "started", f"session started ({d.get('model') or 'model'}, "
                                              f"{d.get('permission_mode') or 'default'} permissions)")
            started.set()
        elif kind == "assistant":
            text = summary(d.get("text") or "", 300)
            fields: dict = {"last_seen_at": now, "last_progress_at": now}
            if text:
                fields["last_activity"] = text
            self.db.upsert_session(sid, **fields)
            if text or d.get("tools"):
                self.db.add_event(sid, "progress", text or ("tools: " + ", ".join(d["tools"])[:200]))
        elif kind == "result":
            s = self.db.session(sid) or {}
            # total_cost_usd is the session's running total (a client-side estimate; on a subscription no money)
            prev, total = float(s.get("cost_usd") or 0), float(d.get("cost_usd") or 0)
            self.db.upsert_session(sid, state="PAUSED" if s.get("state") == "PAUSED" else "IDLE", last_seen_at=now,
                                   last_progress_at=now, last_summary=summary(d.get("text") or "", 400),
                                   cost_usd=max(prev, total), tokens_total=int(d.get("tokens_total") or 0),
                                   turns=int(s.get("turns") or 0) + 1)
            delta = max(0.0, total - prev)
            if delta:
                self.db.bump("cost_microusd", int(delta * 1e6))
            self.db.add_event(sid, "failed" if d.get("is_error") else "completed",
                              summary(d.get("text") or "", 300) or ("turn failed" if d.get("is_error") else "turn done"))
            self._signal(sid, "failed" if d.get("is_error") else "done", d.get("text") or "")
        elif kind == "exit":
            if sid in self.managed:
                self.managed.pop(sid, None)
                self.db.upsert_session(sid, state="STOPPED", ended_at=now)
                self.db.add_event(sid, "ended", f"process exited ({d.get('exit_code')})")
            started.set()

    def _start_codex(self, proj, cwd: str, task: str, *, by: str, model: str, lang: str,
                     resume_thread: str = "") -> dict:
        try:
            srv = self._codex_server()
        except (LookupError, codex.RpcError, OSError) as e:
            return {"ok": False, "code": "PROVIDER_MISSING", "text": f"{say('PROVIDER_MISSING', lang)} ({clean(e, 80)})"}
        if srv.account.get("type") != "chatgpt" and not self.acfg.allow_api_billing:
            return {"ok": False, "code": "AUTH_REQUIRED", "text": say("AUTH_REQUIRED", lang)}
        try:
            th = srv.resume_thread(resume_thread) if resume_thread else srv.start_thread(cwd, model=model)
        except codex.RpcError as e:
            return {"ok": False, "code": "error", "text": f"codex: {clean(e, 200)}"}
        tid = str(th.get("id") or resume_thread)
        sid = session_key("codex", tid)
        ct = _CodexThread(tid)
        self.managed[sid] = ct
        now = self.clock()
        self.db.upsert_session(sid, origin="managed", kind="managed", state="IDLE", cwd=str(th.get("cwd") or cwd),
                               project=proj.name, project_root=proj.path, worktree=cwd if cwd != proj.path else "",
                               auth="subscription", started_at=now, last_seen_at=now, status_source="managed",
                               attachable=1, ended_at=None)
        self.db.add_event(sid, "started", "thread resumed under JARVES" if resume_thread else "thread started")
        if task:
            try:
                ct.busy = True
                self.db.upsert_session(sid, state="RUNNING")
                t = srv.start_turn(tid, task)
                if str(t.get("id") or "") not in ct.done:
                    ct.turn_id = str(t.get("id") or "")
                self.db.bump("turn")
            except codex.RpcError as e:
                return {"ok": False, "code": "error", "text": f"codex: {clean(e, 200)}"}
        self.db.audit(by, "resume" if resume_thread else "start", session_id=sid, decision="approved",
                      result="started", detail=f"codex {proj.name}")
        logs.info("agents", "AGENTS_SESSION_STARTED", "managed coding session started", provider="codex")
        return {"ok": True, "code": "ok", "session": self.view(self.db.session(sid), lang),
                "text": ("Started Codex in " if lang == "en" else "הפעלתי את קודקס בפרויקט ") + proj.name + "."}

    def resume(self, ref: str, *, by: str = "owner", approved: bool = False, lang: str = "en") -> dict:
        if not self.serve:
            return {"ok": False, "code": "CORE_NOT_RUNNING", "text": say("CORE_NOT_RUNNING", lang)}
        s, err = self._resolved(ref, lang)
        if err:
            return err
        if s["provider"] == "opencode":                # D2: an OpenCode session is never resumed (no codex path for it)
            return {"ok": False, "code": "UNSUPPORTED_ATTACH", "text": say("UNSUPPORTED_ATTACH", lang)}
        if s["id"] in self.managed:
            return {"ok": True, "code": "ok", "text": "Already running under JARVES." if lang == "en" else
                    "כבר פועל דרך ג'רוויס."}
        self.refresh(force=True, codex_too=s["provider"] == "codex")
        s = self.db.session(s["id"]) or s
        if s["state"] in ("RUNNING", "WAITING_INPUT", "WAITING_APPROVAL", "IDLE") and s["provider"] == "claude":
            return {"ok": False, "code": "SECOND_WRITER", "text": say("SECOND_WRITER", lang)}
        if s["provider"] == "codex" and s["state"] in ("RUNNING", "WAITING_INPUT", "WAITING_APPROVAL"):
            return {"ok": False, "code": "SECOND_WRITER", "text": say("SECOND_WRITER", lang)}
        proj = self.projects.for_path(s.get("cwd"))
        if proj is None:
            return {"ok": False, "code": "NOT_ALLOWED_PROJECT", "text": say("NOT_ALLOWED_PROJECT", lang)}
        if not approved and not self._ask_owner(f"Resume the {s['provider']} session in {proj.name} under JARVES?",
                                                title="agents_resume", risk="medium", voice=False,
                                                details={"project": proj.name, "session": s["id"]}):
            return {"ok": False, "code": "NOT_APPROVED", "text": say("NOT_APPROVED", lang)}
        model = (s.get("meta") or {}).get("requested_model") or ""
        if s["provider"] == "claude":
            r = self._start_claude(proj, s.get("cwd") or proj.path, "", by=by, model=model, lang=lang,
                                   resume=s["provider_session_id"])
        else:
            r = self._start_codex(proj, s.get("cwd") or proj.path, "", by=by, model=model, lang=lang,
                                  resume_thread=s["provider_session_id"])
        return self._remember_requested_model(r, model, lang)

    # ================================================================== codex events / approvals
    def _codex_sid(self, params: dict) -> str:
        return session_key("codex", str(params.get("threadId") or (params.get("thread") or {}).get("id") or ""))

    def _codex_event(self, method: str, p: dict) -> None:
        sid = self._codex_sid(p)
        now = self.clock()
        m = self.managed.get(sid)
        if method == "turn/started":
            if isinstance(m, _CodexThread):
                m.turn_id, m.busy = str((p.get("turn") or {}).get("id") or m.turn_id), True
            self.db.upsert_session(sid, state="RUNNING", last_progress_at=now, last_seen_at=now)
        elif method == "turn/completed":
            turn = p.get("turn") or {}
            st = str(turn.get("status") or "completed")
            if isinstance(m, _CodexThread):
                m.busy = False
                m.done.add(str(turn.get("id") or ""))
            s = self.db.session(sid) or {}
            self.db.upsert_session(sid, state="PAUSED" if s.get("state") == "PAUSED" else "IDLE",
                                   last_progress_at=now, turns=int(s.get("turns") or 0) + 1)
            self.db.add_event(sid, {"completed": "completed", "interrupted": "interrupted"}.get(st, "failed"),
                              f"turn {st}")
            self._signal(sid, "done" if st == "completed" else ("failed" if st == "failed" else "stopped"),
                         s.get("last_summary") or "")
        elif method == "item/completed":
            item = p.get("item") or {}
            t = item.get("type")
            if t == "agentMessage":
                text = summary(item.get("text") or "", 400)
                self.db.upsert_session(sid, last_summary=text, last_activity=text, last_progress_at=now)
                self.db.add_event(sid, "progress", text)
            elif t == "commandExecution":
                self.db.upsert_session(sid, last_activity=clean(f"ran: {item.get('command')}", 200),
                                       last_progress_at=now)
                self.db.add_event(sid, "progress", clean(f"ran: {item.get('command')}", 200))
            elif t == "fileChange":
                files = [str(c.get("path") or "") for c in item.get("changes") or [] if isinstance(c, dict)]
                s = self.db.session(sid) or {}
                self.db.upsert_session(sid, files=((s.get("files") or []) + files)[-20:], last_progress_at=now)
                self.db.add_event(sid, "progress", "changed: " + ", ".join(Path(f).name for f in files[:5]))
        elif method == "thread/status/changed":
            st, waiting = codex.thread_state(p.get("status"))
            s = self.db.session(sid) or {}
            if s.get("state") != "PAUSED" or st != "IDLE":
                self.db.upsert_session(sid, state=st, waiting_for=waiting, last_seen_at=now)
        elif method == "thread/tokenUsage/updated":
            tot = (p.get("tokenUsage") or {}).get("total") or {}
            self.db.upsert_session(sid, tokens_total=int(tot.get("totalTokens") or 0))
        elif method == "error":
            err = p.get("error") or {}
            self.db.add_event(sid, "error", clean(err.get("message") if isinstance(err, dict) else err, 200))
        with self._cv:
            self._cv.notify_all()

    def _codex_request(self, rpc_id, method: str, p: dict) -> None:
        sid = self._codex_sid(p)
        if method in codex.APPROVAL_METHODS:
            threading.Thread(target=self._codex_approval, args=(rpc_id, method, sid, p), daemon=True,
                             name="agents-codex-approval").start()
        else:                                                   # user-input / elicitation: not answered by JARVES
            self.db.add_event(sid, "need_input", f"Codex asks for input ({method}); answer it in Codex")
            self._signal(sid, "blocked", "needs input")
            try:
                self.codex.respond(rpc_id, {"decision": "cancel"} if "Approval" in method else {})
            except (codex.RpcError, AttributeError, OSError):
                pass

    def _codex_approval(self, rpc_id, method: str, sid: str, p: dict) -> None:
        s = self.db.session(sid) or {}
        command = p.get("command")
        command = " ".join(command) if isinstance(command, list) else str(command or "")
        tool = "fileChange" if "fileChange" in method or "Patch" in method else "commandExecution"
        rid = f"codex-{p.get('itemId') or uuid.uuid4().hex[:8]}"
        decision = self._approval(s, rid, tool=tool, command=clean(command, 1000, keep_code=True),
                                  cwd=str(p.get("cwd") or s.get("cwd") or ""), reason=clean(p.get("reason") or "", 300),
                                  file_path=str(p.get("grantRoot") or ""))
        try:
            self._codex_server().respond(rpc_id, {"decision": "accept" if decision == "allow" else "decline"})
        except (codex.RpcError, LookupError, OSError):
            pass

    def _codex_exit(self, rc: int) -> None:
        for sid, m in list(self.managed.items()):
            if isinstance(m, _CodexThread):
                self.managed.pop(sid, None)
                self.db.upsert_session(sid, state="DISCONNECTED", last_seen_at=self.clock())
                self.db.add_event(sid, "disconnected", f"codex app-server exited ({rc})")
        self.codex = None

    # ================================================================== approvals
    def _approval(self, s: dict, request_id: str, *, tool: str, command: str, cwd: str, reason: str = "",
                  file_path: str = "", preview: str = "") -> str:
        """A worker's approval request -> the owner's popup. Returns "allow" / "deny" (the owner decided) or ""
        (nobody decided: timeout / no screen -> the provider's own prompt stays the fallback)."""
        sid = s.get("id") or ""
        wt = s.get("worktree") or s.get("project_root") or s.get("cwd") or ""
        risk, reasons = policy.classify(tool, command, cwd, wt, file_path)
        self.db.add_approval(request_id, sid, provider=s.get("provider"), tool=tool, command=command[:1000], cwd=cwd,
                             risk=risk)
        self.db.upsert_session(sid, state="WAITING_APPROVAL", waiting_for="approval", last_seen_at=self.clock())
        self.db.add_event(sid, "approval_requested", clean(f"{tool}: {command or file_path or preview}", 200))
        self._signal(sid, "approval", f"{tool}: {command[:120]}")
        pn = PROVIDER_NAMES.get(s.get("provider") or "", ("agent",))[0]
        prompt = (f"{pn} in {s.get('project') or Path(cwd).name} asks to run {tool}:\n{command or file_path or preview}"
                  + (f"\nWhy: {reason}" if reason else "") + (f"\nRisk: {', '.join(reasons)}" if reasons else ""))
        state, aid = self._ask_owner_state(prompt, title=f"agent_{tool}", risk="high" if risk == "high" else "medium",
                                           voice=False, typed=risk == "high", kind="shell" if command else "tool",
                                           timeout=float(self.acfg.permission_wait_s),
                                           details={"provider": s.get("provider"), "project": s.get("project"),
                                                    "command": command[:300], "request": request_id})
        if aid:
            self.db.add_approval(request_id, sid, approval_id=aid)
        decision = "allow" if state == "approved" else ("deny" if state == "denied" else "")
        self.db.decide_approval(request_id, decision or state, channel=state)
        self.db.audit("owner" if decision else "nobody", "approval", session_id=sid, request_id=request_id,
                      decision=decision or state, result=tool, detail=command[:300])
        cur = self.db.session(sid) or {}
        if cur.get("state") == "WAITING_APPROVAL":
            self.db.upsert_session(sid, state="RUNNING", waiting_for="")
        self.db.add_event(sid, "approval_decision", f"{decision or state}: {tool}")
        return decision

    def approvals_list(self) -> dict:
        from .. import approvals as appr
        pend = {a.get("details", {}).get("request"): a for a in appr.read_pending_file(self.home)}
        svc = self.approvals or appr.current()
        if svc is not None:
            pend.update({a["details"].get("request"): a for a in svc.pending() if a.get("details")})
        rows = []
        for r in self.db.approvals("pending"):
            a = pend.get(r["request_id"])
            rows.append({**r, "approval_id": (a or {}).get("id") or r.get("approval_id"),
                         "typed": bool((a or {}).get("typed")), "seconds_left": (a or {}).get("seconds_left")})
        return {"ok": True, "approvals": rows, "recent": self.db.approvals(None)[:20]}

    def decide(self, request_id: str, approve: bool, *, channel: str = "cli", code: str = "") -> dict:
        """The owner answers a worker approval from the CLI / dashboard: goes through the approvals service like
        every other answer (refused from inside a tool, typed code checked)."""
        from .. import approvals as appr
        r = self.db.approval(request_id)
        if r is None or r["state"] != "pending":
            return {"ok": False, "code": "NO_SUCH_SESSION", "text": "no such waiting approval"}
        aid = r.get("approval_id") or next((a["id"] for a in appr.read_pending_file(self.home)
                                            if (a.get("details") or {}).get("request") == request_id), "")
        if not aid:
            return {"ok": False, "code": "EXPIRED", "text": say("EXPIRED")}
        ok, why = appr.submit_answer(self.home, aid, approve, channel=channel, typed=code)
        return {"ok": ok, "code": "ok" if ok else "NOT_APPROVED", "text": "sent" if ok else why}

    # ================================================================== bridge handler (plugin processes)
    def _session_for_conn(self, ancestors, cwd: str) -> dict | None:
        """The live Claude session a plugin process belongs to: its pid is among the process's ancestors (else, one
        live session in that folder)."""
        rows = [s for s in self.db.sessions() if s["provider"] == "claude" and s["state"] in LIVE_STATES]
        anc = {int(a) for a in (ancestors or []) if str(a).isdigit() or isinstance(a, int)}
        if anc:
            by_pid = [s for s in rows if s.get("pid") and int(s["pid"]) in anc]
            if len(by_pid) == 1:
                return by_pid[0]
        by_cwd = [s for s in rows if policy.norm(s.get("cwd")) == policy.norm(cwd)]
        return by_cwd[0] if len(by_cwd) == 1 else None

    def _handle(self, conn: br.Conn, msg: dict) -> dict | None:
        op = str(msg.get("op") or "")
        if op == "hook":
            return self._op_hook(conn, msg)
        if op == "hello":
            return self._op_hello(conn, msg)
        if op == "ack":
            n = self.db.ack([str(x) for x in msg.get("message_ids") or []], delivery=str(msg.get("delivery") or ""))
            return {"ok": True, "acked": n}
        if op == "tool":
            return self._op_tool(conn, msg)
        if op == "permission_relay":
            return self._op_relay(conn, msg)
        if op == "call":
            return self.call(str(msg.get("name") or ""), **(msg.get("args") or {}))
        if op == "ping":
            return {"ok": True, "pong": True}
        return {"ok": False, "error": "unknown op"}

    def _op_hello(self, conn: br.Conn, msg: dict) -> dict:
        conn.role = str(msg.get("role") or "")
        conn.meta = {"ancestors": list(msg.get("ancestors") or [msg.get("ppid")]), "cwd": str(msg.get("cwd") or ""),
                     "channel_live": bool(msg.get("channel_live")), "relay": bool(msg.get("relay"))}
        if conn.role != "channel":
            return {"ok": True}
        s = self._session_for_conn(conn.meta["ancestors"], conn.meta["cwd"])
        if s is None:
            self.refresh(force=True)
            s = self._session_for_conn(conn.meta["ancestors"], conn.meta["cwd"])
        if s is None:
            conn.meta["pending_link"] = True
            return {"ok": True, "session_id": ""}
        self._link_channel(conn, s)
        return {"ok": True, "session_id": s["id"]}

    def _link_channel(self, conn: br.Conn, s: dict) -> None:
        conn.session_id = s["id"]
        conn.meta.pop("pending_link", None)
        self.channels[s["id"]] = conn
        self.db.upsert_session(s["id"], plugin=1, channel=1 if conn.meta.get("channel_live") else 0)
        self.db.add_event(s["id"], "channel", "live channel connected" if conn.meta.get("channel_live") else
                          "JARVES plugin tools connected (no live channel: messages wait for the next stop)")
        if conn.meta.get("channel_live"):                  # deliver what was queued while it was away
            rows = self.db.claim(s["id"], f"channel-{id(conn)}")
            if rows:
                conn.push({"push": "message", "content": wrap_for_agent(rows),
                           "message_ids": [r["message_id"] for r in rows],
                           "meta": {"from": "jarves", "correlation_id": rows[-1].get("correlation_id") or "",
                                    "kind": "queued"}})

    def _conn_closed(self, conn: br.Conn) -> None:
        if conn.session_id and self.channels.get(conn.session_id) is conn:
            self.channels.pop(conn.session_id, None)
            try:
                self.db.upsert_session(conn.session_id, channel=0)
            except Exception:
                pass

    def _op_hook(self, conn: br.Conn, msg: dict) -> dict:
        try:
            env = validate(msg.get("envelope") or {})
        except EnvelopeError as e:
            return {"ok": False, "error": str(e)}
        return self._ingest(env, ancestors=list(msg.get("ancestors") or [msg.get("ppid")]),
                            event=str(msg.get("event") or ""), conn=conn)

    def _spoofed(self, env: Envelope, ancestors, cwd: str) -> bool:
        """A hook may only speak for the session that runs it: its parent pid (when the registry knows the pid)
        and its working folder must match that session."""
        anc = {int(a) for a in ancestors or [] if isinstance(a, int) or str(a).isdigit()}
        s = self.db.session(env.session_id)
        if s is None or not s.get("pid") or not anc or int(s["pid"]) in anc:
            return False
        # the pid differs: ask Claude Code itself (`claude agents --json`) which process runs that session now (a
        # resumed session runs in a new process); only a confirmed mismatch is a spoof
        self.refresh(force=True)
        s = self.db.session(env.session_id) or {}
        if s.get("pid") and int(s["pid"]) in anc:
            return False
        others = [x for x in self.db.sessions() if x.get("pid") and int(x["pid"]) in anc
                  and x["id"] != env.session_id]
        return bool(others) or s.get("state") in LIVE_STATES

    def _ingest(self, env: Envelope, *, ancestors=None, event: str = "", conn: br.Conn | None = None,
                replay: bool = False) -> dict:
        """One agent event (hook / spool) into the registry + timeline; answers what the hook may print."""
        if env.provider != "claude" or not env.session_id.startswith("claude:"):
            return {"ok": False, "error": "bad session"}
        p = env.payload
        cwd = str(p.get("cwd") or "")
        if self._spoofed(env, ancestors, cwd):
            logs.warn("agents", "AGENTS_SPOOF_REFUSED", "an agent event named another session; refused")
            self.db.audit("agent", "spoof_refused", session_id=env.session_id, result="refused",
                          detail=f"event {event}")
            return {"ok": False, "error": "session mismatch", "code": "spoof"}
        dup = self.db.seen(env.message_id)
        now = self.clock()
        sid = env.session_id
        cur = self.db.session(sid) or {}
        fields: dict = {"last_seen_at": now if not replay else max(env.timestamp, cur.get("last_seen_at") or 0),
                        "plugin": 1, "status_source": "hook", "cwd": cwd or cur.get("cwd")}
        if cur.get("origin") is None:
            fields["origin"] = "manual"
        if fields.get("cwd"):
            self._place(fields)
        else:
            fields["project"], fields["project_root"] = cur.get("project") or "", cur.get("project_root") or ""
        k = env.kind
        text = p.get("summary") or p.get("text") or ""
        ev_kind = k
        st = None
        if k == "hello":
            st = "IDLE" if p.get("source") in ("startup", "clear", "resume") else cur.get("state") or "RUNNING"
            fields["started_at"] = cur.get("started_at") or env.timestamp
            fields["ended_at"] = None
            text = f"session {p.get('source') or 'started'}"
        elif k == "progress":
            st = "RUNNING"
            fields["last_progress_at"] = env.timestamp
            if p.get("files"):
                fields["files"] = ((cur.get("files") or []) + list(p["files"]))[-20:]
            if p.get("tool"):
                text = clean(f"{p['tool']}: " + (", ".join(Path(f).name for f in p.get("files") or []) or
                                                 p.get("command") or ""), 200)
                fields["last_activity"] = text
            elif p.get("status") == "prompt":
                fields["last_activity"] = clean(f"new task: {p.get('summary')}", 200)
        elif k == "need_input":
            nt = p.get("notification_type")
            st = "WAITING_APPROVAL" if nt == "permission_prompt" else "WAITING_INPUT"
            fields["waiting_for"] = {"permission_prompt": "approval", "idle_prompt": "a prompt"}.get(nt, "input")
            fields["blocker"] = clean(p.get("text") or "", 200) if nt != "idle_prompt" else ""
            if nt == "idle_prompt":
                st = "IDLE" if cur.get("state") != "PAUSED" else "PAUSED"
        elif k == "status_snapshot":
            st = cur.get("state") if cur.get("state") == "PAUSED" else "IDLE"
            fields["last_summary"] = clean(p.get("summary") or "", 400)
            fields["last_progress_at"] = env.timestamp
            fields["blocker"] = ""
            fields["waiting_for"] = ""
            fields["turns"] = int(cur.get("turns") or 0) + (0 if dup else 1)
        elif k == "completed":
            st = "IDLE"
        elif k == "error":
            fields["blocker"] = clean(f"{p.get('error')}: {p.get('text') or ''}", 200)
            st = "WAITING_INPUT" if p.get("error") in ("authentication_failed", "billing_error", "rate_limit") \
                else cur.get("state")
        elif k == "goodbye":
            st = "STOPPED"
            fields["ended_at"] = env.timestamp
            text = f"session ended ({p.get('reason') or 'exit'})"
        elif k == "approval_requested":
            st = "WAITING_APPROVAL"
        managed = sid in self.managed or cur.get("origin") == "managed"
        if managed:                  # its stream events are the precise source; the plugin's hooks only add approvals
            fields = {k: v for k, v in fields.items() if k in ("last_seen_at", "plugin")}
            st = "WAITING_APPROVAL" if k == "approval_requested" else None
        if st:
            fields["state"] = st
        self.db.upsert_session(sid, **fields)
        if managed and k != "approval_requested":
            dup = True                                       # no duplicate timeline rows
        if not dup:
            self.db.add_event(sid, ev_kind, clean(text, 300), {"event": event}, message_id=env.message_id,
                              ts=env.timestamp)
        self._link_pending_channels()
        with self._cv:
            self._cv.notify_all()
        out: dict = {"ok": True, "dup": dup}
        if replay:
            return out
        if k == "hello" and fields.get("project"):
            out["context"] = CHANNEL_CONTEXT
        if k == "status_snapshot" and p.get("status") == "turn_done":
            self._signal(sid, "done", fields.get("last_summary") or "")
        if k == "need_input" and p.get("notification_type") not in ("idle_prompt",):
            self._signal(sid, "blocked", fields.get("blocker") or "")
        if event in ("Stop", "UserPromptSubmit") and fields.get("project"):
            rows = self.db.claim(sid, f"hook-{(ancestors or ['x'])[0]}")
            if rows:
                self.db.ack([r["message_id"] for r in rows],
                            delivery="stop_hook" if event == "Stop" else "prompt_hook")
                out["deliver"] = wrap_for_agent(rows)
                if event == "Stop":                          # a Stop block makes the agent take one more turn
                    self.db.bump("turn")
                self.db.add_event(sid, "delivered", f"{len(rows)} owner message(s) delivered at "
                                                    f"{'the end of its turn' if event == 'Stop' else 'the next prompt'}")
        if k == "approval_requested" and not dup:
            s = self.db.session(sid) or {}
            if not s.get("project"):
                return out                                   # not allow-listed: its own terminal dialog decides
            decision = self._approval(s, env.request_id or env.message_id[:16], tool=str(p.get("tool_name") or ""),
                                      command=str(p.get("command") or ""), cwd=cwd,
                                      file_path=(p.get("files") or [""])[0], preview=str(p.get("input_preview") or ""))
            if decision:
                out["decision"] = decision
        return out

    def _link_pending_channels(self) -> None:
        if self.bridge is None:
            return
        for c in list(self.bridge.conns):
            if c.role == "channel" and c.meta.get("pending_link") and not c.closed:
                s = self._session_for_conn(c.meta.get("ancestors"), c.meta.get("cwd") or "")
                if s is not None:
                    self._link_channel(c, s)

    def _op_tool(self, conn: br.Conn, msg: dict) -> dict:
        """A JARVES MCP tool called by the agent (untrusted text in, cleaned)."""
        s = self.db.session(conn.session_id) if conn.session_id else \
            self._session_for_conn(msg.get("ancestors") or [msg.get("ppid")], str(msg.get("cwd") or ""))
        if s is None:
            return {"ok": False, "text": "JARVES does not know this session yet; nothing was sent."}
        tool, a = str(msg.get("tool") or ""), msg.get("args") or {}
        now = self.clock()
        if tool == "jarves_reply":
            cid = re.sub(r"[^A-Za-z0-9_-]", "", str(a.get("correlation_id") or ""))[:40]
            req = [r for r in self.db.pending() + self.db._rows(
                "SELECT * FROM messages WHERE direction = 'out' AND correlation_id = ?", (cid,))]
            if not cid or not any(r.get("session_id") == s["id"] for r in req):
                self.db.audit("agent", "reply_refused", session_id=s["id"], request_id=cid, result="unknown id")
                return {"ok": False, "text": "unknown correlation_id for this session"}
            env = make("status_response", provider="claude", session_id=s["id"], correlation_id=cid, origin="agent",
                       payload={"reply": clean(a.get("text") or "", 1200), "status": str(a.get("status") or "")})
            self.db.enqueue(env, direction="in")
            self.db.upsert_session(s["id"], last_summary=clean(a.get("text") or "", 400), last_progress_at=now)
            self.db.add_event(s["id"], "reply", summary(a.get("text") or "", 300), message_id=env.message_id)
            with self._cv:
                self._cv.notify_all()
            self._signal(s["id"], "reply", a.get("text") or "")
            return {"ok": True, "text": "delivered to JARVES"}
        if tool == "jarves_report_status":
            st = str(a.get("status") or "working")
            fields = {"last_summary": clean(a.get("summary") or "", 400), "last_progress_at": now,
                      "blocker": clean(a.get("blocker") or "", 200), "next_step": clean(a.get("next_step") or "", 200)}
            if a.get("files"):
                fields["files"] = [str(f)[:300] for f in list(a["files"])[:20]]
            self.db.upsert_session(s["id"], **fields)
            self.db.add_event(s["id"], "status_report", summary(a.get("summary") or "", 300),
                              {"status": st, "test_result": clean(a.get("test_result") or "", 200)})
            self._signal(s["id"], {"blocked": "blocked", "done": "done", "failed": "failed",
                                   "needs_input": "blocked"}.get(st, "progress"), a.get("summary") or "")
            return {"ok": True, "text": "status noted"}
        if tool == "jarves_heartbeat":
            self.db.upsert_session(s["id"], last_seen_at=now)
            return {"ok": True, "text": "ok"}
        if tool == "jarves_inbox":
            rows = self.db.claim(s["id"], f"inbox-{id(conn)}")
            if not rows:
                return {"ok": True, "text": "no messages"}
            self.db.ack([r["message_id"] for r in rows], delivery="inbox")
            return {"ok": True, "text": wrap_for_agent(rows)}
        return {"ok": False, "text": "unknown tool"}

    def _op_relay(self, conn: br.Conn, msg: dict) -> dict:
        if not self.acfg.channel_permission_relay or not conn.session_id:
            return {"ok": True, "decision": ""}
        rid = str(msg.get("request_id") or "")
        if not re.fullmatch(r"[a-km-z]{5}", rid):
            return {"ok": False, "error": "bad request id"}
        s = self.db.session(conn.session_id) or {}
        d = self._approval(s, f"cc-{rid}", tool=str(msg.get("tool_name") or ""),
                           command=clean(msg.get("input_preview") or "", 1000, keep_code=True),
                           cwd=s.get("cwd") or "", reason=clean(msg.get("description") or "", 300))
        return {"ok": True, "decision": d}

    # ================================================================== owner signals (UC5)
    def _signal(self, sid: str, kind: str, text: str) -> None:
        if self.notify is None or kind not in NOTIFY_KINDS + ("reply",):
            return
        s = self.db.session(sid) or {}
        if not s.get("project"):
            return
        proj = self.projects.for_path(s.get("cwd"))
        allowed = proj.notify if proj is not None else NOTIFY_KINDS
        if kind != "reply" and kind not in allowed:
            return
        pn_en, pn_he = PROVIDER_NAMES.get(s.get("provider") or "", ("agent", "סוכן"))
        project = s.get("project")
        en = {"approval": f"{pn_en} in {project} needs your approval.", "blocked": f"{pn_en} in {project} is blocked.",
              "done": f"{pn_en} in {project} finished its turn.", "failed": f"{pn_en} in {project} failed.",
              "reply": f"{pn_en} in {project} replied."}[kind]
        he = {"approval": f"{pn_he} בפרויקט {project} מחכה לאישור שלך.", "blocked": f"{pn_he} בפרויקט {project} תקוע.",
              "done": f"{pn_he} בפרויקט {project} סיים את התור.", "failed": f"{pn_he} בפרויקט {project} נכשל.",
              "reply": f"{pn_he} בפרויקט {project} ענה."}[kind]
        try:
            self.notify(en, he, kind)
        except Exception:
            logs.warn("agents", "AGENTS_NOTIFY_FAILED", "owner notification failed")

    # ================================================================== digest / log / doctor
    def digest(self, since: float | None = None, lang: str = "en") -> dict:
        self.refresh()
        now = self.clock()
        if since is None:
            try:
                since = float(self.db.get_meta("owner_digest_at") or 0) or now - 8 * 3600
            except ValueError:
                since = now - 8 * 3600
        rows = [s for s in self.db.sessions() if self._visible(s) and
                (s["state"] in LIVE_STATES or (s.get("last_progress_at") or 0) >= since)]
        items = []
        for s in rows:
            evs = [e for e in self.db.events(s["id"], 50) if e["ts"] >= since]
            prog = [e for e in evs if e["kind"] in ("progress", "completed", "status_report", "reply", "status_snapshot")]
            blocked = s["state"] in ("WAITING_APPROVAL", "WAITING_INPUT") or bool(s.get("blocker"))
            items.append({"session": self.view(s, lang), "events": len(evs), "progress": len(prog),
                          "blocked": blocked, "last": summary((prog[-1]["summary"] if prog else "") or
                                                              s.get("last_summary") or "", 200)})
        blocked = [i for i in items if i["blocked"]]
        if lang == "he":
            head = f"{len(items)} סשנים פעילים או שהתקדמו" + (f", {len(blocked)} חסומים" if blocked else ", אף אחד לא חסום")
            lines = [f"{PROVIDER_NAMES[i['session']['provider']][1]} ב-{i['session']['project']}: "
                     f"{i['session']['state_text']}" + (f" — {i['last']}" if i["last"] else "") for i in items]
        else:
            head = f"{len(items)} session(s) active or with progress" + (f", {len(blocked)} blocked" if blocked
                                                                        else ", none blocked")
            lines = [f"{PROVIDER_NAMES[i['session']['provider']][0]} in {i['session']['project']}: "
                     f"{i['session']['state_text']}" + (f" — {i['last']}" if i["last"] else "") for i in items]
        self.db.set_meta("owner_digest_at", str(now))
        return {"ok": True, "since": since, "items": items, "blocked": [i["session"]["project"] for i in blocked],
                "text": head + (": " + "; ".join(lines[:6]) if lines else "") + "."}

    def log(self, ref: str = "", limit: int = 100, lang: str = "en") -> dict:
        if ref:
            s, err = self._resolved(ref, lang)
            if err:
                return err
            return {"ok": True, "session": self.view(s, lang), "events": self.db.events(s["id"], limit),
                    "audit": self.db.audit_rows(s["id"], limit)}
        return {"ok": True, "audit": self.db.audit_rows(None, limit)}

    def doctor(self) -> dict:
        rows = []
        cv = claude.version(self.claude_cmd) if self.claude_cmd else ""
        rows.append({"check": "claude_cli", "ok": bool(cv), "detail": cv or "not found (agents.claude_command)"})
        if self.claude_cmd:
            inst, err = claude.plugin_installed(self.claude_cmd)
            rows.append({"check": "claude_plugin", "ok": bool(inst),
                         "detail": "installed" if inst else (err or "not installed: jarvis agents install-plugin claude")})
            live, err = claude.list_live(self.claude_cmd)
            rows.append({"check": "claude_agents_json", "ok": live is not None,
                         "detail": f"{len(live)} live session(s)" if live is not None else err})
        xv = codex.version(self.codex_cmd) if self.codex_cmd else ""
        rows.append({"check": "codex_cli", "ok": bool(xv), "detail": xv or "not found (agents.codex_command)"})
        if self.codex_cmd and self.acfg.codex_observe:
            try:
                srv = self._codex_server()
                acc = srv.account
                rows.append({"check": "codex_auth", "ok": acc.get("type") == "chatgpt",
                             "detail": f"{acc.get('type') or 'signed out'}" + (f" ({acc.get('plan')})" if acc.get("plan")
                                                                               else "")})
                rows.append({"check": "codex_daemon", "ok": True,
                             "detail": "shared daemon" if srv.argv[-1] == "proxy" else "own app-server (stdio)"})
            except (LookupError, codex.RpcError, OSError) as e:
                rows.append({"check": "codex_auth", "ok": False, "detail": clean(e, 120)})
        if self._opencode_on():
            r = self.opencode_op("status")
            rows.append({"check": "opencode_server", "ok": r["ok"], "detail": (r.get("health") or {}).get("version")
                         or f"{r.get('code')}: {r.get('detail', '')}"})
            pr = self.opencode_profile()
            rows.append({"check": "opencode_model", "ok": pr["ok"], "detail": f"{pr['model']} ({pr['kind']})"
                         + ("" if pr["ok"] else ": not allowed, see agents.opencode_allow_paid")})
        view = br.safe_view(self.home)
        rows.append({"check": "bridge", "ok": self.bridge is not None or br.try_request(self.home, "ping", 5) is not None,
                     "detail": f"{view['transport']} {view['address']}"})
        projs = self.projects.load()
        rows.append({"check": "projects", "ok": bool(projs), "detail": ", ".join(p.name for p in projs) or
                     "none: jarvis agents projects add <folder>"})
        return {"ok": all(r["ok"] for r in rows if r["check"] in ("bridge", "projects")), "checks": rows}

    # ================================================================== projects / plugin
    def projects_op(self, action: str, path: str = "", name: str = "", aliases: list[str] | None = None,
                    no_start: bool = False) -> dict:
        if action == "add":
            try:
                p = self.projects.add(path, name, aliases, allow_start=not no_start)
            except ValueError as e:
                return {"ok": False, "text": str(e)}
            self.db.audit("owner", "project_add", detail=p.path)
            for s in self.db.sessions():
                f: dict = {"cwd": s.get("cwd")}
                self._place(f)
                if f["project"] != (s.get("project") or ""):
                    self.db.upsert_session(s["id"], project=f["project"], project_root=f["project_root"])
            return {"ok": True, "project": {"name": p.name, "path": p.path}}
        if action == "remove":
            ok = self.projects.remove(name or path)
            self.db.audit("owner", "project_remove", detail=name or path)
            return {"ok": ok}
        return {"ok": True, "projects": [{"name": p.name, "path": p.path, "aliases": list(p.aliases),
                                          "allow_start": p.allow_start, "notify": list(p.notify)}
                                         for p in self.projects.load()]}

    def install_plugin(self, provider: str, *, approved: bool = False, uninstall: bool = False) -> dict:
        provider = PROVIDER_WORDS.get(provider.lower(), provider.lower())
        if provider == "codex":
            return {"ok": True, "code": "ok", "text": "Codex needs no plugin: JARVES talks to the Codex App Server "
                    "directly (subscription sign-in in Codex is enough)."}
        if provider != "claude":
            return {"ok": False, "code": "PROVIDER_MISSING", "text": say("PROVIDER_MISSING")}
        if not self.claude_cmd:
            return {"ok": False, "code": "PROVIDER_MISSING", "text": say("PROVIDER_MISSING")}
        if not approved and not self._ask_owner(("Remove" if uninstall else "Install") + " the JARVES plugin in "
                                                "Claude Code (user scope)?", title="agents_install_plugin",
                                                kind="install", risk="medium", voice=False):
            return {"ok": False, "code": "NOT_APPROVED", "text": say("NOT_APPROVED")}
        r = claude.uninstall_plugin(self.claude_cmd, self.home) if uninstall else \
            claude.install_plugin(self.claude_cmd, self.home)
        self.db.audit("owner", "plugin_uninstall" if uninstall else "plugin_install", decision="approved",
                      result="ok" if r.get("ok") else "failed")
        return r

    # ================================================================== one entry point
    def call(self, op: str, **a) -> dict:
        lang = a.pop("lang", "en")
        by = a.pop("by", "owner")
        try:
            if op == "list":
                return self.list(refresh=a.get("refresh", True), project=a.get("project", ""),
                                 include_stopped=bool(a.get("all")), lang=lang, codex_too=a.get("codex", True),
                                 force=bool(a.get("force")))
            if op == "status":
                return self.status(a.get("ref", ""), lang=lang)
            if op == "ask":
                return self.ask(a.get("ref", ""), a.get("question", ""), live=a.get("live"),
                                wait_s=float(a.get("wait_s") or 0), by=by, lang=a.get("lang_override") or None)
            if op == "reply":
                return self.reply(a.get("correlation_id", ""))
            if op == "tell":
                return self.tell(a.get("ref", ""), a.get("text", ""), by=by)
            if op == "continue":
                return self.continue_(a.get("ref", ""), by=by, lang=lang)
            if op == "pause":
                return self.pause(a.get("ref", ""), by=by, lang=lang)
            if op == "interrupt":
                return self.interrupt(a.get("ref", ""), by=by, lang=lang)
            if op == "stop":
                return self.stop_session(a.get("ref", ""), by=by, approved=bool(a.get("approved")), lang=lang)
            if op == "start":
                return self.start_session(a.get("provider", ""), a.get("project", ""), a.get("task", ""), by=by,
                                          approved=bool(a.get("approved")), here=bool(a.get("here")), lang=lang,
                                          model=a.get("model", ""), env=a.get("env"))
            if op == "notify":                        # owner push for the workflow autopilot's away alerts
                if self.notify is None:
                    return {"ok": False, "code": "error", "text": "No owner notification channel."}
                text = str(a.get("text") or "")[:500]
                self.notify(text, text, str(a.get("kind") or "autopilot")[:40])
                return {"ok": True, "code": "ok"}
            if op == "find_worker":                   # the live autopilot session of one workflow agent (unconfirmed start)
                ref = str(a.get("ref") or "")
                for x in self.db.sessions():
                    m = x.get("meta") or {}
                    if ref and m.get("autopilot") and m.get("workflow_agent") == ref and x["state"] in LIVE_STATES:
                        return {"ok": True, "code": "ok", "session_id": x["id"]}
                return {"ok": True, "code": "ok", "session_id": ""}
            if op == "autopilot":
                return self.autopilot_op(a.get("action", "status"), by=by, project=a.get("project", ""),
                                         models=a.get("models"), days=a.get("days"),
                                         max_concurrent=a.get("max_concurrent"), lang=lang)
            if op == "resume":
                return self.resume(a.get("ref", ""), by=by, approved=bool(a.get("approved")), lang=lang)
            if op == "approvals":
                return self.approvals_list()
            if op in ("approve", "deny"):
                return self.decide(a.get("request", ""), op == "approve", channel=a.get("channel", "cli"),
                                   code=a.get("code", ""))
            if op == "log":
                return self.log(a.get("ref", ""), int(a.get("limit") or 100), lang=lang)
            if op == "digest":
                return self.digest(a.get("since"), lang=lang)
            if op == "doctor":
                return self.doctor()
            if op == "opencode":
                return self.opencode_op(a.get("action", "status"), int(a.get("limit") or 20), lang=lang)
            if op == "projects":
                return self.projects_op(a.get("action", "list"), a.get("path", ""), a.get("name", ""),
                                        a.get("aliases"), bool(a.get("no_start")))
            if op == "install_plugin":
                return self.install_plugin(a.get("provider", "claude"), approved=bool(a.get("approved")),
                                           uninstall=bool(a.get("uninstall")))
            if op == "session":
                s, err = self._resolved(a.get("ref", ""), lang)
                return err or {"ok": True, "session": self.view(s, lang), "events": self.db.events(s["id"], 200),
                               "audit": self.db.audit_rows(s["id"], 50)}
        except Exception as e:
            logs.error("agents", "AGENTS_OP_FAILED", "agents operation failed", exc=True, op=op,
                       error_type=type(e).__name__)
            return {"ok": False, "code": "error", "text": f"{op} failed ({type(e).__name__})"}
        return {"ok": False, "code": "error", "text": f"unknown operation {op!r}"}


class _OpenCodeSession:
    def __init__(self, session_id: str, directory: str) -> None:
        self.session_id = session_id
        self.directory = directory


class _CodexThread:
    def __init__(self, thread_id: str) -> None:
        self.thread_id = thread_id
        self.turn_id = ""
        self.busy = False
        self.done: set[str] = set()                    # turns already completed (events can beat the reply)


# ---------------------------------------------------------------------- reaching the right instance
_current: Orchestrator | None = None


def attach(o: Orchestrator) -> Orchestrator:
    global _current
    _current = o
    return o


def detach(o: Orchestrator | None = None) -> None:
    global _current
    if o is None or _current is o:
        _current = None


def current() -> Orchestrator | None:
    return _current


REMOTE_OPS = ("start", "autopilot", "resume", "tell", "continue", "pause", "interrupt", "stop", "ask", "reply", "approve", "deny",
              "approvals", "list", "status", "digest", "log", "doctor", "session", "install_plugin", "projects", "opencode")


def call(home: Path, cfg, op: str, terminal_ask=None, **args) -> dict:
    """The surfaces' single door: the in-process service when it serves this home, else the running Core over the
    bridge, else a local instance (read-only views, queueing for the plugin's next safe stop)."""
    o = _current
    if o is not None and o.home.resolve() == Path(home).resolve() and o.started:
        return o.call(op, **args)
    r = br.try_request(Path(home), "call", timeout=float(args.get("wait_s") or 0) + 240, name=op, args=args)
    if r is not None:
        r.pop("rid", None)
        return r
    local = Orchestrator(Path(home), cfg, serve=False)
    if terminal_ask is not None:
        local.terminal_ask = terminal_ask
    try:
        return local.call(op, **args)
    finally:
        local.db.close()
