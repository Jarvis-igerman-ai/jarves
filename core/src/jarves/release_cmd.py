"""`jarvis release signoff` and the dashboard page "Release sign-off": the owner's manual test of a stable release.

A stable release needs a sheet docs/reviews/MANUAL_TEST_<version>.md (core/tools/manual_test_sheet.py writes it from the
changes since the last stable tag). The owner installs the latest beta, goes through the rows, records pass / fail /
blocked here, then finishes: the beta version tested, its commit SHA and the date are written into the sheet and
core/tools/release_gate.py lets the tag through (docs/RELEASING.md). The file logic is jarves.release_signoff (stdlib).
"""
from __future__ import annotations

import os
from pathlib import Path

from . import clilang, logs, release_signoff as so


# ---------------------------------------------------------------------- where the sheets live
def repo_root(home: Path, explicit: str | None = None) -> Path:
    """The git checkout that holds docs/reviews: --repo, $JARVES_REPO_DIR, the current folder or its parents, the
    source tree this module runs from. Last resort: <home>/release (copy the finished file into docs/reviews)."""
    cands = [explicit, os.environ.get("JARVES_REPO_DIR"), str(Path.cwd()), str(Path(__file__).resolve())]
    for c in cands:
        if not c:
            continue
        p = Path(c).resolve()
        for d in (p, *p.parents):
            if (d / "docs" / "reviews").is_dir() and (d / "core").is_dir():
                return d
    return Path(home) / "release"


def _mine(home: Path, explicit: str | None, version: str) -> tuple[Path, so.Sheet]:
    root = repo_root(home, explicit)
    return root, so.load(root, version)


def summary(sheet: so.Sheet) -> dict:
    counts: dict[str, int] = {r: 0 for r in so.RESULTS}
    for r in sheet.rows:
        counts[r.result] += 1
    return {"version": sheet.version, "rows": [vars(r) for r in sheet.rows], "counts": counts,
            "open": sheet.open_rows(), "signed": sheet.signed, "beta": sheet.beta, "commit": sheet.commit,
            "date": sheet.date, "owner": sheet.owner, "problems": sheet.problems()}


def _finish(sheet: so.Sheet, home: Path, beta: str | None, commit: str | None, owner: str | None = None) -> None:
    from . import updates
    beta = beta or updates.beta_label(home) or ""
    commit = commit or str(updates.build_info().get("commit") or "")
    if not owner:
        import getpass
        try:
            owner = getpass.getuser()                 # the account signing: recorded with the rows hash (#508)
        except Exception:                             # noqa: BLE001
            owner = ""
    sheet.sign(beta, commit, owner=owner or "")


# ---------------------------------------------------------------------- CLI
def cmd_release(a, cfg, home) -> int:
    try:
        version = so.normalise_version(a.version or _pyproject_version(repo_root(home, a.repo)))
    except so.SheetError as e:
        print(f"RELEASE_SIGNOFF: {e}")
        return 2
    if a.action in ("status", "sheet"):
        return _flow(a, home, version)
    if a.action == "run":
        from . import release_run
        return release_run.cmd_run(a, home, repo_root(home, a.repo), version,
                                   lambda sheet, owner: _finish(sheet, home, a.beta, a.commit, owner))
    try:
        root, sheet = _mine(home, a.repo, version)
        if a.row:
            row = sheet.set_result(a.row, a.result, a.note or "")
            so.save(root, sheet)
            print(f"{row.id}: {row.result}" + (f" ({row.note})" if row.note else ""))
        if a.finish:
            _finish(sheet, home, a.beta, a.commit, getattr(a, "owner", None))
            so.save(root, sheet)
            print(clilang.fmt(f"נחתם: בטא {sheet.beta}, קומיט {sheet.commit[:12]}, {sheet.date}, {sheet.owner}, "
                              f"גיבוב {sheet.rows_hash}. עכשיו אפשר לתייג v{version}.",
                              f"Signed off: beta {sheet.beta}, commit {sheet.commit[:12]}, {sheet.date}, {sheet.owner}, "
                              f"hash {sheet.rows_hash}. "
                              f"You can now tag v{version}."))
            logs.info("release", "RELEASE_SIGNED", "manual test sign-off recorded", version=version)
            return 0
    except so.SheetError as e:
        print(f"RELEASE_SIGNOFF: {e}")
        return 1
    s = summary(sheet)
    print(f"Sign-off sheet for {version}: {root / so.SHEET_DIR / ('MANUAL_TEST_' + version + '.md')}")
    for r in sheet.rows:
        print(f"  [{r.result:^16}] {r.id}  {r.todo}" + (f"  -- {r.note}" if r.note else ""))
    if s["problems"]:
        print("Still open:")
        for p in s["problems"]:
            print(f"  - {p}")
        print("Record a result:  jarvis release signoff --version %s --row ID --result pass|fail|blocked|accepted-blocked "
              "[--note TEXT]" % version)
        print("When every row is pass or accepted-blocked:  jarvis release signoff --version %s --finish" % version)
        return 1
    print("Signed off.")
    return 0


def _pyproject_version(root: Path) -> str:
    import re
    try:
        m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', (root / "core" / "pyproject.toml").read_text(encoding="utf-8"))
    except OSError:
        m = None
    return m.group(1) if m else ""


