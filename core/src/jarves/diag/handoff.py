"""Slice C of #336: hand an unexplained failure to a coding agent (Claude Code) AFTER the owner says yes, twice.

Beta, OFF by default (`self_diagnosis_handoff`, needs the coding-agent switch). The flow: after the self-check JARVES asks one
more yes/no question (hand it to a coding agent?) -> on yes the existing Orchestrator `start_session` runs with
its OWN approval card, which shows the exact brief, the project and the folder (a second, separate approval: one mis-heard
"yes" never starts an agent) -> the agent works in a fresh worktree under the Orchestrator's daily cost cap.
The brief is built ONLY from the redacted report (codes, key names, versions, the guide id), is scanned like a ticket (a
secret-, e-mail-, phone- or path-shaped string blocks the hand-off) and tells the agent to propose a DRAFT pull request and
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
                  "It works in a separate copy of the code and proposes a fix as a draft pull request; nothing is merged. "
                  "You will see the exact text and approve again before it starts.",
           "title": "Hand the failure to a coding agent?",
           "why": "Only a redacted report (codes and versions, no conversation text, no keys) is written into the task.",
           "no_project": "No project is set for the hand-off. Allow one with jarvis agents, then set it: "
                         "jarvis settings diag.handoff_project <project>.",
           "blocked": "The report still looked private, so nothing was handed over.",
           "no_agent": "The coding-agent service is not running (jarvis features on agent_orchestrator).",
           "started": "The coding agent started: ", "refused": "The coding agent was not started: "},
    "he": {"ask": "להעביר את הכשל לסוכן קוד (Claude Code)?\n"
                  "הוא עובד בעותק נפרד של הקוד ומציע תיקון כבקשת משיכה בטיוטה; שום דבר לא ממוזג. "
                  "תראו את הטקסט המדויק ותאשרו שוב לפני שהוא מתחיל.",
           "title": "להעביר את הכשל לסוכן קוד?",
           "why": "נכתב למשימה רק דוח מצונזר (קודים וגרסאות, בלי טקסט שיחה ובלי מפתחות).",
           "no_project": "לא הוגדר פרויקט להעברה. מתירים פרויקט ב-jarvis agents ואז מגדירים: jarvis settings diag.handoff_project <project>.",
           "blocked": "הדוח עדיין נראה פרטי, ולכן לא הועבר כלום.",
           "no_agent": "שירות סוכני הקוד לא פועל (jarvis features on agent_orchestrator).",
           "started": "סוכן הקוד התחיל: ", "refused": "סוכן הקוד לא התחיל: "},
}


def brief(rep: areas.DiagReport) -> str:
    """The task text for the agent, from the redacted report only. Raises TicketBlocked when the final scan finds private text."""
    ev = evidence_of(rep)
    lines = [f"- {k}: {v}" for k, v in ev.items()]
    text = "\n".join([
        "Self-diagnosis hand-off from JARVES. A tool failed and the built-in self-check could not explain it.",
        f"Area: {rep.area}. Error code: {rep.code}. Confidence of the self-check: {rep.confidence}.",
        "Redacted evidence (key names, codes and versions only; no conversation text):", *lines,
        "Task: find the cause in this repository and propose a fix as a DRAFT pull request with a test. Never merge. "
        "Do not read, print or send tokens, keys or personal data. If this is a setup problem and not a bug, say so and stop."])
    bad = scan(text)
    if bad:
        raise TicketBlocked(bad)
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
    return {"ok": ok, "code": str(r.get("code") or "started"), "text": (s["started"] if ok else s["refused"]) + str(r.get("text") or "")[:200]}


def _probe(c: feat.ProbeCtx) -> tuple[bool, str]:
    return True, ""


feat.register(FEATURE, "העברת כשל לסוכן קוד", "Hand a failure to a coding agent", False, probe=_probe,
              needs=["self_diagnosis_ask", "agent_orchestrator"], risk="high",
              about="after the self-check, offer to hand a redacted report to a coding agent that "
                    "proposes a fix as a draft pull request; two approvals, nothing is merged",
              targets=targets("test_self_diagnosis_handoff.py"),
              permissions=["read", "starts a coding agent after two approvals"], settings="jarvis features on self_diagnosis_handoff")
