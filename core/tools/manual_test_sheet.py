"""Generate the owner's manual test sheet for a stable release: docs/reviews/MANUAL_TEST_<version>.md.

    python tools/manual_test_sheet.py --version 4.1.0 [--since v4.0.0] [--repo-root ..] [--force]

Rows come from (1) the CHANGELOG entries of the release, (2) the feature guides touched since the last stable tag,
(3) the dashboard pages touched, and (4) the standing owner rows of docs/JARVES_SW_V4_WORK_PACKAGE.md section 7 that
every release must pass again (install, voice, approvals, update/rollback, health, privacy). Each row has what to do
(one line), the expected result and a result column (pending / pass / fail / blocked / accepted-blocked) plus a note.
The owner fills it with `jarvis release signoff` or the dashboard page Release sign-off (docs/RELEASING.md); the
release gate (tools/release_gate.py) refuses a stable tag until it is complete and signed.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from jarves import release_signoff as so  # noqa: E402

# Section 7 rows that must pass on every stable release (ids exist in JARVES_SW_V4_WORK_PACKAGE.md)
STANDING = ("OI-02", "OV-01", "OV-02", "OV-03", "OV-07", "OP-01", "OP-02", "OP-03", "OL-I1", "OL-I2", "OL-I3",
            "OL-I4", "OO-02", "OR-01", "OR-02")
MAX_CHANGELOG_ROWS = 20


def changelog_items(text: str, version: str) -> list[str]:
    """Bullets of the CHANGELOG section for `version` (heading containing it, else 'unreleased', else the first)."""
    sections = re.split(r"(?m)^## ", text)[1:]
    pick = next((s for s in sections if version in s.splitlines()[0]), None) \
        or next((s for s in sections if "unreleased" in s.splitlines()[0].lower()), None) \
        or (sections[0] if sections else "")
    items = []
    for line in pick.splitlines()[1:]:
        m = re.match(r"^\s*[-*]\s+(.*)$", line)
        if not m:
            continue
        body = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", m.group(1))             # links -> text
        for part in re.split(r"(?<=[.;])\s+(?=[A-Z])", body):                     # one row per sentence
            part = part.strip()
            if len(part) > 12:
                items.append(part)
    return items[:MAX_CHANGELOG_ROWS]


def standing_rows(work_package: str, ids=STANDING) -> list[so.Row]:
    rows = []
    for line in work_package.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) >= 5 and cells[0] in ids:
            steps = cells[2] or cells[1]
            rows.append(so.Row(cells[0], "Standing", steps, cells[3]))
    order = {i: n for n, i in enumerate(ids)}
    return sorted(rows, key=lambda r: order[r.id])


def build_sheet(version: str, since: str | None, changelog: str, work_package: str, changed_files: list[str]) -> so.Sheet:
    rows: list[so.Row] = []
    for n, item in enumerate(changelog_items(changelog, version), 1):
        short = item if len(item) <= 150 else item[:147].rstrip() + "..."
        rows.append(so.Row(f"C{n}", "Changed", f"Try it in the beta: {short}",
                           "Works as described; nothing next to it broke"))
    guides = sorted({Path(f).stem for f in changed_files if re.match(r"core/src/jarves/guides/data/[^/]+\.toml$", f)})
    for n, g in enumerate(guides, 1):
        rows.append(so.Row(f"G{n}", "Guide", f"Open the guide '{g}' (Guides page or `jarvis help {g}`) and do its steps",
                           "Every step works as written, in English and in Hebrew"))
    pages = sorted({Path(f).stem for f in changed_files if re.match(r"core/src/jarves/dashboard_static/[a-z_]+\.js$", f)}
                   - {"app", "i18n", "shell", "pages", "home"})
    for n, pg in enumerate(pages, 1):
        rows.append(so.Row(f"P{n}", "Dashboard", f"Open the dashboard page(s) of {pg}.js in English, then Hebrew",
                           "Renders, no console error, no cut-off text, keyboard works"))
    rows += standing_rows(work_package)
    intro = (f"Generated {date.today().isoformat()} from the changes since "
             f"{since or 'the beginning (no earlier stable tag)'}. Install the LATEST beta first "
             "(`jarvis update --channel beta`), test that exact build, then sign off.")
    return so.Sheet(version=version, rows=rows, intro=intro)


def _git(root: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def last_stable_tag(root: Path) -> str | None:
    tags = [t for t in _git(root, "tag", "--list", "v[0-9]*").splitlines() if re.fullmatch(r"v\d+\.\d+\.\d+", t)]
    tags.sort(key=lambda t: tuple(int(x) for x in t[1:].split(".")))
    return tags[-1] if tags else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="manual_test_sheet.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--version", required=True, help="the stable version, e.g. 4.1.0")
    ap.add_argument("--since", help="compare with this tag (default: the newest stable vX.Y.Z tag)")
    ap.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    ap.add_argument("--force", action="store_true", help="overwrite an existing sheet (loses recorded results)")
    a = ap.parse_args(argv)
    root = a.repo_root.resolve()
    version = so.normalise_version(a.version)
    out = so.sheet_path(root, version)
    if out.exists() and not a.force:
        print(f"{out} exists; not overwriting recorded results (--force to regenerate)")
        return 1
    since = a.since or last_stable_tag(root)
    files = _git(root, "diff", "--name-only", f"{since}..HEAD").splitlines() if since else []
    read = lambda p: (root / p).read_text(encoding="utf-8") if (root / p).is_file() else ""   # noqa: E731
    sheet = build_sheet(version, since, read("CHANGELOG.md"), read("docs/JARVES_SW_V4_WORK_PACKAGE.md"), files)
    so.save(root, sheet)
    print(f"wrote {out} with {len(sheet.rows)} rows (since {since or 'the beginning'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