def _flow(a, home, version: str) -> int:
    """`jarvis release status` (evidence + what remains) and `jarvis release sheet` (reduced owner sheet)."""
    from . import release_flow as rf
    root = repo_root(home, a.repo)
    if a.action == "sheet":
        try:
            path = rf.write_sheet(root, version, force=a.force)
        except so.SheetError as e:
            print(f"RELEASE_SHEET: {e}")
            return 1
        print(clilang.fmt(f"גיליון קצר ל-{version}: {path}", f"Reduced owner sheet for {version}: {path}"))
        return 0
    checks = rf.collect(root, version)
    for c in checks:
        print(f"  [{'PASS' if c.ok else 'TODO'}] {c.name}: {c.detail}")
    if a.report:
        out = root / "docs" / "release" / f"EVIDENCE_{version}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rf.report(version, checks), encoding="utf-8", newline="\n")
        print(f"Evidence report: {out}")
    left = rf.remaining(checks)
    if left:
        print("What remains:")
        for t in left:
            print(f"  - {t}")
        return 1
    print(clilang.fmt(f"מוכן: אפשר לתייג v{version}.", f"READY: you can tag v{version}."))
    return 0


def add_cli(sub) -> None:
    rp = sub.add_parser("release", help="release sign-off: record your manual test of a stable release")
    rp.add_argument("action", nargs="?", default="status", choices=["status", "sheet", "signoff", "run"],
                    help="status: every gate + what remains (default); sheet: write the reduced owner sheet; "
                         "signoff: show / fill / finish the manual test sheet; "
                         "run: evidence, then (with --approved-by-owner) sign and print the tag commands")
    rp.add_argument("--version", help="the stable version being released, e.g. 4.1.0 (default: core/pyproject.toml)")
    rp.add_argument("--report", action="store_true", help="status: also write docs/release/EVIDENCE_<version>.md")
    rp.add_argument("--force", action="store_true", help="sheet: rebuild even if it holds results or a signature")
    rp.add_argument("--row", help="a sheet row id to record (with --result)")
    rp.add_argument("--result", choices=[r for r in so.RESULTS if r != "pending"], default="pass")
    rp.add_argument("--note", help="note for the row (required for accepted-blocked)")
    rp.add_argument("--finish", action="store_true",
                    help="write the tested beta, its commit SHA and today's date; only when every row is done")
    rp.add_argument("--beta", help="finish: the beta tested, e.g. 4.1.0-beta.7 (default: this installed beta)")
    rp.add_argument("--commit", help="finish: full SHA of the tested beta (default: this build's commit)")
    rp.add_argument("--owner", help="finish: the owner's name recorded in the sheet (default: this account)")
    rp.add_argument("--approved-by-owner", metavar="QUOTE", help="run: the owner's approval (chat quote or time UTC); "
                    "without it nothing is signed")
    rp.add_argument("--dry-run", action="store_true", help="run: same checks, writes and signs nothing")
    rp.add_argument("--repo", help="the git checkout holding docs/reviews (default: auto-detected)")


# ---------------------------------------------------------------------- feature + dashboard
def _register_feature() -> None:
    from . import features
    if "release_signoff" in features.CATALOG:
        return
    import inspect
    kw = {"targets": {"windows": {"status": "implemented_unverified",
                                  "evidence": "core/tests/test_release_signoff.py (sheet round trip, sign-off, gate)"},
                      "linux_arm64_pi": {"status": "planned", "blocker": "release tooling runs on the owner's PC",
                                         "roadmap_issue": "https://github.com/Jarvis-igerman-ai/jarves/issues/3"}}} \
        if "targets" in inspect.signature(features.register).parameters else {}
    features.register("release_signoff", "אישור גרסה (בדיקה ידנית)", "Release sign-off (manual test)", True, risk="low",
                      about="the owner's checklist for a stable release: test the latest beta by hand, record the "
                            "results, sign off; a stable tag is refused without it", **kw)


_register_feature()


def _ver(body_or_q) -> str:
    v = (body_or_q or {}).get("version") if isinstance(body_or_q, dict) else None
    return so.normalise_version(str(v or ""))


def api_signoff(ctx, m, body, q=None):
    v = (q or {}).get("version")
    v = v[0] if isinstance(v, list) and v else (v or "")
    try:
        version = so.normalise_version(str(v))
        root, sheet = _mine(ctx.home, None, version)
    except so.SheetError as e:
        return 404, {"error": str(e)}
    return 200, {**summary(sheet), "file": str(so.sheet_path(root, version))}


def api_signoff_set(ctx, m, body):
    """POST {version, row, result, note} records one row; {version, finish: true} signs the sheet."""
    body = body or {}
    try:
        version = _ver(body)
        root, sheet = _mine(ctx.home, None, version)
        if body.get("finish") is True:
            _finish(sheet, ctx.home, body.get("beta"), body.get("commit"), body.get("owner"))
        else:
            sheet.set_result(str(body.get("row") or ""), str(body.get("result") or ""), str(body.get("note") or ""))
        so.save(root, sheet)
    except so.SheetError as e:
        return 409, {"error": str(e)}
    return 200, summary(sheet)


def dashboard_routes(register_route) -> None:
    register_route("GET", r"/api/release/signoff", api_signoff)
    register_route("POST", r"/api/release/signoff", api_signoff_set)

