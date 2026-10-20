"""Hard guardrails that no switch, autonomy grant, prompt or generated Skill can lift (SW-V2 spec 6.7, 8).

- Calendar: NO event deletion (owner decision D05). A Skill whose tool looks like it deletes / cancels / clears /
  moves calendar data cannot even be registered (SkillRegistry.register calls `check_tool`), and every Google
  request passes `skills.google.calendar_guard`.
"""
from __future__ import annotations

import re

_CAL = re.compile(r"calendar|event|יומן|אירוע", re.I)
_DELETE = re.compile(r"delet|remov|cancel|clear|purge|erase|trash|destroy|wipe|move|drop|unsubscrib|מחק|בטל|הסר",
                     re.I)


def check_tool(skill_name: str, tool_name: str, description: str = "") -> str | None:
    """None = may be registered; else why it is refused."""
    name = f"{skill_name} {tool_name}"
    if _CAL.search(name) and _DELETE.search(tool_name):
        return f"tool {tool_name!r} would delete or remove calendar events: forbidden (owner decision D05)"
    return None
