"""JAR-295: the combined Dependabot patch bumps (#935 gradle wrapper-validation 6.4.1, #936 setup-uv 10.3.3) are in
place: every use of each action is pinned to one full commit SHA, no old pin is left, and the version comment next to
each pin names the version that SHA really is. Offline: reads the workflow files. On the next bump, update EXPECTED
with the SHA and version from the Dependabot PR."""
import re
from pathlib import Path

WF = Path(__file__).resolve().parents[2] / ".github" / "workflows"

EXPECTED = {
    "astral-sh/setup-uv": ("68559e99cc807c1eb7e9238fd43a15bba460eec2", "v10.3.3"),
    "gradle/actions/wrapper-validation": ("8da46b539cd8fe6ab27ca75128919a18d757604c", "v6.4.1"),
}


def _pins(action):
    pat = re.compile(rf"uses:\s*{re.escape(action)}@(\S+)(?:\s+#\s*(v[\d.]+))?")
    return [(f.name, m.group(1), m.group(2))
            for f in sorted(WF.glob("*.yml")) for m in pat.finditer(f.read_text(encoding="utf-8"))]


def test_actions_pinned_to_the_bumped_commit_sha_with_a_matching_version_comment():
    for action, (sha, version) in EXPECTED.items():
        assert re.fullmatch(r"[0-9a-f]{40}", sha)
        pins = _pins(action)
        assert pins, action
        assert not [(f, ref) for f, ref, _ in pins if ref != sha], (action, pins)
        assert not [(f, v) for f, _, v in pins if v != version], (action, pins)
