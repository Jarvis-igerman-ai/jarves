"""Repo consolidation (V4-RP-1/2/3): the public repo is the source repo, signed installers stay in the releases repo,
ticket drafts stay local by default, and no product file links to the deprecated private repo. No network."""
from __future__ import annotations

import asyncio
import base64
import hashlib
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from jarves import updates
from jarves.updates import UpdateChecker

ROOT = Path(__file__).resolve().parents[2]
OLD = "igalgerman/" + "JARVIS"                     # the deprecated private repo (assembled: not a literal in this file)
EXE = b"MZ signed 4.0.2 installer"


class ReleaseServer:
    """Local fake of the releases host: serves a signed latest.json and the installer; 404 for everything else."""

    def __init__(self, version: str):
        k = Ed25519PrivateKey.generate()
        self.pub = base64.b64encode(k.public_key().public_bytes(serialization.Encoding.Raw,
                                                                serialization.PublicFormat.Raw)).decode()
        name = f"JARVES-Setup-{version}.exe"
        m = {"product": "jarves", "platform": "windows-x64", "channel": "main", "version": version, "installer": name,
             "sha256": hashlib.sha256(EXE).hexdigest(), "size": len(EXE), "commit": "abc", "built": "now",
             "url": f"{updates.public_releases_url()}/{name}"}
        sig = k.sign(updates.canonical(m))
        self.files = {f"{updates.public_releases_url()}/latest.json":
                      {"manifest": m, "signature": {"alg": "ed25519", "sig": base64.b64encode(sig).decode()}},
                      m["url"]: EXE}
        self.calls: list[str] = []

    def __call__(self, url, headers):
        self.calls.append(url)
        return (200, self.files[url]) if url in self.files else (404, None)


def test_default_sources_public_source_repo_and_releases_repo():
    assert updates.REPO == "igalgerman/jarves" and updates.DEFAULT_RELEASES_REPO == "igalgerman/jarvis-releases"
    assert updates.public_releases_url() == "https://github.com/igalgerman/jarvis-releases/releases/latest/download"


@pytest.mark.parametrize("current,found", [("3.0.0.5", True), ("4.0.1.9", True), ("4.0.2.1", False),
                                           ("4.1.0.0", False), ("5.0.0.0", False)])
def test_default_update_check_reads_the_signed_manifest_and_never_offers_a_downgrade(tmp_path, monkeypatch, current,
                                                                                    found):
    for k in ("JARVES_UPDATE_REPO", "JARVES_UPDATE_BASE_URL", "JARVES_RELEASES_REPO"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(updates, "is_installed_build", lambda: False)
    srv = ReleaseServer("4.0.2.1")
    s = UpdateChecker(tmp_path, http=srv, current=current, public_keys=[srv.pub]).check(force=True)
    assert srv.calls == [f"{updates.public_releases_url()}/latest.json"]     # only the releases repo, no REST call
    assert s["available"] is found and s["error"] is None
    if found:
        assert s["latest"]["version"] == "4.0.2.1" and "jarvis-releases" in s["latest"]["asset"]["download_url"]


def test_wrongly_signed_manifest_is_never_an_update(tmp_path, monkeypatch):
    monkeypatch.delenv("JARVES_UPDATE_BASE_URL", raising=False)
    monkeypatch.setattr(updates, "is_installed_build", lambda: False)
    srv, other = ReleaseServer("9.0.0.1"), ReleaseServer("9.0.0.1")
    s = UpdateChecker(tmp_path, http=srv, current="3.0.0.5", public_keys=[other.pub]).check(force=True)
    assert not s["available"] and "not signed by a trusted" in s["error"]


def test_missing_release_message_names_the_public_repos_not_the_old_one(tmp_path, monkeypatch):
    monkeypatch.delenv("JARVES_UPDATE_REPO", raising=False)
    s = UpdateChecker(tmp_path, http=lambda url, h: (404, None), current="3.0.0.5", public_keys=[],
                      base_url="").check(force=True)
    assert not s["available"] and "igalgerman/jarves" in s["error"] and "igalgerman/jarvis-releases" in s["error"]
    assert OLD not in s["error"]


def test_ticket_repo_default_is_empty_and_an_empty_repo_files_nothing(tmp_path):
    from jarves.diag import db as diag_db, tickets
    from jarves.store import Store
    assert diag_db.SETTINGS["health.ticket_repo"] == ""
    calls, asked = [], []

    def runner(argv, stdin=None):
        calls.append(argv)
        return 1, "unexpected"

    async def ask(*a, **k):
        asked.append(a)
        return True
    st = Store(tmp_path / "store.db", tier="auto", backend_model="x")
    try:
        tid = tickets.save_draft(st, tickets.draft({"fingerprint": "fp1", "title": "t", "severity": "low",
                                                    "category": "x", "detail": "d", "evidence": [], "fix": "f",
                                                    "count": 1}))
        res = asyncio.run(tickets.file(st, tid, ask=ask, repo="", by="test", run=runner))
        assert res["status"] == "draft" and "stays local" in res["reason"] and not calls and not asked
    finally:
        st.close()


PRODUCT_DIRS = ("core/src", "packaging", ".github", "scripts")
ALLOW: set[str] = set()          # product files allowed to name the deprecated repo (none)
SKIP_SUFFIX = {".pyc", ".png", ".ico", ".exe", ".woff2", ".woff", ".db", ".wav", ".mp3", ".zip", ".jpg", ".onnx"}


def test_no_product_file_links_to_the_deprecated_private_repo():
    bad = []
    for d in PRODUCT_DIRS:
        for p in (ROOT / d).rglob("*"):
            if not p.is_file() or p.suffix in SKIP_SUFFIX or {"__pycache__", ".venv"} & set(p.parts):
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            rel = p.relative_to(ROOT).as_posix()
            if OLD in text and rel not in ALLOW:
                bad.append(rel)
    assert not bad, f"product files still name the deprecated repo: {bad}"


def test_plain_http_update_base_is_named_not_silently_replaced(tmp_path, monkeypatch):
    """V4-UP-1: an http:// base is refused with its own message (not the 'private repository' GitHub fallback)."""
    monkeypatch.delenv("JARVES_UPDATE_BASE_URL", raising=False)
    hits = []
    s = UpdateChecker(tmp_path, http=lambda url, h: hits.append(url) or (404, None), current="3.0.0.5",
                      public_keys=[], base_url="http://example.test/releases").check(force=True)
    assert not s["available"] and "must be an https://" in s["error"] and hits == []
