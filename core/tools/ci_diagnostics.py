"""SW-V4 M: collect REDACTED diagnostics from a failed CI job into one folder (uploaded as a workflow artifact).

    python tools/ci_diagnostics.py OUT_DIR SOURCE [SOURCE ...]

A SOURCE is a file or a folder (searched for *.log, *.out, *.txt, *.xml, *.json up to 2 MB each, newest tail kept).
Every line goes through jarves.logs.redact (keys, tokens, bearer headers); JSON log lines additionally through
jarves.logs.scrub with privacy ON (user content masked); e-mail addresses, user-profile paths and phone-like numbers are
replaced too. The public repo's Actions logs and artifacts are world readable, so nothing leaves unredacted. The
output is re-scanned and the run fails (exit 2, nothing kept) if a secret-looking string survives.
Also writes `environment.txt` (platform, python, package versions), no environment-variable values.
"""
from __future__ import annotations

import json
import platform
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

MAX_BYTES = 2_000_000
SUFFIXES = {".log", ".out", ".txt", ".xml", ".json"}
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_USER_PATH = re.compile(r"(?i)([A-Z]:[\\/]+Users[\\/]+|/home/|/Users/)[^\\/\s\"']+")
_PHONE = re.compile(r"(?<![\w.])\+?\d[\d \-]{8,}\d(?![\w.])")
_LEFTOVER = re.compile(r"sk-[A-Za-z0-9_-]{16,}|(?i:bearer)\s+[A-Za-z0-9._~+/=-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|"
                       r"AIza[0-9A-Za-z_-]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY")


def scrub_line(line: str) -> str:
    from jarves import logs
    s = line.strip()
    if s.startswith("{") and s.endswith("}"):
        try:
            line = json.dumps(logs.scrub(json.loads(s), private=True), ensure_ascii=False)
        except (ValueError, TypeError):
            pass
    line = logs.redact(line)
    line = _EMAIL.sub("<email>", line)
    line = _USER_PATH.sub(r"\1<user>", line)
    return _PHONE.sub("<number>", line)


def scrub_text(text: str) -> str:
    return "\n".join(scrub_line(ln) for ln in text.splitlines())


def _files(src: Path):
    if src.is_file():
        yield src
    elif src.is_dir():
        yield from (p for p in sorted(src.rglob("*")) if p.is_file() and p.suffix.lower() in SUFFIXES)


def collect(out: Path, sources: list[Path]) -> list[str]:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    written = []
    for n, src in enumerate(sources):
        for f in _files(src):
            raw = f.read_bytes()[-MAX_BYTES:]
            text = scrub_text(raw.decode("utf-8", "replace"))
            name = f"{n:02d}_{f.name}"
            (out / name).write_text(text + "\n", encoding="utf-8")
            written.append(name)
    env = [f"platform: {platform.platform()}", f"python: {sys.version.split()[0]}", f"machine: {platform.machine()}"]
    (out / "environment.txt").write_text("\n".join(env) + "\n", encoding="utf-8")
    written.append("environment.txt")
    for name in written:
        if _LEFTOVER.search((out / name).read_text(encoding="utf-8")):
            shutil.rmtree(out)
            raise SystemExit(2)
    return written


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 64
    try:
        files = collect(Path(argv[0]), [Path(a) for a in argv[1:]])
    except SystemExit as e:
        print("ci_diagnostics: a secret-looking string survived redaction; nothing was kept")
        return int(e.code or 2)
    print(f"ci_diagnostics: {len(files)} redacted file(s) in {argv[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
