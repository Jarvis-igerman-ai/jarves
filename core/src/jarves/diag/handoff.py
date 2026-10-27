"""Slice C of #336: hand an unexplained failure to a coding agent (Claude Code) AFTER the owner says yes, twice.

Beta, OFF by default (`self_diagnosis_handoff`, needs the coding-agent switch). The flow: after the self-check JARVES asks one
more yes/no question (hand it to a coding agent?) -> on yes the existing Orchestrator `start_session` runs with
its OWN approval card, which shows the exact brief, the project and the folder (a second, separate approval: one mis-heard
"yes" never starts an agent) -> the agent works in a fresh worktree under the Orchestrator's daily cost cap.
The brief is built ONLY from the redacted report (codes, key names, versions, the guide id), is scanned like a ticket (a
secret-, e-mail-, phone- or path-shaped string blocks the hand-off) and tells the agent to propose a patch and
never merge. The project comes from the setting `diag.handoff_project` (an allow-listed Orchestrator project); empty = the
hand-off says how to set it. Tests use a fake Orchestrator: no live agent, no spend."""
from __future__ import annotations

from .. import features as feat
from .. import logs
from ..calm import targets
from . import areas
from .diag_ticket import TicketBlocked, evidence_of, scan

FEATURE = "self_diagnosis_handoff"
PROVIDER = "claude"

TEXT = {
    "en": {"ask": "Hand this failure to a coding agent (Claude Code)?\n"
                  "It works in a separate copy of the code and proposes a fix as a patch (a commit with a test). It cannot merge or push; every other command asks your approval. "
                  "You will see the exact text and approve again before it starts.",
           "title": "Hand the failure to a coding agent?",
           "why": "Only a redacted report (codes and versions, no conversation text, no keys) is written into the task.",
           "no_project": "No project is set for the hand-off. Allow one with jarvis agents, then set it: "
                         "jarvis diagnose --handoff-project <project>.",
           "blocked": "The report still looked private, so nothing was handed over.",
           "no_agent": "The coding-agent service is not running (jarvis features on agent_orchestrator).",
           "started": "The coding agent started: ", "refused": "The coding agent was not started: "},
    "he": {"ask": "להעביר את הכשל לסוכן קוד (Claude Code)?\n"
                  "הוא עובד בעותק נפרד של הקוד ומציע תיקון כטלאי (קומיט עם בדיקה). הוא לא יכול למזג או לדחוף; כל פקודה אחרת מבקשת את אישורכם. "
                  "תראו את הטקסט המדויק ותאשרו שוב לפני שהוא מתחיל.",
           "title": "להעביר את הכשל לסוכן קוד?",
           "why": "נכתב למשימה רק דוח מצונזר (קודים וגרסאות, בלי טקסט שיחה ובלי מפתחות).",
           "no_project": "לא הוגדר פרויקט להעברה. מתירים פרויקט ב-jarvis agents ואז מגדירים: jarvis diagnose --handoff-project <project>.",
           "blocked": "הדוח עדיין נראה פרטי, ולכן לא הועבר כלום.",
           "no_agent": "שירות סוכני הקוד לא פועל (jarvis features on agent_orchestrator).",
           "started": "סוכן הקוד התחיל: ", "refused": "סוכן הקוד לא התחיל: "},
}


MAX_BRIEF = 580       # the Orchestrator's approval card shows the first 600 characters: the owner must see ALL of it


def brief(rep: areas.DiagReport) -> str:
    """The task text for the agent, from the redacted report only, short enough that the approval card shows all of it.
    Raises TicketBlocked when the final scan finds private text."""
    ev = evidence_of(rep)
    codes = ev.get("last_log_codes") or []
    facts = f"area {rep.area}, code {rep.code}, os {ev.get('diag_os', '?')}, version {ev.get('jarves_version', '?')}"
    if codes:
        facts += ", recent log codes: " + "; ".join(str(c) for c in codes[:3])
    text = ("Rules first. Do not read, print or send tokens, keys or personal data. Do not merge or push. Task: find the cause in "
            "this repository and propose a fix as a patch (a commit with a test); if it is only a setup problem, say so and stop. "
            f"Context: JARVES self-diagnosis hand-off; a tool failed, the self-check named a cause and next step, and the owner "
            f"still wants a code look. Facts (redacted, no conversation text): {facts}.")
    bad = scan(text)
    if bad:
        raise TicketBlocked(bad)
    if len(text) > MAX_BRIEF:
        text = text[:MAX_BRIEF - 1] + "."
    return text


def start(orch, rep: areas.DiagReport, project: str, lang: str = "en") -> dict:
    """Blocking (the Orchestrator asks its own approval): run it in an executor. Never raises; returns {ok, text}."""
    s = TEXT["he" if lang == "he" else "en"]
    if not project:
        return {"ok": False, "code": "HANDOFF_NO_PROJECT", "text": s["no_project"]}
    if orch is None:
        return {"ok": False, "code": "HANDOFF_NO_AGENT", "text": s["no_agent"]}
    try:
        task = brief(rep)
    except TicketBlocked as e:
        logs.warn("diag", "DIAG_HANDOFF_BLOCKED", "hand-off brief blocked by the final scan", count=len(e.reasons))
        return {"ok": False, "code": "HANDOFF_BLOCKED", "text": s["blocked"]}
    try:
        r = orch.start_session(PROVIDER, project, task, by="diag", approved=False, lang=lang)
    except Exception:                                   # noqa: BLE001 - fixed words only
        logs.error("diag", "DIAG_HANDOFF_FAILED", "coding agent hand-off failed", exc=True)
        return {"ok": False, "code": "HANDOFF_FAILED", "text": s["refused"] + "error"}
    ok = bool(r.get("ok", True)) and r.get("code") not in ("NOT_APPROVED", "PROVIDER_MISSING", "NOT_ALLOWED_PROJECT")
    logs.info("diag", "DIAG_HANDOFF", "coding agent hand-off finished", area=rep.area, error_code=rep.code, ok=ok,
              outcome=str(r.get("code") or "started"))
    code = str(r.get("code") or "started")
    code = code if code.replace("_", "").isalnum() and len(code) <= 40 else "unknown"
    return {"ok": ok, "code": code, "text": (s["started"] if ok else s["refused"]) + code}      # fixed words: a git error may hold a path


def _probe(c: feat.ProbeCtx) -> tuple[bool, str]:
    return True, ""


feat.register(FEATURE, "העברת כשל לסוכן קוד", "Hand a failure to a coding agent", False, probe=_probe,
              needs=["self_diagnosis_ask", "agent_orchestrator"], risk="high",
              about="after the self-check, offer to hand a redacted report to a coding agent that "
                    "proposes a fix as a patch; two approvals, and it cannot merge or push",
              targets=targets("test_self_diagnosis_handoff.py"),
              permissions=["read", "starts a coding agent after two approvals"], settings="jarvis features on self_diagnosis_handoff")
