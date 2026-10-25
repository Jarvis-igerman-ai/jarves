"""Which test files does a change affect? (fix-forward on a red main: core/tools/red_main.py; speed item S1 in #160)

The PR run executes only the affected files; the batch run on `next` always runs everything, so a wrong guess can only
delay a failure by one batch, never ship it. Files are deselected, never skipped (the zero-skips rule is unchanged).

    python tools/ci_affected.py --base origin/next         # changed files from git diff, prints "all" or file names
    python tools/ci_affected.py --files core/src/jarves/timers.py core/tests/test_x.py

Rules: static import graph (AST) of core/src/jarves and core/tests; a test is affected when it, a test helper it imports
or any jarves module in its import closure changed. Lock files, workflow files, CI tools, conftest.py and test helpers
shared by many tests select everything. A few data-file rules map non-code files to their tests.
"""
from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
SRC = CORE / "src"
TESTS = CORE / "tests"

ALL_PREFIXES = (".github/", "core/tools/ci_", "core/tools/netguard.py", "core/tools/pytest_ci.py")
ALL_FILES = ("core/uv.lock", "core/pyproject.toml", "core/tests/conftest.py", "core/tests/durations.json")
DATA_RULES = (  # (path prefix, test file globs relative to core/tests)
    ("core/src/jarves/guides/data/", ("test_guides*.py", "test_cli_parity.py")),
    ("core/src/jarves/dashboard_static/", ("test_dashboard*.py", "test_browser.py", "test_canary.py")),
    ("core/src/jarves/hebrew", ("test_hebrew*.py", "test_dashboard_locales.py", "test_guides*.py")),
    ("docs/", ("test_docs_links.py",)),
    ("packaging/", ("test_packaging*.py", "test_installer*.py", "test_release*.py")),
)


def _module_name(path: Path) -> str:
    rel = path.relative_to(SRC).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imports(path: Path, own: str, is_pkg: bool, known: set[str]) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set()
    out: set[str] = set()
    pkg = own if is_pkg else own.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                up = pkg.split(".")[: len(pkg.split(".")) - (node.level - 1)] if pkg else []
                base = ".".join([*up, *([node.module] if node.module else [])])
            out.add(base)
            for a in node.names:
                out.add(f"{base}.{a.name}")
    return {m for m in out if m in known or m in _helpers()}


_HELP: set[str] | None = None


def _helpers() -> set[str]:
    global _HELP
    if _HELP is None:
        _HELP = {p.stem for p in TESTS.glob("*.py") if not p.name.startswith("test_")}
    return _HELP


def build_graph() -> tuple[dict[str, set[str]], dict[str, Path]]:
    """node -> direct imports. Nodes: jarves modules ('jarves.x') and test/helper files ('tests/<name>.py')."""
    mods = {_module_name(p): p for p in SRC.rglob("*.py")}
    known = set(mods)
    graph: dict[str, set[str]] = {}
    paths: dict[str, Path] = {}
    for name, p in mods.items():
        graph[name] = _imports(p, name, p.name == "__init__.py", known)
        paths[name] = p
    for p in sorted(TESTS.glob("*.py")):
        node = f"tests/{p.name}"
        deps = _imports(p, "tests." + p.stem, False, known)
        graph[node] = {(f"tests/{d}.py" if d in _helpers() else d) for d in deps}
        paths[node] = p
    return graph, paths


def closure(graph: dict[str, set[str]], start: str) -> set[str]:
    seen, todo = set(), [start]
    while todo:
        n = todo.pop()
        if n in seen:
            continue
        seen.add(n)
        todo.extend(graph.get(n, ()))
    return seen


def affected(changed: list[str]) -> list[str] | None:
    """Test file names (relative to core/tests) or None meaning 'run everything'."""
    changed = [c.replace("\\", "/") for c in changed]
    if any(c in ALL_FILES or c.startswith(ALL_PREFIXES) for c in changed):
        return None
    graph, _ = build_graph()
    helpers = {f"tests/{h}.py" for h in _helpers()}
    changed_nodes: set[str] = set()
    picked: set[str] = set()
    for c in changed:
        if c.startswith("core/src/jarves/") and c.endswith(".py"):
            changed_nodes.add(_module_name(CORE.parent / c))
        elif c.startswith("core/tests/") and c.endswith(".py"):
            name = c.rsplit("/", 1)[1]
            if name.startswith("test_"):
                picked.add(name)
            else:
                changed_nodes.add(f"tests/{name}")
        for prefix, globs in DATA_RULES:
            if c.startswith(prefix):
                for g in globs:
                    picked.update(p.name for p in TESTS.glob(g))
    for node in graph:
        if node.startswith("tests/") and node not in helpers:
            if closure(graph, node) & changed_nodes:
                picked.add(node.split("/", 1)[1])
    return sorted(picked)


def changed_files(base: str) -> list[str]:
    out = subprocess.run(["git", "diff", "--name-only", f"{base}...HEAD"], capture_output=True, text=True, check=True,
                         cwd=CORE.parent).stdout
    return [line for line in out.splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="origin/next")
    ap.add_argument("--files", nargs="*")
    args = ap.parse_args(argv)
    files = args.files if args.files is not None else changed_files(args.base)
    result = affected(files)
    print("all" if result is None else " ".join(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
