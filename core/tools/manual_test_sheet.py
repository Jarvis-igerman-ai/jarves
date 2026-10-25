"""Generate the owner's manual test sheet for a stable release: docs/reviews/MANUAL_TEST_<version>.md.

    python tools/manual_test_sheet.py --version 4.1.0 [--since v4.0.0] [--extra-rows FILE] [--repo-root ..] [--force]

Rows come from (1) the CHANGELOG entries of the release, (2) the feature guides touched since the last stable tag,
(3) the dashboard pages touched, and (4) the standing owner rows of docs/JARVES_SW_V4_WORK_PACKAGE.md section 7 that
every release must pass again (install, voice, approvals, update/rollback, health, privacy). Each row has what to do
(one line), the expected result and a result column (pending / pass / fail / blocked / accepted-blocked) plus a note.
The owner fills it with `jarvis release signoff` or the dashboard page Release sign-off (docs/RELEASING.md); the
release gate (tools/release_gate.py) refuses a stable tag until it is complete and signed.

--since TAG-OR-VERSION is the diff baseline: the first run is a baseline (`--since 1.0.0`: with no tag at or below it,
every guide and dashboard page), later releases use the previous release (changed topics only). A version without a tag
uses the nearest tag below it. --extra-rows FILE appends owner-only rows (numbered lines `1. text`, or table rows
`| ID | Area | Do | Expected |`), e.g. docs/reviews/SWV5_ACCEPTANCE.md for the real-phone checks. A version with no CHANGELOG
section uses the newest section below it, with a note in the sheet intro. The sheet format is unchanged.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from jarves import changelog_parts  # noqa: E402
from jarves import release_signoff as so  # noqa: E402

# Section 7 rows that must pass on every stable release (ids exist in JARVES_SW_V4_WORK_PACKAGE.md)
STANDING = ("OI-02", "OV-01", "OV-02", "OV-03", "OV-07", "OP-01", "OP-02", "OP-03", "OL-I1", "OL-I2", "OL-I3",
            "OL-I4", "OO-02", "OR-01", "OR-02")
MAX_CHANGELOG_ROWS = 20
# entries the owner cannot try in the app: documentation, CI and repository tooling (CI tests those)
NOT_IN_APP = re.compile(r"^(Docs|Internal|CI)\b|^\w+(\s*\([^)]*\))?:\s*(CI\b|SECURITY\.md)")


def _ver(text: str) -> tuple[int, int, int] | None:
    m = re.search(r"(?<![\d.])(\d+)\.(\d+)(?:\.(\d+))?(?![\d.]*\d)", text)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)) if m else None


def _pick_sections(text: str, version: str, since: str | None = None) -> tuple[list[str], str]:
    """CHANGELOG sections for `version` and a note. No section for it: those newer than `since`, else the newest below it."""
    sections = re.split(r"(?m)^## ", text)[1:]
    exact = next((s for s in sections if version in s.splitlines()[0]), None)
    if exact is not None:
        return [exact], ""
    want = _ver(version)
    low = _ver(since.split("-v")[-1].lstrip("v")) if since else None
    older = sorted(((_ver(s.splitlines()[0]), s) for s in sections
                    if want and _ver(s.splitlines()[0]) and _ver(s.splitlines()[0]) < want),
                   key=lambda t: t[0], reverse=True)
    if older:
        chosen = [s for v, s in older if low and v > low] or [older[0][1]]
        heads = ", ".join(s.splitlines()[0].strip() for s in chosen)
        return chosen, f"There is no CHANGELOG section for {version}; the rows come from the earlier section(s): {heads}."
    pick = next((s for s in sections if "unreleased" in s.splitlines()[0].lower()), None) or (sections[0] if sections else "")
    return [pick], ""


def changelog_items(text: str, version: str, since: str | None = None) -> list[str]:
    return changelog_items_note(text, version, since)[0]


def changelog_items_note(text: str, version: str, since: str | None = None) -> tuple[list[str], str]:
    """Bullets of the CHANGELOG section(s) for `version`, and a note when they are not its own section."""
    picked, note = _pick_sections(text, version, since)
    items = []
    for line in (ln for sec in picked for ln in sec.splitlines()[1:]):
        m = re.match(r"^\s*[-*]\s+(.*)$", line)
        if not m:
            continue
        body = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", m.group(1)).strip()     # links -> text
        if NOT_IN_APP.match(body):
            continue
        first = ""                                       # one row per entry (one row per sentence made rows like
        for sentence in re.split(r"(?<=[.;])\s+(?=[A-Z])", body):   # "See docs/x.md."): its first sentence, plus the
            first = f"{first} {sentence.strip()}".strip()             # next one while it is too short to act on
            if len(first) >= 40:
                break
        if len(first) > 12:
            items.append(first)
    return items[:MAX_CHANGELOG_ROWS], note


def standing_rows(work_package: str, ids=STANDING) -> list[so.Row]:
    rows = []
    for line in work_package.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) >= 5 and cells[0] in ids:
            steps = cells[2] or cells[1]
            rows.append(so.Row(cells[0], "Standing", steps, cells[3]))
    order = {i: n for n, i in enumerate(ids)}
    return sorted(rows, key=lambda r: order[r.id])


def extra_rows(text: str, area: str = "Owner-only") -> list[so.Row]:
    """Rows from a file: numbered lines `N. text` (id X<N>) or table rows `| ID | Area | Do | Expected |`."""
    rows: list[so.Row] = []
    for line in text.splitlines():
        m = re.match(r"^\s*(\d+)\.\s+(.+?)\s*$", line)
        if m:
            rows.append(so.Row(f"X{int(m.group(1)):02d}", area, f"Owner only, needs the real device: {m.group(2)}",
                               "The step works as written (PASS), or say what differs"))
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.lstrip().startswith("|") and len(cells) >= 4 and cells[0] not in ("ID", "Step") \
                and not set(cells[0]) <= set("-: "):
            rows.append(so.Row(cells[0], area, f"Owner only, needs the real device: {cells[2]}", cells[3]))
    ids = [r.id for r in rows]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise ValueError(f"duplicate row id in the extra rows: {', '.join(dup)}")
    return rows


def build_sheet(version: str, since: str | None, changelog: str, work_package: str, changed_files: list[str],
                extra: list[so.Row] | None = None, note: str = "") -> so.Sheet:
    rows: list[so.Row] = []
    items, cl_note = changelog_items_note(changelog, version, since)
    for n, item in enumerate(items, 1):
        short = item if len(item) <= 150 else item[:147].rstrip() + "..."
        rows.append(so.Row(f"C{n}", "Changed", f"Try it in the beta: {short}",
                           "Works as described; nothing next to it broke"))
    guides = sorted({Path(f).stem for f in changed_files if re.match(r"core/src/jarves/guides/data/[^/]+\.toml$", f)})
    if guides:                         # one row, not one per guide: CI already runs every guide (test_guides, CLI parity)
        rows.append(so.Row("G1", "Guide", f"Open the {len(guides)} changed guide(s) on the Guides page, in English and in "
                           f"Hebrew, and skim each: {', '.join(guides)}",
                           "Each reads correctly and its first steps work as written"))
    pages = sorted({Path(f).stem for f in changed_files if re.match(r"core/src/jarves/dashboard_static/[a-z_]+\.js$", f)}
                   - {"app", "i18n", "shell", "pages", "home"})
    if pages:                          # one row; CI already loads every page in a real browser (test_browser, a11y)
        rows.append(so.Row("P1", "Dashboard", f"Open the dashboard pages changed in this release, in English, then Hebrew: "
                           f"{', '.join(pages)}", "Each renders, no cut-off text, keyboard works"))
    rows += standing_rows(work_package)
    taken = {r.id for r in rows}
    for r in extra or []:
        if r.id in taken:
            raise ValueError(f"extra row id {r.id} already exists in the sheet")
        rows.append(r)
    intro = (f"Generated {date.today().isoformat()} from the changes since "
             f"{since or 'the beginning (no earlier stable tag)'}. Install the LATEST beta first "
             "(`jarvis update --channel beta`), test that exact build, then sign off.")
    notes = " ".join(x for x in (note, cl_note) if x)
    return so.Sheet(version=version, rows=rows, intro=f"{intro} {notes}".strip())


def _git(root: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def last_stable_tag(root: Path) -> str | None:
    tags = [t for t in _git(root, "tag", "--list", "v[0-9]*").splitlines() if re.fullmatch(r"v\d+\.\d+\.\d+", t)]
    tags.sort(key=lambda t: tuple(int(x) for x in t[1:].split(".")))
    return tags[-1] if tags else None


def resolve_since(tags: list[str], since: str) -> tuple[str | None, str]:
    """(git ref, note). An existing tag as given; else the nearest stable tag at or below the version; else baseline (None)."""
    if since in tags:
        return since, ""
    for cand in (f"v{since}", f"win-v{since}"):
        if cand in tags:
            return cand, ""
    want = _ver(since.split("-v")[-1].lstrip("v"))
    below = sorted((_ver(t), t) for t in tags if re.fullmatch(r"v\d+\.\d+\.\d+", t) and want and _ver(t) <= want)
    if below:
        return below[-1][1], f"No tag for {since}; compared with the nearest earlier tag {below[-1][1]}."
    return None, (f"No tag at or below {since}: this is the baseline run, every guide and dashboard page is listed "
                  "(later releases list only what changed).")


def tree_files(root: Path) -> list[str]:
    """Every guide and dashboard page of the working tree (the baseline run)."""
    out: list[str] = []
    for sub, pat in (("core/src/jarves/guides/data", "*.toml"), ("core/src/jarves/dashboard_static", "*.js")):
        if (root / sub).is_dir():
            out += [f"{sub}/{p.name}" for p in sorted((root / sub).glob(pat))]
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="manual_test_sheet.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--version", required=True, help="the stable version, e.g. 4.1.0")
    ap.add_argument("--since", help="diff baseline: a tag or version (nearest earlier tag if it has none; no tag at or below "
                                    "it = baseline run listing everything). Default: the newest stable vX.Y.Z tag")
    ap.add_argument("--extra-rows", type=Path, help="file whose numbered lines or table rows are appended as owner-only rows")
    ap.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    ap.add_argument("--force", action="store_true", help="overwrite an existing sheet (loses recorded results)")
    a = ap.parse_args(argv)
    root = a.repo_root.resolve()
    version = so.normalise_version(a.version)
    out = so.sheet_path(root, version)
    if out.exists() and not a.force:
        print(f"{out} exists; not overwriting recorded results (--force to regenerate)")
        return 1
    note = ""
    if a.since:
        since, note = resolve_since(_git(root, "tag", "--list").splitlines(), a.since)
        files = _git(root, "diff", "--name-only", f"{since}..HEAD").splitlines() if since else tree_files(root)
    else:
        since = last_stable_tag(root)
        files = _git(root, "diff", "--name-only", f"{since}..HEAD").splitlines() if since else []
    read = lambda p: (root / p).read_text(encoding="utf-8") if (root / p).is_file() else ""   # noqa: E731
    changelog = changelog_parts.read(root / "CHANGELOG.md") if (root / "CHANGELOG.md").is_file() else ""   # + fragments
    extra: list[so.Row] = []
    if a.extra_rows:
        try:
            extra = extra_rows(a.extra_rows.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"--extra-rows: {e}")
            return 2
    sheet = build_sheet(version, since or a.since, changelog, read("docs/JARVES_SW_V4_WORK_PACKAGE.md"), files, extra, note)
    so.save(root, sheet)
    print(f"wrote {out} with {len(sheet.rows)} rows (since {since or 'the beginning'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
