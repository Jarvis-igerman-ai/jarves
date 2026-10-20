"""AST lint for every `logs.*` call (SW-V4 package B, issue #15).

Rules (a violation fails `tests/test_log_message_lint.py`):
  1. The message (3rd positional argument, or `msg=`) is a plain string literal: no f-string, no `%`/`.format`,
     no concatenation with variables, no variable. Every variable part goes into a keyword field.
  2. The component and the code (1st/2nd positional) are string literals too (codes are greppable IDs).
  3. Every keyword field name is classified in `logs.FIELD_CLASS` (safe/secret/content/pii).
  4. `**expansion` is allowed only for the helpers listed in ALLOWED_EXPANSIONS (they return classified keys).

Usage: python tools/log_lint.py [--json] [--fields]   (prints violations; exit 1 when there are any)
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "jarves"
LEVEL_FUNCS = {"info", "warn", "error", "debug", "verbose", "log"}
# Keyword arguments of the logs.* functions themselves, never log fields.
CALL_KWARGS = {"exc", "level"}
# Helpers whose result may be spread with ** (they only emit registered / safe keys).
ALLOWED_EXPANSIONS = {"logs.safe_fields", "safe_fields", "logs.error_fields", "error_fields", "logs.exc_fields", "logs.numeric_fields",
                      "usage_fields", "brain.usage_fields", "self._take_reply_wait"}


def _is_logs_call(node: ast.Call, aliases: set[str]) -> bool:
    f = node.func
    return (isinstance(f, ast.Attribute) and f.attr in LEVEL_FUNCS and isinstance(f.value, ast.Name)
            and f.value.id in aliases)


def _aliases(tree: ast.AST) -> set[str]:
    """Names under which `jarves.logs` is imported in this module (`from . import logs`, `... as _logs`)."""
    out: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            for a in n.names:
                if a.name == "logs":
                    out.add(a.asname or "logs")
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.name.endswith(".logs") and a.asname:
                    out.add(a.asname)
    return out


def _literal(n: ast.AST | None) -> bool:
    return isinstance(n, ast.Constant) and isinstance(n.value, str)


def _call_name(n: ast.AST) -> str:
    if isinstance(n, ast.Call):
        f = n.func
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
            return f"{f.value.id}.{f.attr}"
        if isinstance(f, ast.Name):
            return f.id
    return ""


def _code_ok(n: ast.AST | None) -> bool:
    """A code is a literal, a choice between literals, or a structured error code (`e.code`, `code`)."""
    if _literal(n):
        return True
    if isinstance(n, ast.IfExp):
        return _code_ok(n.body) and _code_ok(n.orelse)
    if isinstance(n, ast.Attribute) and n.attr in ("code", "error_code"):
        return True
    if isinstance(n, ast.JoinedStr):              # f"{self.prefix}_DONE" / f"JOB_{state.upper()}": composed codes
        return any(_literal(v) for v in n.values)
    if isinstance(n, ast.BoolOp):                 # code or f"HTTP_{status}"
        return all(_code_ok(v) for v in n.values)
    if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):   # "GRANT_" + op.upper()
        return _literal(n.left)
    return isinstance(n, ast.Name) and n.id in ("code", "error_code")


def _dict_keys(n: ast.AST) -> list[str] | None:
    """Keys of `**{"a": x}` / `**({"a": x} if c else {})`; None when the keys are not all string literals."""
    if isinstance(n, ast.Dict):
        if all(_literal(k) for k in n.keys):
            return [k.value for k in n.keys]
        return None
    if isinstance(n, ast.IfExp):
        a, b = _dict_keys(n.body), _dict_keys(n.orelse)
        return None if a is None or b is None else a + b
    return None


def scan_file(path: Path, registry: dict[str, str] | None = None) -> tuple[list[dict], list[dict]]:
    """(call sites, violations) of one file."""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))
    src_lines = src.splitlines()
    aliases = _aliases(tree) or set()
    if path.name == "logs.py":
        aliases = aliases | {"logs"}
    calls, bad = [], []
    rel = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)

    def is_level_ref(n: ast.AST) -> bool:
        """`logs.info` (not called), or a choice between such references."""
        if isinstance(n, ast.IfExp):
            return is_level_ref(n.body) and is_level_ref(n.orelse)
        return (isinstance(n, ast.Attribute) and n.attr in LEVEL_FUNCS and isinstance(n.value, ast.Name)
                and n.value.id in aliases)
    # names bound to a logs function: `level = logs.info if ok else logs.warn` -> level(...) is a logs call
    level_names = {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign) and is_level_ref(n.value)
                   for t in n.targets if isinstance(t, ast.Name)}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        pos = list(node.args)
        if _is_logs_call(node, aliases):
            fn = node.func.attr
        elif isinstance(node.func, ast.Name) and node.func.id in level_names:
            fn = "indirect"
        elif any(is_level_ref(a) for a in pos):
            # wrapper: self._log(logs.info, CODE, msg, **f) -> component supplied by the wrapper
            i = next(i for i, a in enumerate(pos) if is_level_ref(a))
            fn, pos = "wrapper", [ast.Constant("<wrapper>")] + pos[i + 1:]
        else:
            continue
        comp = pos[0] if len(pos) > 0 else None
        code = pos[1] if len(pos) > 1 else None
        msg = pos[2] if len(pos) > 2 else next((k.value for k in node.keywords if k.arg == "msg"), None)
        fields = [k.arg for k in node.keywords if k.arg and k.arg not in CALL_KWARGS and k.arg != "msg"]
        site = {"file": rel, "line": node.lineno, "func": fn,
                "component": comp.value if _literal(comp) else None,
                "code": code.value if _literal(code) else None,
                "msg": msg.value if _literal(msg) else None, "fields": fields}
        calls.append(site)

        # pragmas on the call's lines: "log-lint: wrapper" (msg/code are parameters checked at the wrapper's
        # callers) and "log-lint: fields=a,b" (the keys a non-literal ** expansion can contribute)
        text = "\n".join(src_lines[max(0, node.lineno - 2):node.end_lineno])      # the call and the line above
        wrapper = "log-lint: wrapper" in text
        declared = re.search(r"log-lint:[^\n]*?fields=([\w,]+)", text)
        if declared:
            fields += declared.group(1).split(",")

        def v(rule: str, detail: str = "") -> None:
            if wrapper and rule in ("msg-not-literal", "code-not-literal", "component-not-literal"):
                return
            if declared and rule == "star-expansion":
                return
            bad.append({"file": rel, "line": node.lineno, "rule": rule, "detail": detail})
        if not (_literal(comp) or isinstance(comp, (ast.Name, ast.Attribute))):
            v("component-not-literal")
        if not _code_ok(code):
            v("code-not-literal", ast.unparse(code)[:60] if code is not None else "")
        if msg is None:
            v("msg-missing")
        elif not _literal(msg):
            v("msg-not-literal", type(msg).__name__)
        if fn in ("info", "warn", "debug", "verbose", "error") and any(k.arg in ("level", "component", "code") for k in node.keywords):
            v("reserved-keyword", "level/component/code clash with the logs.* parameters")
        if len(pos) > 3 and not (fn == "log" and len(pos) == 4):
            v("extra-positional")
        for k in node.keywords:
            if k.arg is None:
                keys = _dict_keys(k.value)
                if keys is None and _call_name(k.value) not in ALLOWED_EXPANSIONS:
                    v("star-expansion", ast.unparse(k.value)[:60])
                fields += keys or []
                site["fields"] = fields
        if registry is not None:
            for f in fields:
                if f not in registry:
                    v("field-not-registered", f)
    return calls, bad


def scan(registry: dict[str, str] | None = None, root: Path = SRC) -> tuple[list[dict], list[dict]]:
    calls, bad = [], []
    for p in sorted(root.rglob("*.py")):
        c, b = scan_file(p, registry)
        calls += c
        bad += b
    return calls, bad


def main(argv: list[str]) -> int:
    sys.path.insert(0, str(ROOT / "src"))
    from jarves import logs
    calls, bad = scan(logs.FIELD_CLASS if hasattr(logs, "FIELD_CLASS") else None)
    if "--fields" in argv:
        names: dict[str, int] = {}
        for c in calls:
            for f in c["fields"]:
                names[f] = names.get(f, 0) + 1
        print(json.dumps(dict(sorted(names.items())), indent=1))
        return 0
    if "--json" in argv:
        print(json.dumps({"calls": len(calls), "violations": bad}, indent=1, ensure_ascii=False))
    else:
        for b in bad:
            print(f"{b['file']}:{b['line']}: {b['rule']} {b['detail']}")
        print(f"{len(calls)} logs.* call sites, {len(bad)} violations")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
