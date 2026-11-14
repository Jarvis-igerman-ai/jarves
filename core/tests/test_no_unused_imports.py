"""JAR-91 quality drift: modules cleaned of dead stdlib imports stay clean (hermetic AST check, no ruff needed)."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "jarves"
CLEANED = ("diag/preflight.py", "diag/service.py", "perf_advisor.py", "triage.py")


def _unused_top_level_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                imported[(a.asname or a.name).split(".")[0]] = node.lineno
        elif isinstance(node, ast.ImportFrom) and node.module != "__future__":
            for a in node.names:
                imported[a.asname or a.name] = node.lineno
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    exported: set[str] = set()
    for node in tree.body:  # names listed in __all__ count as used
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "__all__" for t in node.targets):
            exported |= {e.value for e in getattr(node.value, "elts", []) if isinstance(e, ast.Constant)}
    return sorted(f"{name} (line {line})" for name, line in imported.items() if name not in used | exported)


@pytest.mark.parametrize("rel", CLEANED)
def test_no_unused_top_level_imports(rel):
    assert _unused_top_level_imports(SRC / rel) == []
