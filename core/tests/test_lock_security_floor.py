"""Security floors for locked dependencies: each advisory fix stays in core/uv.lock."""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from packaging.version import Version

LOCK = Path(__file__).resolve().parents[1] / "uv.lock"

# package name -> lowest patched version (advisory id). Add one row per advisory.
FLOORS = {
    "opentelemetry-api": ("1.45.2", "GHSA-mf5v-w3w3-f6cg"),  # #480, Dependabot alert 2
}


def _locked_versions(text: str | None = None) -> dict[str, list[str | None]]:
    """name -> locked versions; None marks an entry without a static version."""
    data = tomllib.loads(LOCK.read_text(encoding="utf-8") if text is None else text)
    found: dict[str, list[str | None]] = {}
    for pkg in data.get("package", []):
        found.setdefault(pkg["name"], []).append(pkg.get("version"))
    return found


def test_entry_without_version_is_reported_not_crash():
    lock = '[[package]]\nname = "ws"\n\n[[package]]\nname = "a"\nversion = "1.0"\n'
    assert _locked_versions(lock) == {"ws": [None], "a": ["1.0"]}


@pytest.mark.parametrize("name", sorted(FLOORS))
def test_locked_version_at_or_above_patched(name):
    floor, advisory = FLOORS[name]
    versions = _locked_versions().get(name)
    assert versions, f"{name} is no longer in uv.lock; drop its floor row ({advisory})"
    for v in versions:
        assert v is not None, f"{name} has a uv.lock entry with no version; cannot check {floor} ({advisory})"
        assert Version(v) >= Version(floor), f"{name} {v} in uv.lock is below {floor} ({advisory})"
