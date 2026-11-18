"""CLI demo for a PR (JAR-251): render `jarvis [GROUP] --help` as an asciinema-style terminal SVG image and write
demo.json (pr, title, one-line what_changed, files) for the `demo-<PR>` artifact.

    uv run python tools/cli_demo.py --pr 123 --title "..." --what "..." --out demo [--group agents ...]

Only the fixed `python -m jarves [GROUP] --help` command runs (GROUP must match a lowercase name); no shell, no
model-supplied command. Help text is test data only. Title/what are PR-controlled: they are stored as JSON strings and
XML-escaped in the image; any consumer must still escape them.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

MAX_LINES, MAX_COLS, MAX_WHAT = 60, 120, 200
GROUP_RE = re.compile(r"[a-z][a-z0-9-]{0,29}")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-_]")
CHILD_TIMEOUT = 60


def _clean(line: str) -> str:
    line = ANSI_RE.sub("", line).expandtabs(4)
    return "".join(c for c in line if c.isprintable())[:MAX_COLS]


def render_svg(text: str, title: str) -> str:
    lines = [_clean(x) for x in text.splitlines()]
    extra = len(lines) - MAX_LINES
    rows = [_clean(f"$ {title}")] + lines[:MAX_LINES] + ([f"… ({extra} more lines)"] if extra > 0 else [])
    lh, cw, top = 18, 8.7, 40
    w, h = int(max(60, max(len(r) for r in rows)) * cw + 32), top + lh * len(rows) + 16
    body = "\n".join(
        f'<text x="16" y="{top + lh * i}" fill="{"#7ee787" if i == 0 else "#e6edf3"}" xml:space="preserve">'
        f"{escape(r)}</text>" for i, r in enumerate(rows))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'font-family="DejaVu Sans Mono, Consolas, monospace" font-size="14">\n'
            f'<rect width="{w}" height="{h}" rx="8" fill="#0d1117"/>\n'
            '<circle cx="18" cy="16" r="5" fill="#ff5f56"/><circle cx="36" cy="16" r="5" fill="#ffbd2e"/>'
            '<circle cx="54" cy="16" r="5" fill="#27c93f"/>\n'
            f"{body}\n</svg>\n")


def check_group(group: str) -> None:
    if group and not GROUP_RE.fullmatch(group):
        raise ValueError(f"group must match {GROUP_RE.pattern}: {group[:40]!r}")


def capture_help(group: str, run=subprocess.run) -> str:
    check_group(group)
    cmd = [sys.executable, "-m", "jarves", *([group] if group else []), "--help"]
    p = run(cmd, capture_output=True, text=True, timeout=CHILD_TIMEOUT, env={**os.environ, "NO_COLOR": "1"})
    if p.returncode != 0:
        raise RuntimeError(f"jarvis {group} --help: exit {p.returncode}: {(p.stderr or '').strip()[:300]}")
    return p.stdout


def _one_line(name: str, v, limit: int) -> str:
    if not isinstance(v, str) or not v.strip() or "\n" in v or "\r" in v or len(v) > limit:
        raise ValueError(f"{name} must be one non-empty line of at most {limit} characters")
    return v.strip()


def write_demo(out: Path, pr, title, what_changed, files) -> Path:
    if not isinstance(pr, int) or isinstance(pr, bool) or pr <= 0:
        raise ValueError("pr must be a positive integer")
    data = {"pr": pr, "title": _one_line("title", title, 300),
            "what_changed": _one_line("what_changed", what_changed, MAX_WHAT), "files": []}
    for f in files:
        if not isinstance(f, str) or Path(f).name != f or not (out / f).is_file():
            raise ValueError(f"file must be an existing plain name inside the demo folder: {str(f)[:80]!r}")
        data["files"].append(f)
    fd, tmp = tempfile.mkstemp(dir=out, prefix=".demo-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, out / "demo.json")
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return out / "demo.json"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--what", required=True, help="one line: what changed for you")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--group", action="append", default=None, help="CLI group to show ('' = top-level help)")
    a = ap.parse_args(argv)
    try:
        groups = a.group if a.group is not None else [""]
        for g in groups:  # validate every name before writing anything
            check_group(g)
        a.out.mkdir(parents=True, exist_ok=True)
        files = []
        for g in groups:
            name = f"cli-{g or 'help'}.svg"
            title = f"jarvis {g} --help" if g else "jarvis --help"
            (a.out / name).write_text(render_svg(capture_help(g), title), encoding="utf-8")
            files.append(name)
        print(write_demo(a.out, a.pr, a.title, a.what, files))
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as e:
        print(f"cli_demo: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
