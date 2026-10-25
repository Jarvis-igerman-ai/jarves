"""Build the log-code index for root-cause hints (SW-V4 package O, Self-Diagnostics 2.0).

    python tools/build_log_index.py [--src src/jarves] [--out src/jarves/diag/log_codes.json] [--no-blame]

Scans every `logs.info|warn|error|debug|verbose|log(component, CODE, msg, ...)` call (and the local aliases
`log(...)`, `log = logs.info if ...`) with the AST, and writes code -> [file:line, function, component, level, the
message template, the last commit that touched that line]. The commit comes from `git blame` HERE, at build time;
the running JARVES never calls git. A message template keeps only the literal text of the call (f-string
placeholders become {}), never runtime values, so the index is safe to ship and to show to the AI reviewer.
The packaging build (packaging/build_windows.ps1) runs this before the wheel is built.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import time
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
CODE_RX = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
LEVELS = {"info": "INFO", "warn": "WARNING", "warning": "WARNING", "error": "ERROR", "debug": "DEBUG",
          "verbose": "VERBOSE", "log": "?"}
REPO = "Jarvis-igerman-ai/jarves"           # the public repo (permalinks); private notes live elsewhere


def _template(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)
    return "{}"


def _func_name(call: ast.Call) -> str | None:
    f = call.func
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in ("logs", "_logs") \
            and f.attr in LEVELS:
        return f.attr
    if isinstance(f, ast.Name) and f.id in ("log", "_log"):
        return "log"
    return None


def scan_file(path: Path, rel: str) -> list[dict]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return []
    out: list[dict] = []
    stack: list[str] = []

    class V(ast.NodeVisitor):
        def _scope(self, node):
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scope

        def visit_Call(self, node: ast.Call) -> None:
            name = _func_name(node)
            if name and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) \
                    and isinstance(node.args[1].value, str) and CODE_RX.match(node.args[1].value) \
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                level = LEVELS[name]
                if name == "log":
                    for kw in node.keywords:
                        if kw.arg == "level" and isinstance(kw.value, ast.Attribute):
                            level = {"ERROR": "ERROR", "WARNING": "WARNING", "INFO": "INFO",
                                     "DEBUG": "DEBUG"}.get(kw.value.attr, "?")
                    if len(node.args) >= 4 and isinstance(node.args[3], ast.Attribute):
                        level = {"ERROR": "ERROR", "WARNING": "WARNING", "INFO": "INFO",
                                 "DEBUG": "DEBUG"}.get(node.args[3].attr, "?")
                out.append({"code": node.args[1].value, "file": rel, "line": node.lineno,
                            "function": ".".join(stack) or "<module>", "component": node.args[0].value,
                            "level": level, "msg": _template(node.args[2])[:160] if len(node.args) >= 3 else ""})
            self.generic_visit(node)

    V().visit(tree)
    return out


def blame(repo_root: Path, rel_to_root: str) -> dict[int, str]:
    """line -> commit (short) of the last change, from one `git blame --porcelain` per file."""
    try:
        p = subprocess.run(["git", "blame", "--porcelain", "--", rel_to_root], cwd=repo_root, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if p.returncode != 0:
        return {}
    out: dict[int, str] = {}
    for line in p.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and re.fullmatch(r"[0-9a-f]{40}", parts[0]):
            try:
                out[int(parts[2])] = parts[0][:12]
            except ValueError:
                pass
    return out


def git_head(repo_root: Path) -> str:
    try:
        p = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, timeout=30)
        return p.stdout.strip()[:12] if p.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def build(src: Path, *, use_blame: bool = True, repo_root: Path | None = None) -> dict:
    src = src.resolve()
    repo_root = (repo_root or CORE.parent).resolve()
    codes: dict[str, list[dict]] = {}
    for path in sorted(src.rglob("*.py")):
        rel = path.relative_to(src.parent).as_posix()          # "jarves/app.py"
        found = scan_file(path, rel)
        if not found:
            continue
        lines = {}
        if use_blame:
            try:
                lines = blame(repo_root, path.relative_to(repo_root).as_posix())
            except ValueError:
                lines = {}
        for e in found:
            e["commit"] = lines.get(e["line"], "")
            codes.setdefault(e.pop("code"), []).append(e)
    try:
        src_prefix = src.parent.relative_to(repo_root).as_posix()     # "core/src"
    except ValueError:
        src_prefix = ""
    return {"schema": 1, "generated": round(time.time()), "repo": REPO, "head": git_head(repo_root) if use_blame else "",
            "src_prefix": src_prefix, "codes": dict(sorted(codes.items()))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", type=Path, default=CORE / "src" / "jarves")
    ap.add_argument("--out", type=Path, default=CORE / "src" / "jarves" / "diag" / "log_codes.json")
    ap.add_argument("--no-blame", action="store_true", help="skip git blame (no commit per line)")
    a = ap.parse_args(argv)
    idx = build(a.src, use_blame=not a.no_blame)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(idx, ensure_ascii=False, indent=0), encoding="utf-8")
    n = sum(len(v) for v in idx["codes"].values())
    print(f"log index: {len(idx['codes'])} codes, {n} call sites -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
