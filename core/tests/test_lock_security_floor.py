"""Regression for #480 (GHSA-mf5v-w3w3-f6cg): the Core lockfile must not pin a
vulnerable opentelemetry-api, and its hashes must be the PyPI-published ones."""

from __future__ import annotations

import tomllib
from pathlib import Path

LOCK = Path(__file__).resolve().parents[1] / "uv.lock"

# Digests checked against https://pypi.org/pypi/opentelemetry-api/1.45.2/json
# on 2026-11-16.
PYPI_SHA256 = {
    "opentelemetry_api-1.45.2.tar.gz": "d7b0008e21e5fd61cbe7a7f34ca3b6905a78ab677d9bc395e299359c75cda6b4",
    "opentelemetry_api-1.45.2-py3-none-any.whl": "2cbd737377e710ee740d261c94e93fa0f08e6d037c18f17513cc0a2d94479a34",
}


def _package(name: str) -> dict:
    data = tomllib.loads(LOCK.read_text(encoding="utf-8"))
    matches = [p for p in data["package"] if p.get("name") == name]
    assert len(matches) == 1, f"expected one {name} entry, got {len(matches)}"
    return matches[0]


def test_opentelemetry_api_at_or_above_patched_version() -> None:
    version = tuple(int(x) for x in _package("opentelemetry-api")["version"].split("."))
    assert version >= (1, 45, 2), f"opentelemetry-api {version} is vulnerable (GHSA-mf5v-w3w3-f6cg)"


def test_opentelemetry_api_hashes_match_pypi() -> None:
    # A later bump must re-verify its digests against PyPI and update this table.
    pkg = _package("opentelemetry-api")
    artifacts = [pkg["sdist"], *pkg["wheels"]]
    seen = {a["url"].rsplit("/", 1)[-1]: a["hash"] for a in artifacts}
    assert seen == {k: f"sha256:{v}" for k, v in PYPI_SHA256.items()}
