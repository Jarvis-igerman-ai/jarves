"""Fail the build if the staged installer payload contains anything personal (SW-V2 section 11).

Forbidden anywhere: the files JARVES writes into a user's data dir (secrets.env, link_token, OAuth tokens, databases,
logs, health snapshots, pairing ids, user config) and generic secret files (.env, *.pem, *.key, .git).
Content scan of JARVES's own files (launcher, jarves package, face): OpenAI keys, GitHub tokens, private keys.
Third-party wheels are scanned by name only (their test fixtures may legitimately contain sample keys).

    python -I packaging/payload_check.py <stage dir>      exit 0 = clean, 1 = findings (listed)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

FORBIDDEN_NAMES = {"secrets.env", "link_token", "google_token.json", "google_oauth.json", "health.json",
                   "pear_client_id", "pear_client_id.paired", "features.json", "setup_done", "config.toml",
                   "updates.json", ".env", ".git", "id_rsa", "id_ed25519"}
FORBIDDEN_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm", ".log", ".pem", ".key", ".p12", ".pfx")
FORBIDDEN_DIRS = {"chatgpt", "logs", "backups", "workspace", "updates", ".git"}
# Third-party packages that ship sample data with such extensions (checked 2026-10-19 on the real payload).
ALLOWED = (re.compile(r"(^|/)python/Lib/site-packages/[^/]+/.*(tests?|testing|data)/"),
           re.compile(r"/certifi/cacert\.pem$"))             # the public CA bundle (certifi, pip's vendored copy)
SECRET_PATTERNS = [re.compile(p) for p in (r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}", r"gh[pousr]_[A-Za-z0-9]{30,}",
                                            r"github_pat_[A-Za-z0-9_]{30,}", r"-----BEGIN [A-Z ]*PRIVATE KEY-----")]
OWN = (re.compile(r"^[^/]+\.(cmd|txt|json)$"), re.compile(r"^python/Lib/site-packages/jarves/"), re.compile(r"^face/"))
TEXT_LIMIT = 2_000_000


def findings(stage: Path) -> list[str]:
    out = []
    for p in sorted(stage.rglob("*")):
        rel = p.relative_to(stage).as_posix()
        if any(rx.search(rel) for rx in ALLOWED):
            continue
        name = p.name.lower()
        if p.is_dir():
            if name in FORBIDDEN_DIRS and not rel.startswith("python/Lib/"):
                out.append(f"forbidden folder: {rel}")
            continue
        if name in FORBIDDEN_NAMES or name.endswith(FORBIDDEN_SUFFIXES):
            out.append(f"forbidden file: {rel}")
            continue
        if any(rx.search(rel) for rx in OWN) and p.stat().st_size <= TEXT_LIMIT:
            text = p.read_bytes().decode("utf-8", "ignore")
            for rx in SECRET_PATTERNS:
                if rx.search(text):
                    out.append(f"secret-like content ({rx.pattern[:20]}...): {rel}")
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not Path(argv[1]).is_dir():
        print("usage: payload_check.py <stage dir>", file=sys.stderr)
        return 2
    bad = findings(Path(argv[1]))
    for b in bad:
        print("PAYLOAD CHECK:", b)
    print("PAYLOAD CHECK: " + ("FAIL" if bad else "PASS") + f" ({len(bad)} finding(s))")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
