"""JAR-295: the combined Dependabot patch bumps (#935 gradle wrapper-validation 6.4.1, #936 setup-uv 10.3.3) are in
place, every use of each action is pinned to one full commit SHA, and no old pin is left behind. Offline: reads the
workflow files. On the next bump, update EXPECTED with the SHAs from the Dependabot PR."""
import re
from pathlib import Path

WF = Path(__file__).resolve().parents[2] / ".github" / "workflows"

EXPECTED = {
    "astral-sh/setup-uv": "68559e99cc807c1eb7e9238fd43a15bba460eec2",  # v10.3.3
    "gradle/actions/wrapper-validation": "8da46b539cd8fe6ab27ca75128919a18d757604c",  # v6.4.1
}


def _pins(action):
    pat = re.compile(rf"uses:\s*{re.escape(action)}@(\S+)")
    return [(f.name, m.group(1)) for f in sorted(WF.glob("*.yml")) for m in pat.finditer(f.read_text(encoding="utf-8"))]


def test_actions_pinned_to_the_bumped_commit_sha():
    for action, sha in EXPECTED.items():
        pins = _pins(action)
        assert pins, action
        bad = [(f, ref) for f, ref in pins if ref != sha]
        assert not bad, (action, bad)
        assert re.fullmatch(r"[0-9a-f]{40}", sha)
