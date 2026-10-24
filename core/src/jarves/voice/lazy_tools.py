"""Lazy tool exposure (issue #71): the client-delegation backend call lists the everyday tools plus the tools of the
groups the user's words (or an earlier `tools_more` call) opened, instead of all ~130 tools. Execution never depends on
this list: the registry still checks permission, taint and approvals for any call, so hiding a tool only saves prompt.
Never hidden: approvals/safety (schedule_approve, proposal_decide, folder_grant_request, feature_set, forget*, quit,
end_conversation, mute, privacy tools). Server delegation and the text/plan routes keep the full list."""
from __future__ import annotations

import re

# group -> (tool names, EN+HE trigger words, one-line summary shown in the tools_more description)
GROUPS: dict[str, tuple[tuple[str, ...], str, str]] = {
    "goals": (("goal_add", "goal_show", "goal_checkin", "goal_step_add", "goal_step_done", "goal_status", "goal_progress",
               "goal_delete", "goal_plan_propose", "goal_plan_show", "goal_plan_run"),
              r"goal|plan\b|milestone|check-?in|target|יעד|מטרה|תוכנית|תכנית|צעד", "goals, steps, check-ins, goal plans"),
    "preferences": (("pref_suggest", "pref_confirm", "pref_decline", "pref_show", "pref_edit", "pref_forget"),
                    r"prefer|habit|routine|העדפ|הרגל|שגרה", "learned preferences"),
    "proactive": (("proactive_show", "proactive_dismiss"),
                  r"proactive|suggestion|nudge|reminder of|פרואקטיב|הצעות|הצעה", "proactive suggestions"),
    "development": (("dev_suggest", "dev_drafts", "dev_discard", "dev_status", "list_issues", "skill_propose",
                     "skills_generated_list"),
                    r"develop|improve yourself|new skill|draft|issue|bug|פיתוח|שפר|סקיל|באג|טיוטה", "self-development, drafts, issues, new skills"),
    "selfcheck": (("self_check_run", "self_check_suggestions", "self_check_decide", "self_check_ticket", "self_status",
                   "self_change", "self_connect", "self_undo", "health_summary", "health_ai_review_summary",
                   "health_remediate", "health_canary_run", "health_ai_review_run", "run_selftest", "run_speedtest"),
                  r"self-?check|self.?test|speed.?test|health|diagnos|canary|self status|change yourself|connect yourself"
                  r"|בדיק|בריאות|אבחון|תקין|בדיקה עצמית|תשנה את עצמך",
                  "self-check, health, selftest/speedtest, changing or connecting JARVES itself"),
}
TOOL_GROUP = {t: g for g, (names, _, _) in GROUPS.items() for t in names}
_PATTERNS = {g: re.compile(p, re.IGNORECASE) for g, (_, p, _) in GROUPS.items()}
DISCOVER = "tools_more"


def groups_for(text: str) -> set[str]:
    """Groups whose trigger words occur in the user's recent words."""
    return {g for g, p in _PATTERNS.items() if p.search(text or "")}


def discover_tool(hidden: list[str]) -> dict:
    lines = "; ".join(f"{g} = {GROUPS[g][2]}" for g in hidden)
    return {"type": "function", "name": DISCOVER, "strict": False,
            "description": "Some tools are hidden to keep the prompt small. If the user wants something none of your tools "
                           "does, call this once with the group, then use the new tools. Groups: " + lines + ".",
            "parameters": {"type": "object", "properties": {"group": {"type": "string", "enum": hidden}},
                           "required": ["group"]}}


def lazy_tools(registry, open_groups=frozenset()) -> list[dict]:
    """live_tools() minus the tools of closed groups, plus the tools_more tool when something is hidden."""
    from .live import live_tools
    full = live_tools(registry)
    present = {t["name"] for t in full}
    hidden = [g for g, (names, _, _) in GROUPS.items() if g not in open_groups and present & set(names)]
    out = [t for t in full if TOOL_GROUP.get(t["name"]) not in hidden]
    return out + [discover_tool(hidden)] if hidden else out


def open_group(registry, group: str, open_groups: set) -> str:
    """Handle a tools_more call: open the group, answer with the names now available."""
    if group not in GROUPS:
        return f"unknown group; choose one of: {', '.join(GROUPS)}"
    open_groups.add(group)
    names = [t.name for t in registry.tools() if TOOL_GROUP.get(t.name) == group]
    return "opened; tools now available: " + ", ".join(names)
