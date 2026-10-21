"""Manual test sign-off for a stable release (owner flow, docs/RELEASING.md). Standard library only: the same module is
used by `jarvis release signoff`, the dashboard "Release sign-off" page, core/tools/manual_test_sheet.py (writes the
sheet) and core/tools/release_gate.py (refuses a stable tag without a complete sign-off).

The sheet is one markdown file per stable version, docs/reviews/MANUAL_TEST_<version>.md:

    <!-- manual-test-sheet v1 -->
    # Manual test sheet for JARVES 4.1.0
    Version: 4.1.0
    | ID | Area | What to do | Expected | Result | Note |
    ...
    ## Sign-off
    - Tested beta: 4.1.0-beta.7
    - Commit: <40 hex>
    - Date: 2026-10-21
    - Signed: yes

A row is done when its result is `pass`, or `accepted-blocked` with a note (the owner accepts that it could not be
tested and says why). `pending`, `fail` and `blocked` stop the release. Nothing here talks to the network.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

MARKER = "<!-- manual-test-sheet v1 -->"
RESULTS = ("pending", "pass", "fail", "blocked", "accepted-blocked")
HEADER = ("ID", "Area", "What to do", "Expected", "Result", "Note")
SHEET_DIR = Path("docs") / "reviews"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SPLIT = re.compile(r"(?<!\\)\|")


class SheetError(ValueError):
    pass


def normalise_version(v: str) -> str:
    """'v4.1.0' / 'win-v4.1.0' / '4.1.0' -> '4.1.0'. Anything else raises."""
    s = re.sub(r"^(?:win-)?v", "", (v or "").strip())
    if not VERSION_RE.match(s):
        raise SheetError(f"{v!r} is not a stable version X.Y.Z")
    return s


def sheet_path(root: Path | str, version: str) -> Path:
    return Path(root) / SHEET_DIR / f"MANUAL_TEST_{normalise_version(version)}.md"


def _cell(text: str) -> str:
    return " ".join(str(text).replace("|", "\\|").split())


def _uncell(text: str) -> str:
    return text.strip().replace("\\|", "|")


@dataclass
class Row:
    id: str
    area: str
    todo: str
    expected: str
    result: str = "pending"
    note: str = ""

    def problem(self) -> str | None:
        if self.result == "pass":
            return None
        if self.result == "accepted-blocked":
            return None if self.note.strip() else f"{self.id}: accepted-blocked needs a note (why it could not be tested)"
        return f"{self.id}: result is {self.result}"


@dataclass
class Sheet:
    version: str
    rows: list[Row] = field(default_factory=list)
    intro: str = ""
    beta: str = ""
    commit: str = ""
    date: str = ""
    signed: bool = False

    # ---- text
    def render(self) -> str:
        out = [MARKER, f"# Manual test sheet for JARVES {self.version}", ""]
        if self.intro:
            out += [self.intro.strip(), ""]
        out += [f"Version: {self.version}", "",
                "Results: `pass`, `fail`, `blocked`, `accepted-blocked` (needs a note), `pending`. "
                "Fill them with `jarvis release signoff` or the dashboard page Release sign-off.", "",
                "| " + " | ".join(HEADER) + " |", "|" + "---|" * len(HEADER)]
        for r in self.rows:
            out.append("| " + " | ".join(_cell(x) for x in (r.id, r.area, r.todo, r.expected, r.result, r.note)) + " |")
        out += ["", "## Sign-off", "", f"- Tested beta: {self.beta}", f"- Commit: {self.commit}",
                f"- Date: {self.date}", f"- Signed: {'yes' if self.signed else 'no'}", ""]
        return "\n".join(out)

    @classmethod
    def parse(cls, text: str) -> "Sheet":
        text = text.replace("\r\n", "\n")
        if MARKER not in text:
            raise SheetError("not a manual test sheet (marker missing)")
        m = re.search(r"^Version:\s*(\S+)\s*$", text, re.M)
        if not m:
            raise SheetError("the sheet has no 'Version:' line")
        sh = cls(version=normalise_version(m.group(1)))
        body, _, tail = text.partition("\n## Sign-off")
        for line in body.splitlines():
            if not line.startswith("|") or line.startswith("|---") or line.startswith("| ID "):
                continue
            cells = [_uncell(c) for c in _SPLIT.split(line.strip())[1:-1]]
            if len(cells) != len(HEADER):
                raise SheetError(f"bad table row: {line[:60]!r}")
            if cells[4] not in RESULTS:
                raise SheetError(f"{cells[0]}: unknown result {cells[4]!r}")
            sh.rows.append(Row(*cells))
        for key, attr in (("Tested beta", "beta"), ("Commit", "commit"), ("Date", "date")):
            mm = re.search(rf"^- {key}:[ \t]*(.*)$", tail, re.M)
            setattr(sh, attr, mm.group(1).strip() if mm else "")
        mm = re.search(r"^- Signed:[ \t]*(\w+)", tail, re.M)
        sh.signed = bool(mm and mm.group(1).lower() == "yes")
        return sh

    # ---- editing
    def row(self, rid: str) -> Row:
        for r in self.rows:
            if r.id.lower() == rid.strip().lower():
                return r
        raise SheetError(f"no row {rid!r} in the sheet for {self.version}")

    def set_result(self, rid: str, result: str, note: str = "") -> Row:
        if result not in RESULTS or result == "pending":
            raise SheetError(f"result must be one of {', '.join(r for r in RESULTS if r != 'pending')}")
        r = self.row(rid)
        r.result, r.note = result, note.strip() or r.note
        if result == "accepted-blocked" and not r.note.strip():
            raise SheetError(f"{r.id}: accepted-blocked needs a note (--note)")
        self.signed = False                    # any change after signing needs a new sign-off
        return r

    def open_rows(self) -> list[str]:
        return [p for p in (r.problem() for r in self.rows) if p]

    def sign(self, beta: str, commit: str, today: str | None = None) -> None:
        """Record which beta was tested, its exact commit and the date. Refuses while any row is open."""
        if not self.rows:
            raise SheetError("the sheet has no rows")
        if self.open_rows():
            raise SheetError("cannot sign off, rows still open: " + "; ".join(self.open_rows()))
        beta = (beta or "").strip()
        if not re.fullmatch(rf"{re.escape(self.version)}-beta\.\d+", beta):
            raise SheetError(f"the tested beta must look like {self.version}-beta.N, not {beta!r}")
        if not SHA_RE.match((commit or "").strip().lower()):
            raise SheetError("the commit must be the full 40-character SHA of the tested beta build")
        self.beta, self.commit, self.date = beta, commit.strip().lower(), today or date.today().isoformat()
        self.signed = True

    def problems(self) -> list[str]:
        """Everything that stops a stable release (empty = signed off and complete)."""
        p = list(self.open_rows())
        if not self.rows:
            p.append("the sheet has no rows")
        if not self.signed:
            p.append("the sheet is not signed (jarvis release signoff --finish)")
        if not re.fullmatch(rf"{re.escape(self.version)}-beta\.\d+", self.beta):
            p.append(f"'Tested beta' is missing or not {self.version}-beta.N")
        if not SHA_RE.match(self.commit):
            p.append("'Commit' is missing or not a full SHA")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", self.date):
            p.append("'Date' is missing")
        return p


def load(root: Path | str, version: str) -> Sheet:
    path = sheet_path(root, version)
    if not path.is_file():
        raise SheetError(f"no sign-off sheet for {normalise_version(version)} ({path}); create it with "
                         f"python tools/manual_test_sheet.py --version {normalise_version(version)}")
    return Sheet.parse(path.read_text(encoding="utf-8"))


def save(root: Path | str, sheet: Sheet) -> Path:
    path = sheet_path(root, sheet.version)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(sheet.render(), encoding="utf-8", newline="\n")
    tmp.replace(path)
    return path


DOCS_ONLY = re.compile(r"^(docs/|[^/]+\.md$|LICENSE|\.gitignore)")


def docs_only(files: list[str]) -> bool:
    return all(DOCS_ONLY.match(f.replace("\\", "/")) for f in files)


def tested_sha_ok(tested: str, tag_sha: str, is_ancestor, changed_files) -> str | None:
    """None when the tested beta commit is the tag commit, or an ancestor of it with only documentation changes in
    between; else the reason. `is_ancestor(a, b)` and `changed_files(a, b)` are injected (git in the gate)."""
    tested, tag_sha = tested.strip().lower(), tag_sha.strip().lower()
    if tested == tag_sha:
        return None
    if not is_ancestor(tested, tag_sha):
        return f"the tested beta commit {tested[:12]} is not an ancestor of the tag commit {tag_sha[:12]}"
    files = changed_files(tested, tag_sha)
    code = [f for f in files if not docs_only([f])]
    if code:
        return (f"code changed between the tested beta {tested[:12]} and the tag {tag_sha[:12]}: "
                f"{', '.join(code[:5])}{' ...' if len(code) > 5 else ''}; test a newer beta")
    return None
