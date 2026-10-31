"""Risk classification of a pull request for the auto-label workflow (.github/workflows/autolabel.yml).

Standard library only, no network. LOW-RISK PRs get the `automerge` label, so automerge.yml puts them in the merge queue as soon
as `CI result (single required check)` is green; this tool never merges and never bypasses a check.

    python tools/risk_class.py --files-from files.txt --lines 42 --labels bugfix,foo --author octocat --mode true
    -> prints one line "automerge <class>: <reason>" or "none: <reason>" (exit 0 = label, 1 = do not label)

Classes (docs/RELEASING.md, section "Automatic labelling of low-risk PRs"):
  docs      only docs/, *.md, CHANGELOG, DOC_MAP
  tests     only core/tests/test_*.py (no conftest.py, no shared helper files), docs allowed
  guides    only guides data (core/src/jarves/guides/data/*.toml) and dashboard locales / translation catalogs
  bugfix    label `bugfix`, fewer than 150 changed lines, no high-risk path   (mode `all` only)
Dependabot PRs are handled by dependabot-automerge.yml (patch/minor get `automerge`, major get `needs-review`).
HIGH-RISK paths never get the label, whatever the class. Labels `needs-review` and `no-automerge` block everything.
Modes (repository variable ENABLE_AUTOLABEL): unset/"true" = docs, tests, guides; "all" = also bugfix; "false" = off.
"""
from __future__ import annotations

import argparse
import fnmatch
import sys

HIGH_RISK = (
    ".github/*", "packaging/*", "CLAUDE.md", "AGENTS.md",
    "core/pyproject.toml", "core/uv.lock", "uv.lock", "*/uv.lock", "pyproject.toml",
    "core/src/jarves/approvals*", "core/src/jarves/*/approvals*", "core/src/jarves/helper*", "core/src/jarves/*/helper*",
    "core/src/jarves/isolation*", "core/src/jarves/winsandbox*", "core/src/jarves/mobile*", "core/src/jarves/secrets*",
    "core/src/jarves/dashboard_auth*", "core/src/jarves/privacy*", "core/src/jarves/permissions*",
    "core/src/jarves/updates*", "core/src/jarves/signing*", "core/src/jarves/security*", "core/src/jarves/redact*",
    "core/tools/release*", "core/tools/promote*", "core/tools/ci_*", "core/tools/risk_class.py", "core/tools/netguard.py",
    "core/tools/dependabot_gate.py", "core/tools/manual_test_sheet.py",
    "core/tests/conftest.py", "core/tests/_tools.py", "core/tests/durations*",
    "core/src/jarves/hotfix*", "core/tools/hotfix.py", "core/src/jarves/install_latest.py", "core/src/jarves/release_signoff.py",
    "core/src/jarves/browser*", "core/src/jarves/skills/browser.py", "core/src/jarves/mobile_browser.py", "core/src/jarves/voice/wake_*",
    ".claude/*", "core/src/jarves/workflow/hooks.py", "core/src/jarves/workflow/bootstrap.py",
    "core/tools/workflow_hook.py", "core/tools/workflow_service.ps1",
)
BLOCKING_LABELS = {"needs-review", "no-automerge", "do-not-merge", "wip"}
GUIDES = ("core/src/jarves/guides/data/*.toml",)
LOCALES = ("core/src/jarves/dashboard_static/locales/*", "core/src/jarves/locales/*", "core/src/jarves/*catalog*.json",
           "core/src/jarves/hebrew_catalog*")
BUGFIX_MAX_LINES = 150


def _m(path: str, patterns) -> bool:
    return any(fnmatch.fnmatchcase(path, p) for p in patterns)


def is_high_risk(path: str) -> bool:
    return _m(path, HIGH_RISK)


def is_docs(path: str) -> bool:
    name = path.rsplit("/", 1)[-1].lower()
    if name in ("claude.md", "agents.md") or path.startswith(".claude/"):
        return False                      # instruction files steer agents: never "just docs"
    return path.startswith("docs/") or name.endswith(".md") or name.startswith("changelog") or name == "doc_map.md"


def is_test(path: str) -> bool:
    return path.startswith("core/tests/") and not is_high_risk(path) and path.rsplit("/", 1)[-1].startswith("test_")


def is_guide_or_locale(path: str) -> bool:
    return _m(path, GUIDES) or _m(path, LOCALES)


def classify(files, lines: int = 0, labels=frozenset(), author: str = "", mode: str = "true"):
    """(class or None, reason). None = do not label."""
    files = [f.replace("\\", "/") for f in files if f]
    labels = {x.lower() for x in labels}
    if mode == "false":
        return None, "ENABLE_AUTOLABEL=false"
    if labels & BLOCKING_LABELS:
        return None, f"blocked by label {sorted(labels & BLOCKING_LABELS)[0]}"
    if author.lower().startswith("dependabot"):
        return None, "dependabot PRs are handled by dependabot-automerge.yml"
    if not files:
        return None, "no changed files"
    risky = [f for f in files if is_high_risk(f) and not is_docs(f)]
    if risky:
        return None, f"high-risk path: {risky[0]}"
    if all(is_docs(f) for f in files):
        return "docs", "docs only"
    if all(is_docs(f) or is_test(f) for f in files):
        return "tests", "tests (and docs) only"
    if all(is_docs(f) or is_guide_or_locale(f) for f in files):
        return "guides", "guides / locales only"
    if mode == "all" and "bugfix" in labels:
        if lines < BUGFIX_MAX_LINES:
            return "bugfix", f"bugfix label, {lines} changed lines, no high-risk path"
        return None, f"bugfix with {lines} >= {BUGFIX_MAX_LINES} changed lines"
    return None, "not a low-risk class"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--files-from", required=True, help="file with one changed path per line")
    ap.add_argument("--lines", type=int, default=0)
    ap.add_argument("--labels", default="")
    ap.add_argument("--author", default="")
    ap.add_argument("--mode", default="true")
    a = ap.parse_args(argv)
    with open(a.files_from, encoding="utf-8") as fh:
        files = [line.strip() for line in fh if line.strip()]
    cls, why = classify(files, a.lines, {x for x in a.labels.split(",") if x}, a.author, a.mode or "true")
    print(f"automerge {cls}: {why}" if cls else f"none: {why}")
    return 0 if cls else 1


if __name__ == "__main__":
    sys.exit(main())
