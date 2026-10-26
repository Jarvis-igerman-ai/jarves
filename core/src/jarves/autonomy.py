"""Bounded autonomy for diagnostic proposals (SW-V2 WP-M3, spec 6.7, decision D11).

Self-diagnosis (diagnose.py) raises evidence-backed issues. `Autonomy.sync` turns the calibration-relevant ones
into PROPOSALS (memory.db `proposals`, one per issue fingerprint):

    false_wake (repeated)   -> raise voice.wake_threshold by 0.05 (config.toml, applies at the next Core start)
    tool_fail:<tool> (high) -> switch off the feature that owns the tool until it is fixed (medium risk)
    early_response          -> advice only (turn detection), nothing is changed automatically

What happens next depends on the autonomy level (Dashboard; default suggest_and_ask):
- observe_only      proposals are recorded ("observed") and shown on the dashboard; no alert, nothing applied.
- suggest_and_ask   recorded + one alert asking for approval; applied only after the user approves (dashboard, or
                    by voice: low-risk after a spoken yes, medium/high only with the on-screen confirmation).
- approved_low_risk additionally applies LOW-risk proposals whose applier the user explicitly granted
                    (autonomy.grants), with an audit entry and an alert afterwards; everything else is asked.
Every decision and change is in the audit log; an applied proposal can be reverted. Raising the level or adding
grants is dashboard-only (voice may only lower it). Appliers are a closed list: autonomy never calls Skills/tools,
so it can never delete calendar events or touch other hard guardrails.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

from . import logs
from .userdata import UserDB, ctx_db

LEVELS = ("observe_only", "suggest_and_ask", "approved_low_risk")
WAKE_STEP, WAKE_MAX = 0.05, 0.8
FALSE_WAKE_MIN = 2                 # occurrences before a wake-threshold proposal


@dataclass
class Applier:
    id: str
    risk: str
    about: str
    apply: Callable[["Autonomy", dict], tuple[str, dict]]      # -> (result text, revert data)
    revert: Callable[["Autonomy", dict], str]


def _set_toml(path: Path, section: str, key: str, literal: str) -> None:
    from .cli import set_toml_value                 # one TOML editor (keeps comments), shared with `jarvis wake`
    set_toml_value(path, section, key, literal)


def _wake_apply(a: "Autonomy", p: dict) -> tuple[str, dict]:
    path = a.home / "config.toml"
    old = float(a.cfg.voice.wake_threshold)
    new = round(min(WAKE_MAX, max(old, float(p.get("to", old + WAKE_STEP)))), 2)
    if new <= old:
        raise ValueError(f"wake threshold is already {old}")
    _set_toml(path, "voice", "wake_threshold", str(new))
    a.cfg.voice.wake_threshold = new
    return f"voice.wake_threshold {old} -> {new} (applies at the next Core start)", {"old": old, "new": new}


def _wake_revert(a: "Autonomy", r: dict) -> str:
    _set_toml(a.home / "config.toml", "voice", "wake_threshold", str(r["old"]))
    a.cfg.voice.wake_threshold = r["old"]
    return f"voice.wake_threshold back to {r['old']}"


def _feature_apply(a: "Autonomy", p: dict) -> tuple[str, dict]:
    if a.features is None:
        raise ValueError("feature switches unavailable")
    fid = p["feature"]
    before = a.features.enabled(fid)
    a.features.set(fid, False, by="autonomy")
    return f"feature {fid} switched off", {"feature": fid, "was": before}


def _feature_revert(a: "Autonomy", r: dict) -> str:
    a.features.set(r["feature"], bool(r["was"]), by="autonomy-revert")
    return f"feature {r['feature']} back {'on' if r['was'] else 'off'}"


def _config_apply(a: "Autonomy", p: dict) -> tuple[str, dict]:
    """Self-check suggestions: one allowlisted config.toml value (config_edit.EDITABLE, validated, backed up)."""
    from . import config_edit
    key = p["key"]
    if key not in config_edit.EDITABLE:
        raise ValueError(f"{key} is not an editable setting")
    sec, name = key.split(".", 1)
    old = getattr(getattr(a.cfg, sec), name)
    config_edit.apply(a.home / "config.toml", {key: p["value"]})
    return f"{key} {old} -> {p['value']} (applies at the next Core start)", {"key": key, "old": old}


def _config_revert(a: "Autonomy", r: dict) -> str:
    from . import config_edit
    config_edit.apply(a.home / "config.toml", {r["key"]: r["old"]})
    return f"{r['key']} back to {r['old']}"


def _feature_on_apply(a: "Autonomy", p: dict) -> tuple[str, dict]:
    """Self-check suggestions: switch on a useful feature (never a privacy-sensitive SAFE_OFF one); prerequisites are
    checked/installed first through the maintained setup path, like `jarvis features on`."""
    from .setup_wizard import SAFE_OFF
    fid = p["feature"]
    if a.features is None:
        raise ValueError("feature switches unavailable")
    if fid in SAFE_OFF:
        raise ValueError(f"{fid} is privacy-sensitive: switch it on yourself on the Features page")
    if a.features.locked(fid):
        from .features import BETA_MSG
        raise ValueError(BETA_MSG)
    from .feature_setup import prepare
    ok, detail = prepare(fid, a.features, a.home)
    if not ok:
        raise ValueError(detail)
    before = a.features.enabled(fid)
    a.features.set(fid, True, by="autonomy")
    return f"feature {fid} switched on", {"feature": fid, "was": before}


APPLIERS: dict[str, Applier] = {
    "wake_threshold": Applier("wake_threshold", "low", "raise the wake-word threshold after repeated false wakes",
                              _wake_apply, _wake_revert),
    "feature_off": Applier("feature_off", "medium", "switch off a feature whose tool keeps failing",
                           _feature_apply, _feature_revert),
    # self-check suggestions (selfcheck.py): medium risk, so never granted for automatic use
    "config_setting": Applier("config_setting", "medium", "change one dashboard-editable setting suggested by the "
                              "self-check", _config_apply, _config_revert),
    "feature_on": Applier("feature_on", "medium", "switch on a useful feature suggested by the self-check",
                          _feature_on_apply, _feature_revert),
}


class Autonomy:
    def __init__(self, db: UserDB, *, cfg, home: Path, features=None, alerts=None, clock=time.time) -> None:
        self.db, self.cfg, self.home, self.features, self.alerts, self.clock = db, cfg, Path(home), features, alerts, clock

    # ------------------------------------------------------------------ level
    def level(self) -> str:
        v = self.db.setting("autonomy.level")
        return v if v in LEVELS else "suggest_and_ask"

    def set_level(self, level: str, *, by: str) -> str:
        if level not in LEVELS:
            raise ValueError(f"level must be one of {LEVELS}")
        if LEVELS.index(level) > LEVELS.index(self.level()) and by not in ("dashboard", "cli", "user"):
            raise PermissionError("more autonomy can only be granted on the dashboard")
        self.db.set_setting("autonomy.level", level, actor=by)
        logs.info("autonomy", "AUTONOMY_LEVEL", "autonomy level set", autonomy=level, by=by)
        return level

    def grants(self) -> list[str]:
        return [g for g in (self.db.setting("autonomy.grants") or []) if g in APPLIERS]

    def set_grants(self, grants: list[str], *, by: str) -> list[str]:
        if by not in ("dashboard", "cli", "user"):
            raise PermissionError("autonomy grants are set on the dashboard")
        bad = [g for g in grants if g not in APPLIERS]
        if bad:
            raise ValueError(f"unknown appliers {bad}")
        if any(APPLIERS[g].risk != "low" for g in grants):
            raise ValueError("only low-risk appliers can be granted for automatic use")
        self.db.set_setting("autonomy.grants", sorted(set(grants)), actor=by)
        return self.grants()

    # ------------------------------------------------------------------ proposals
    def _rule(self, issue: dict) -> tuple[str | None, dict, str, str] | None:
        """(applier, params, title, detail) for a calibration-relevant issue, else None."""
        fp, kind = issue["fingerprint"], issue["kind"]
        if fp == "false_wake" and issue["count"] >= FALSE_WAKE_MIN:
            old = float(self.cfg.voice.wake_threshold)
            if old + WAKE_STEP > WAKE_MAX + 1e-9:
                return None
            new = round(old + WAKE_STEP, 2)
            return ("wake_threshold", {"from": old, "to": new},
                    f"להעלות את סף מילת ההפעלה מ-{old} ל-{new}",
                    f"{issue['count']} false wakes (a conversation opened and nobody spoke). A higher threshold "
                    "means fewer false wakes but \"Hey Jarvis\" must be clearer. Applies at the next start.")
        m = re.match(r"tool_fail:(.+)$", fp)
        if m and issue["severity"] == "high" and self.features is not None:
            fid = self.features.owner("", m.group(1))
            if fid and self.features.enabled(fid):
                return ("feature_off", {"feature": fid, "tool": m.group(1)},
                        f"לכבות זמנית את {fid} (הכלי {m.group(1)} נכשל שוב ושוב)",
                        f"{issue['title']}. Turning it off stops JARVES from promising what fails; switch it on "
                        "again on the Features page once fixed.")
        if kind == "voice" and fp == "early_response":
            return (None, {}, "תגובות מוקדמות מדי בזמן שאתה מדבר",
                    f"{issue['title']}. Advice only: nothing is changed automatically.")
        return None

    def sync(self, issues: list[dict]) -> list[dict]:
        """Create proposals for new calibration-relevant issues and act on them per the autonomy level."""
        made = []
        for issue in issues:
            if issue.get("status", "open") != "open":
                continue
            rule = self._rule(issue)
            if rule is None:
                continue
            applier, params, title, detail = rule
            fp = f"{issue['fingerprint']}:{json.dumps(params, sort_keys=True)}"
            if self.db.row("SELECT id FROM proposals WHERE fingerprint=?", (fp,)):
                continue
            risk = APPLIERS[applier].risk if applier else "low"
            level = self.level()
            status = "observed" if level == "observe_only" else "proposed"
            cur = self.db.x("INSERT INTO proposals (fingerprint, title, detail, evidence, applier, params, risk, "
                            "status, created) VALUES (?,?,?,?,?,?,?,?,?)",
                            (fp, title, detail, (issue.get("evidence") or "")[:2000], applier,
                             json.dumps(params), risk, status, self.clock()))
            pid = cur.lastrowid
            self.db.audit("autonomy", "proposal_new", pid, {"issue": issue["fingerprint"], "level": level})
            logs.info("autonomy", "PROPOSAL_NEW", "diagnostic proposal", proposal_id=pid, applier=applier,
                      autonomy=level)
            if level == "approved_low_risk" and applier and risk == "low" and applier in self.grants():
                self.decide(pid, True, by="autonomy")
                self._alert(pid, "low", f"שיניתי: {title}", "לפי ההרשאה שנתת. אפשר לבטל בלוח הבקרה.")
            elif level != "observe_only":
                self._alert(pid, "normal", f"הצעה לשיפור: {title}", "אשר או דחה בלוח הבקרה, או תגיד לי.")
            made.append(self.get(pid))
        return made

    def _alert(self, pid: int, priority: str, title: str, text: str) -> None:
        if self.alerts is not None:
            self.alerts.raise_("autonomy", title, text, priority=priority, kind="proposal",
                               dedupe_key=f"proposal:{pid}", ref=f"proposal:{pid}")

    def get(self, pid: int) -> dict | None:
        r = self.db.row("SELECT * FROM proposals WHERE id=?", (int(pid),))
        if r:
            r["params"] = json.loads(r["params"] or "{}")
            r["revert"] = json.loads(r["revert"]) if r["revert"] else None
        return r

    def proposals(self, status: str | None = None) -> list[dict]:
        rows = (self.db.rows("SELECT id FROM proposals WHERE status=? ORDER BY id DESC", (status,)) if status
                else self.db.rows("SELECT id FROM proposals ORDER BY id DESC LIMIT 200"))
        return [self.get(r["id"]) for r in rows]

    def decide(self, pid: int, approve: bool, *, by: str) -> dict:
        p = self.get(pid)
        if p is None:
            raise KeyError(pid)
        if p["status"] not in ("proposed", "observed", "failed"):
            raise ValueError(f"proposal is already {p['status']}")
        now = self.clock()
        if not approve:
            self.db.x("UPDATE proposals SET status='rejected', decided_at=?, decided_by=? WHERE id=?", (now, by, p["id"]))
            self.db.audit(by, "proposal_reject", p["id"])
            return self.get(p["id"])
        if not p["applier"]:
            self.db.x("UPDATE proposals SET status='applied', decided_at=?, decided_by=?, result=? WHERE id=?",
                      (now, by, "acknowledged (advice only)", p["id"]))
            self.db.audit(by, "proposal_ack", p["id"])
            return self.get(p["id"])
        try:
            result, revert = APPLIERS[p["applier"]].apply(self, p["params"])
            status = "applied"
        except Exception as e:
            result, revert, status = f"failed: {type(e).__name__}: {str(e)[:160]}", None, "failed"
        self.db.x("UPDATE proposals SET status=?, decided_at=?, decided_by=?, result=?, revert=? WHERE id=?",
                  (status, now, by, result, json.dumps(revert) if revert else None, p["id"]))
        self.db.audit(by, "proposal_apply", p["id"], {"applier": p["applier"], "result": result})
        logs.info("autonomy", "PROPOSAL_APPLIED" if status == "applied" else "PROPOSAL_FAILED", "proposal decided",
                  status=status, detail=result, proposal_id=p["id"], by=by)
        return self.get(p["id"])

    def revert(self, pid: int, *, by: str) -> dict:
        p = self.get(pid)
        if p is None:
            raise KeyError(pid)
        if p["status"] != "applied" or not p["revert"]:
            raise ValueError("only an applied change can be reverted")
        result = APPLIERS[p["applier"]].revert(self, p["revert"])
        self.db.x("UPDATE proposals SET status='reverted', result=? WHERE id=?", (result, p["id"]))
        self.db.audit(by, "proposal_revert", p["id"], {"result": result})
        return self.get(p["id"])


def open_issues(home: Path) -> list[dict]:
    """Open self-diagnosis issues from store.db (empty when history is off / no store yet)."""
    path = Path(home) / "store.db"
    if not path.exists():
        return []
    from .store import Store
    st = Store(path)
    try:
        return st.issues("open")
    finally:
        st.close()


# ---------------------------------------------------------------------- voice tools
class DecideArgs(BaseModel):
    proposal_id: int = Field(..., ge=1)
    approve: bool = Field(..., description="true only after the user clearly agreed")


def make_skill(auto: Autonomy, registry=None):
    from .skills import NoArgs, Skill, Tool, ToolResult

    class AutonomySkill(Skill):
        name = "autonomy"
        description = "self-improvement proposals from diagnostics (always asks)"

        def tools(self) -> list:
            async def list_(_):
                items = [{"id": p["id"], "title": p["title"], "detail": p["detail"], "risk": p["risk"],
                          "status": p["status"]} for p in auto.proposals("proposed")][:10]
                return ToolResult(True, f"autonomy {auto.level()}; {len(items)} open proposal(s)",
                                  {"level": auto.level(), "proposals": items})

            async def decide(a: DecideArgs):
                p = auto.get(a.proposal_id)
                if p is None:
                    return ToolResult(False, "no such proposal", code="PROPOSAL_NOT_FOUND")
                if a.approve and p["risk"] != "low":
                    confirm = getattr(registry, "confirm", None)
                    if not (confirm and await confirm(f"לאשר: {p['title']}?")):
                        return ToolResult(False, "this one needs approval on the screen or the dashboard",
                                          code="PROPOSAL_NEEDS_APPROVAL")
                try:
                    p = auto.decide(p["id"], a.approve, by="voice")
                except ValueError as e:
                    return ToolResult(False, str(e), code="PROPOSAL_STATE")
                return ToolResult(True, f"{p['status']}: {p['result'] or p['title']}",
                                  {"id": p["id"], "status": p["status"]})

            return [Tool("proposals_list", "Open self-improvement proposals and autonomy level: 'any proposals', 'יש הצעות לשיפור'.", list_, NoArgs, permission="read"),
                    Tool("proposal_decide", "Approve or reject one proposal, only after asking the user: 'approve it', 'תאשר את ההצעה'.", decide,
                         DecideArgs, permission="action")]

    return AutonomySkill()


ALLOWED_ACTIONS = {"proposal_decide"}


# ---------------------------------------------------------------------- dashboard
def _auto(ctx) -> Autonomy:
    a = getattr(ctx, "_autonomy", None)
    if a is None:
        from .features import _ctx_features
        from .alerts import AlertEngine
        db = ctx_db(ctx)
        a = Autonomy(db, cfg=ctx.cfg, home=ctx.home, features=_ctx_features(ctx), alerts=AlertEngine(db))
        ctx._autonomy = a
    return a


def api_autonomy(ctx, m, body):
    a = _auto(ctx)
    return 200, {"level": a.level(), "levels": LEVELS, "grants": a.grants(),
                 "appliers": [{"id": x.id, "risk": x.risk, "about": x.about} for x in APPLIERS.values()],
                 "proposals": a.proposals(), "audit": a.db.audit_log(100)}


def api_autonomy_set(ctx, m, body):
    a, b = _auto(ctx), body or {}
    try:
        if "level" in b:
            a.set_level(str(b["level"]), by="dashboard")
        if "grants" in b:
            a.set_grants(list(b["grants"] or []), by="dashboard")
    except (ValueError, PermissionError) as e:
        return 400, {"error": str(e)}
    return 200, {"level": a.level(), "grants": a.grants()}


def api_autonomy_sync(ctx, m, body):
    a = _auto(ctx)
    return 200, {"new": a.sync(open_issues(ctx.home))}


def api_proposal_op(ctx, m, body):
    a, op = _auto(ctx), (body or {}).get("op")
    try:
        if op in ("approve", "reject"):
            return 200, a.decide(int(m.group(1)), op == "approve", by="dashboard")
        if op == "revert":
            return 200, a.revert(int(m.group(1)), by="dashboard")
    except KeyError:
        return 404, {"error": "no such proposal"}
    except ValueError as e:
        return 400, {"error": str(e)}
    return 400, {"error": "op must be approve, reject or revert"}


def dashboard_routes(register) -> None:
    register("GET", r"/api/autonomy", api_autonomy)
    register("POST", r"/api/autonomy", api_autonomy_set)
    register("POST", r"/api/autonomy/sync", api_autonomy_sync)
    register("POST", r"/api/proposals/(\d+)", api_proposal_op)
