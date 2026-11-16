"""Regression for #480 (GHSA-mf5v-w3w3-f6cg): the Core lockfile must not pin a
vulnerable opentelemetry-api. Hermetic: reads uv.lock only. Artifact digests
are not pinned here; `uv sync --locked` verifies them against the index."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

LOCK = Path(__file__).resolve().parents[1] / "uv.lock"
PATCHED = (1, 45, 2, 0)


def _release(raw: str) -> tuple[int, ...]:
    """Release tuple of a PEP 440 version (no third-party parser). A pre or dev
    release sorts below its final release, so it gets a trailing -1."""
    m = re.fullmatch(r"(\d+(?:\.\d+)*)([.\-_]?(?:a|b|rc|dev)\d*)?(\.post\d+)?(\+[A-Za-z0-9.]+)?", raw.strip())
    if not m:
        raise AssertionError(f"opentelemetry-api version {raw!r} in uv.lock is not PEP 440")
    parts = tuple(int(p) for p in m.group(1).split("."))
    parts = parts + (0,) * (3 - len(parts))
    return parts + ((-1,) if m.group(2) else (0,))


def _version(name: str) -> str:
    data = tomllib.loads(LOCK.read_text(encoding="utf-8"))
    matches = [p for p in data.get("package", []) if p.get("name") == name]
    assert len(matches) == 1, f"expected one {name} entry, got {len(matches)}"
    return matches[0]["version"]


def test_opentelemetry_api_at_or_above_patched_version() -> None:
    raw = _version("opentelemetry-api")
    assert _release(raw) >= PATCHED, f"opentelemetry-api {raw} is vulnerable (GHSA-mf5v-w3w3-f6cg)"


def test_release_parser_orders_versions() -> None:
    assert _release("1.45.1") < PATCHED <= _release("1.45.2")
    assert _release("1.46") > PATCHED
    assert _release("1.45.2rc1") < PATCHED
    assert _release("1.45.2.post1+local") >= PATCHED


def test_release_parser_rejects_malformed() -> None:
    with pytest.raises(AssertionError, match="not PEP 440"):
        _release("latest")
