"""The committed Claude deny-list stays valid and keeps the scheduling/merge denies."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_claude_settings_deny_list():
    data = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8-sig"))
    deny = data["permissions"]["deny"]
    for rule in ("CronCreate", "CronDelete", "RemoteTrigger", "ScheduleWakeup", "Bash(gh pr merge*)"):
        assert rule in deny
