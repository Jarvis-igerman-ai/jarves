"""T20: one hermetic audit that FAILS if a planted FAKE secret survives redaction in any export path:
the JSON log files (both privacy modes), the `jarvis diag` bundle (zip members) and the tracked source files.
All values come from tools/redaction_audit.SECRETS (fake, unique, searchable; scanned raw, JSON, URL, base64, UTF-16)."""
from __future__ import annotations

import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from jarves import diagbundle, logs
from jarves.config import Config

CORE = Path(__file__).resolve().parents[1]
REPO = CORE.parent
sys.path.insert(0, str(CORE))
from tools import redaction_audit as ra  # noqa: E402

SECRET_KEYS = {f"secret:{k}" for k in ra.SECRETS}
BARE = {"dashboard_link", "bridge_bearer", "password", "user_profile"}     # no token shape: only masked by context
SHAPED = {k: v for k, v in ra.SECRETS.items() if k not in BARE}


def secret_hits(blobs: dict[str, bytes]) -> dict[str, list]:
    out = {}
    for name, data in blobs.items():
        for n, hits in ra.scan_blob(name, data).items():
            bad = [h for h in hits if h[0] in SECRET_KEYS or h[0].startswith("secret")]
            if bad:
                out[n] = bad
    return out


def emit_planted(home: Path, private: bool) -> None:
    logs.setup(home / "logs", privacy=private, console=False, max_bytes=40_000, backups=3)
    try:
        for k, v in SHAPED.items():
            logs.info("audit", "PLANTED", "planted secret in a field", note=f"value is {v}", detail={"x": [v]})
        for k, v in ra.SECRETS.items():
            logs.warn("audit", "PLANTED_KV", "planted secret under a key", **{"token": v, "password": v, "api_key": v})
        logs.info("audit", "PLANTED_ALL", "all planted secrets at once", note=ra.SECRET_ONLY)
        for h in logs._logger.handlers:
            h.flush()
    finally:
        logs.set_privacy(True)


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("JARVES_HOME", str(h))
    monkeypatch.setenv("OPENAI_ADMIN_KEY", ra.SECRETS["openai_admin_key"])
    return h


@pytest.mark.parametrize("private", [True, False], ids=["privacy_on", "privacy_off"])
def test_no_planted_secret_in_log_files(home, private):
    emit_planted(home, private)
    files = sorted((home / "logs").glob("jarves.log*"))
    assert files and sum(f.stat().st_size for f in files) > 0, "nothing was logged (the audit would be blind)"
    assert not secret_hits({f.name: f.read_bytes() for f in files})


@pytest.mark.parametrize("private", [True, False], ids=["privacy_on", "privacy_off"])
def test_no_planted_secret_in_diag_bundle(home, tmp_path, private):
    emit_planted(home, private)
    out = tmp_path / "diag.zip"
    diagbundle.build(home, Config(), out, lines=5000)
    with zipfile.ZipFile(out) as z:
        assert "recent.log" in z.namelist() and z.read("recent.log").strip(), "bundle log member is empty"
    assert not secret_hits({"diag.zip": out.read_bytes()})


def test_no_planted_secret_in_tracked_files():
    """Fake secrets live only in the corpus/tests/tools; no other tracked file may contain one."""
    files = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True).stdout.split(b"\0")
    blobs = {}
    for f in filter(None, files):
        rel = f.decode("utf-8", "replace")
        if rel.startswith(("core/tests/", "core/tools/")) or rel.endswith((".png", ".jpg", ".ico", ".zip", ".wav", ".mp3")):
            continue
        p = REPO / rel
        try:
            if p.is_file() and p.stat().st_size < 5_000_000:
                blobs[rel] = p.read_bytes()
        except OSError:
            pass
    assert blobs
    assert not secret_hits(blobs)


def test_the_audit_can_fail():
    """Controls: the scanner flags a planted secret in a raw log line, a bundle zip and a tracked-file blob."""
    v = ra.SECRETS["openai_key"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("recent.log", json.dumps({"msg": f"leak {v}"}))
    assert secret_hits({"a.log": f"leak {v}".encode()})
    assert secret_hits({"d.zip": buf.getvalue()})


def test_a_broken_redactor_is_caught(home, monkeypatch):
    """With redaction disabled the planted secret reaches the log file, so the audit above would go red."""
    monkeypatch.setattr(logs, "redact", lambda s: s)
    monkeypatch.setattr(logs, "_PATTERNS", [])
    emit_planted(home, False)
    files = sorted((home / "logs").glob("jarves.log*"))
    assert secret_hits({f.name: f.read_bytes() for f in files})
