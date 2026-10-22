"""Fail the build if the staged installer payload contains anything personal (SW-V2 section 11).

Forbidden anywhere: the files JARVES writes into a user's data dir (secrets.env, link_token, OAuth tokens, databases,
logs, health snapshots, pairing ids, user config) and generic secret files (.env, *.pem, *.key, .git).
Content scan of JARVES's own files (launcher, jarves package, face): OpenAI keys, GitHub tokens, private keys.
Third-party wheels are scanned by name only (their test fixtures may legitimately contain sample keys).

    python -I packaging/payload_check.py <stage dir> [--bridge-pin FILE]      exit 0 = clean, 1 = findings (listed)

WhatsApp bridge (SW-V4 L): the Go binary is built in CI and pinned by SHA256 in packaging/bridge_pin.json
({"path": "bridge/jarves-whatsapp-bridge.exe", "sha256": "<64 hex>"}). Without that file the bridge is simply not part
of this build ("BRIDGE: not part of this build", no finding) - but a bridge binary found in the stage WITHOUT a pin is a
finding, and so is a pinned binary that is missing or whose SHA256 differs. The installed copy is therefore always the
exact binary CI built.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

BRIDGE_DIR = "bridge"
BRIDGE_PIN = Path(__file__).with_name("bridge_pin.json")

FORBIDDEN_NAMES = {"secrets.env", "link_token", "google_token.json", "google_oauth.json", "health.json",
                   "pear_client_id", "pear_client_id.paired", "features.json", "setup_done", "config.toml",
                   "updates.json", ".env", ".git", "id_rsa", "id_ed25519",
                   "store.enc", "admin.token", "audit.jsonl"}   # SW-V4 F: WhatsApp bridge state
FORBIDDEN_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm", ".log", ".pem", ".key", ".p12", ".pfx")
FORBIDDEN_DIRS = {"chatgpt", "logs", "backups", "workspace", "updates", ".git", "whatsapp_bridge"}
BRIDGE_EXE = "bridge/whatsapp_bridge.exe"   # SW-V4 F: must match the .sha256 written by scripts/build_whatsapp_bridge.ps1
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
    out.extend(sidecar_findings(stage))
    return out


def sidecar_findings(stage: Path) -> list[str]:
    """If the WhatsApp bridge is staged, its SHA-256 must equal the .sha256 file next to it."""
    exe = stage / BRIDGE_EXE
    if not exe.is_file():
        return []
    side = exe.with_name(exe.name + ".sha256")
    if not side.is_file():
        return [f"bridge binary without .sha256: {BRIDGE_EXE}"]
    want = (side.read_text(encoding="ascii", errors="replace").split() or [""])[0].lower()
    got = hashlib.sha256(exe.read_bytes()).hexdigest()
    return [] if want == got else [f"bridge binary sha256 mismatch: {BRIDGE_EXE}"]


def bridge_findings(stage: Path, pin_file: Path = BRIDGE_PIN) -> tuple[list[str], str]:
    """([findings], one status line) for the Go WhatsApp bridge in the staged payload."""
    present = (sorted(p.relative_to(stage).as_posix() for p in (stage / BRIDGE_DIR).rglob("*") if p.is_file())
               if (stage / BRIDGE_DIR).is_dir() else [])
    if not pin_file.is_file():
        if present:
            return [f"bridge binary without a pin ({pin_file.name} is missing): {', '.join(present)}"], "BRIDGE: unpinned"
        return [], "BRIDGE: not part of this build (no pin file)"
    try:
        pin = json.loads(pin_file.read_text(encoding="utf-8-sig"))
        rel, want = str(pin["path"]), str(pin["sha256"]).lower()
        if (not re.fullmatch(r"[0-9a-f]{64}", want) or rel.startswith(("/", "\\")) or ".." in Path(rel).parts
                or not rel.startswith(BRIDGE_DIR + "/")):
            raise ValueError("bad pin")
    except (OSError, ValueError, KeyError, TypeError):
        return [f"bridge pin file {pin_file.name} is not valid (needs path under bridge/ and a 64-hex sha256)"], "BRIDGE: bad pin"
    f = stage / rel
    if not f.is_file():
        return [f"pinned bridge binary is missing from the payload: {rel}"], "BRIDGE: missing"
    got = hashlib.sha256(f.read_bytes()).hexdigest()
    out = []
    if got != want:
        out.append(f"bridge binary SHA256 {got[:16]}... differs from the pin {want[:16]}...: {rel}")
    extra = [x for x in present if x not in (rel, rel + ".sha256")]   # the build-script sidecar is allowed
    if extra:
        out.append(f"unexpected files next to the bridge binary: {', '.join(extra)}")
    return out, f"BRIDGE: {rel} sha256 {'verified' if not out else 'MISMATCH'} ({f.stat().st_size} bytes)"


def main(argv: list[str]) -> int:
    pin = BRIDGE_PIN
    if "--bridge-pin" in argv:
        i = argv.index("--bridge-pin")
        pin = Path(argv[i + 1]) if i + 1 < len(argv) else pin
        argv = argv[:i] + argv[i + 2:]
    if len(argv) != 2 or not Path(argv[1]).is_dir():
        print("usage: payload_check.py <stage dir> [--bridge-pin FILE]", file=sys.stderr)
        return 2
    bad = findings(Path(argv[1]))
    extra, status = bridge_findings(Path(argv[1]), pin)
    bad += extra
    print("PAYLOAD CHECK: " + status)
    for b in bad:
        print("PAYLOAD CHECK:", b)
    print("PAYLOAD CHECK: " + ("FAIL" if bad else "PASS") + f" ({len(bad)} finding(s))")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
