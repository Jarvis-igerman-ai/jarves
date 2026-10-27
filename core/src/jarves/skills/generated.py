"""Generated Skills with owner approval (SW-V2 M2, spec 6.2 / 9 / D15). Extends the ONE SkillRegistry: an installed
generated Skill is an ordinary `Skill` whose tools are validated, permission-checked and gated like every other.

Lifecycle (docs/SKILL_CREATION.md):
  propose   JARVES (model tool `skill_propose`) or the owner writes SKILL.md (+ optional skill.py) into an isolated
            draft dir  <state_dir>/skills/drafts/<id>/ . Nothing runs, nothing is registered.
  validate  static checks only (never executes the code): manifest, requested capabilities, tool permissions, AST
            (allow-listed imports, no eval/exec/open/getattr/dunder or private attributes, no top-level code, no
            calendar deletion / mail sending), sizes. The report + a human summary are shown to the owner.
  approve   ONLY a user route (dashboard / CLI), with the sha256 of exactly the content the owner reviewed. Installs
            version N into  installed/<id>/versions/<N>/  DISABLED, with no grants. The model has no approve tool.
  grant     the owner grants a SUBSET of the requested capabilities (storage, network:<host>) and, separately,
            "actions" for the Skill's action tools. Nothing else can be granted.
  enable / disable / rollback (previous version, disabled again, grants cleared) / uninstall (moved to removed/).

A Markdown instruction alone never grants anything: the body is served by a read tool `<id>_guide`, and every tool
it mentions keeps its own permission. Disabled Skills cannot run from ANY route: the registry hides and refuses
them (SKILL_DISABLED: voice, model tools, scheduler, offline commands) and `run()` re-reads the state on every call
(dashboard test button, a stale registry). Code runs in a separate process per call (sandbox_child.py): empty
environment (no keys), restricted builtins and imports, POSIX rlimits, hard timeout; storage / network go through
`_Broker`, which checks the grants. AST validation is not a perfect sandbox; the layers together are the defence
and an OS sandbox on Linux is tracked for the Pi (issue #21).
"""
from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, create_model

from .. import features, isolation, logs, winsandbox
from . import Skill, Tool, ToolResult

ROOT = "skills"
ID_RX = re.compile(r"^[a-z][a-z0-9_]{2,31}$")
HOST_RX = re.compile(r"^(?=.{4,253}$)(?!-)[a-z0-9-]{1,63}(?:\.[a-z0-9-]{1,63})*\.[a-z]{2,63}$")
MAX_MD, MAX_PY, MAX_TOOLS, MAX_ARGS = 32_000, 64_000, 8, 8
MAX_STORAGE = 256_000
MAX_HTTP = 200_000
CALL_TIMEOUT_S = 10.0
MAX_RPC = 50
USER_ROUTES = ("dashboard", "cli", "user")          # who may approve / grant / enable (never "voice" or "model")
# `string` is NOT here: string.Formatter().get_field("0._os.system", (random,), {}) walks attributes out of the
# sandbox (get_field/get_value use getattr on the field name) and passes a naive AST check. Anything that formats
# with a runtime field name is a getattr primitive and stays out.
ALLOWED_IMPORTS = ("json", "math", "re", "datetime", "statistics", "random", "textwrap", "itertools",
                   "functools", "collections", "decimal", "fractions", "unicodedata", "calendar", "zoneinfo")
FORBIDDEN_NAMES = frozenset({"eval", "exec", "compile", "open", "__import__", "globals", "locals", "vars", "getattr",
                             "setattr", "delattr", "input", "breakpoint", "help", "memoryview", "exit", "quit",
                             "type", "object", "super", "classmethod", "staticmethod", "property", "dir", "id",
                             "print", "bytearray", "bytes", "__builtins__", "__loader__", "__spec__", "__file__",
                             "Formatter"})       # string.Formatter is a getattr primitive (see ALLOWED_IMPORTS)
# attribute names that lead out of the allowed modules (typing.sys, enum.sys...) or into frames / code objects, and
# any getattr-by-string route: str.format*/Formatter walk a field path with getattr; operator.*getter build one.
FORBIDDEN_ATTRS = frozenset({"sys", "os", "modules", "builtins", "inspect", "importlib", "subprocess", "socket",
                             "ctypes", "marshal", "pickle", "shutil", "system", "popen", "spawn", "loader", "spec",
                             "code", "frame", "globals", "locals", "copyreg", "enum", "mro", "format", "format_map",
                             "f_back", "f_globals", "f_locals", "f_builtins", "gi_frame", "gi_code", "cr_frame",
                             "co_code", "tb_frame", "tb_next", "func_globals", "register", "exec", "eval", "compile",
                             "open", "getattr", "setattr", "delattr", "vars", "type",
                             "get_field", "get_value", "vformat", "convert_field", "format_field", "parse",
                             "attrgetter", "methodcaller", "itemgetter"})
CTX_METHODS = {"storage_get": "storage", "storage_set": "storage", "http_get": "network", "now": None, "log": None}
ARG_TYPES = {"string": str, "integer": int, "number": float, "boolean": bool}
# D05 + mail policy: a generated Skill can never delete calendar events or send mail, whatever it is granted.
BANNED_RX = re.compile(r"calendar[_ ]?delete|delete[_ ]?(?:calendar[_ ]?)?event|events\(\)\s*\.\s*delete|"
                       r"gmail[_ ]?send|send[_ ]?(?:e-?)?mail", re.IGNORECASE)
INJECTION_RX = re.compile(r"ignore (?:all |the )?(?:previous|above|prior) instructions|system prompt|"
                          r"התעלם מ(?:כל )?ההוראות", re.IGNORECASE)
FRONT_KEYS = {"name", "title", "description", "version", "capabilities"}
V2_KEYS = {"manifest", "min_core", "permissions", "dependencies", "surfaces", "signed_by", "signature"}


class SkillError(ValueError):
    pass


# ---------------------------------------------------------------------- manifest
def parse_skill_md(text: str) -> tuple[dict, str]:
    """SKILL.md = '---' frontmatter (flat `key: value` / `key: [a, b]`, the agent-skills convention) + Markdown body."""
    if not text.startswith("---"):
        raise SkillError("SKILL.md must start with a '---' frontmatter block")
    end = text.find("\n---", 3)
    if end < 0:
        raise SkillError("SKILL.md frontmatter is not closed with '---'")
    meta: dict[str, Any] = {}
    for raw in text[3:end].splitlines():
        line = raw.split(" #", 1)[0].strip()
        if not line:
            continue
        key, sep, val = line.partition(":")
        if not sep:
            raise SkillError(f"frontmatter line is not 'key: value': {line[:60]}")
        key, val = key.strip().lower(), val.strip()
        if val.startswith("[") and val.endswith("]"):
            meta[key] = [v.strip().strip("'\"") for v in val[1:-1].split(",") if v.strip()]
        else:
            meta[key] = val.strip("'\"")
    return meta, text[end + 4:].lstrip("\n")


def _literal_tools(tree: ast.Module) -> tuple[list | None, list[str]]:
    errs, found = [], None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "TOOLS" for t in node.targets):
            try:
                found = ast.literal_eval(node.value)
            except ValueError:
                errs.append("TOOLS must be a plain literal list of dicts")
    return found, errs


def _check_code(src: str, skill_id: str, caps: set[str]) -> tuple[list[dict], list[str], list[str]]:
    """Static analysis only. Returns (tools, errors, warnings)."""
    errs: list[str] = []
    warns: list[str] = []
    try:
        tree = ast.parse(src, "skill.py")
    except SyntaxError as e:
        return [], [f"skill.py does not parse: line {e.lineno}: {e.msg}"], warns
    funcs: dict[str, ast.FunctionDef] = {}
    for node in tree.body:                     # top level: imports, defs, constants, a docstring - no code that runs
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.FunctionDef):
            funcs[node.name] = node
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and not any(isinstance(n, (ast.Call, ast.Lambda, ast.Await))
                                                                     for n in ast.walk(node)):
            continue
        errs.append(f"line {node.lineno}: only imports, functions and constant values are allowed at top level "
                    f"({type(node).__name__})")
    has_net = any(c.startswith("network:") for c in caps)
    for node in ast.walk(tree):
        line = getattr(node, "lineno", "?")
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] not in ALLOWED_IMPORTS:
                    errs.append(f"line {line}: import {a.name} is not allowed")
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module or node.module.split(".")[0] not in ALLOWED_IMPORTS:
                errs.append(f"line {line}: from {node.module or '.'} import is not allowed")
            for a in node.names:
                if a.name == "*" or a.name.startswith("_"):
                    errs.append(f"line {line}: from-import of {a.name} is not allowed")
        elif isinstance(node, ast.Name):
            if node.id in FORBIDDEN_NAMES or node.id.startswith("__"):
                errs.append(f"line {line}: {node.id} is not allowed")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_") or node.attr in FORBIDDEN_ATTRS:
                errs.append(f"line {line}: attribute .{node.attr} is not allowed")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            errs.append(f"line {line}: global/nonlocal is not allowed")
        elif isinstance(node, (ast.AsyncFunctionDef, ast.Await, ast.AsyncFor, ast.AsyncWith, ast.ClassDef,
                               ast.Yield, ast.YieldFrom)):
            errs.append(f"line {line}: {type(node).__name__} is not allowed (plain functions only)")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and "__" in node.value:
            errs.append(f"line {line}: strings with '__' are not allowed")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) \
                and node.func.value.id == "ctx":
            need = CTX_METHODS.get(node.func.attr, "?")
            if need == "?":
                errs.append(f"line {line}: ctx.{node.func.attr} does not exist")
            elif need == "storage" and "storage" not in caps:
                errs.append(f"line {line}: ctx.{node.func.attr} needs the 'storage' capability in SKILL.md")
            elif need == "network" and not has_net:
                errs.append(f"line {line}: ctx.http_get needs a 'network:<host>' capability in SKILL.md")
    tools, terrs = _literal_tools(tree)
    errs += terrs
    out: list[dict] = []
    if not isinstance(tools, list) or not tools:
        errs.append("skill.py must define TOOLS = [{name, description, permission, args}, ...]")
        return out, errs, warns
    if len(tools) > MAX_TOOLS:
        errs.append(f"at most {MAX_TOOLS} tools")
    for t in tools[:MAX_TOOLS]:
        if not isinstance(t, dict):
            errs.append("each TOOLS entry must be a dict")
            continue
        name, perm = str(t.get("name", "")), t.get("permission")
        args = t.get("args") or {}
        if not re.fullmatch(rf"{skill_id}_[a-z0-9_]{{1,40}}", name) or name == f"{skill_id}_guide":
            errs.append(f"tool name {name!r} must be '{skill_id}_<word>' (and not {skill_id}_guide)")
        if perm == "destructive":
            errs.append(f"{name}: generated Skills may not have destructive tools")
        elif perm not in ("read", "action"):
            errs.append(f"{name}: permission must be read or action")
        if not isinstance(args, dict) or len(args) > MAX_ARGS or any(
                not isinstance(k, str) or not k.isidentifier() or k.startswith("_") or v not in ARG_TYPES
                for k, v in args.items()):
            errs.append(f"{name}: args must map up to {MAX_ARGS} names to {'/'.join(ARG_TYPES)}")
            args = {}
        fn = funcs.get(name)
        if fn is None:
            errs.append(f"{name}: no function def {name}(args, ctx)")
        elif len(fn.args.args) != 2 or fn.args.vararg or fn.args.kwarg or fn.args.kwonlyargs:
            errs.append(f"{name}: must be def {name}(args, ctx)")
        out.append({"name": name, "description": str(t.get("description", ""))[:300], "permission": perm,
                    "args": args})
    return out, errs, warns


def _lf(text: str) -> str:
    return (text or "").replace(chr(13) + chr(10), chr(10)).replace(chr(13), chr(10))


def _sha(md: str, py: str | None) -> str:
    h = hashlib.sha256(md.encode("utf-8"))
    h.update(b"\0")
    h.update((py or "").encode("utf-8"))
    return h.hexdigest()


def validate(md: str, py: str | None, *, taken_skills: set[str] = frozenset(), taken_tools: set[str] = frozenset(),
             upgrading: bool = False, v2: bool = False) -> dict:
    """Static validation report of one Skill draft (never executes anything). `v2` (the skills_manifest2 switch):
    a `manifest: 2` Skill may request storage / network:<host> under `permissions` too (still granted by the owner)."""
    errs: list[str] = []
    warns: list[str] = []
    if len(md.encode("utf-8")) > MAX_MD:
        errs.append(f"SKILL.md larger than {MAX_MD} bytes")
    if py is not None and len(py.encode("utf-8")) > MAX_PY:
        errs.append(f"skill.py larger than {MAX_PY} bytes")
    try:
        meta, body = parse_skill_md(md[:MAX_MD * 2])
    except SkillError as e:
        meta, body = {}, ""
        errs.append(str(e))
    sid = str(meta.get("name", ""))
    if not ID_RX.match(sid):
        errs.append("name must be 3-32 chars: lower-case letters, digits, '_' (it is the Skill id)")
    elif sid in taken_skills and not upgrading:
        errs.append(f"a Skill named {sid} already exists")
    if not str(meta.get("description", "")).strip():
        errs.append("description is required")
    is_v2 = v2 and str(meta.get("manifest", "")).strip() == "2"
    for k in set(meta) - FRONT_KEYS - (V2_KEYS if is_v2 else set()):
        warns.append(f"unknown frontmatter key {k!r} ignored")
    caps_raw = meta.get("capabilities", [])
    caps_raw = caps_raw if isinstance(caps_raw, list) else [caps_raw] if caps_raw else []
    if is_v2:                  # read/action are tool levels, not capabilities; anything else is checked below
        extra = meta.get("permissions", [])
        extra = extra if isinstance(extra, list) else [extra] if extra else []
        caps_raw = list(dict.fromkeys([*caps_raw, *(c for c in extra if str(c).strip().lower() not in ("read", "action"))]))
    caps: list[str] = []
    for c in caps_raw:
        c = str(c).strip().lower()
        if c == "storage":
            caps.append(c)
        elif c.startswith("network:") and HOST_RX.match(c[8:]) and not re.match(r"^[\d.]+$", c[8:]) \
                and c[8:] not in ("localhost",) and not c[8:].endswith((".local", ".lan", ".internal")):
            caps.append(c)
        else:
            errs.append(f"unknown or unsafe capability {c!r} (allowed: storage, network:<public host name>)")
    for text, where in ((body, "SKILL.md"), (py or "", "skill.py")):
        if BANNED_RX.search(text):
            errs.append(f"{where}: generated Skills may never delete calendar events or send mail")
        if INJECTION_RX.search(text):
            warns.append(f"{where}: contains prompt-injection-like wording; review the instructions carefully")
    tools: list[dict] = []
    if py is not None and py.strip():
        tools, cerrs, cwarns = _check_code(py, sid or "skill", set(caps))
        errs += cerrs
        warns += cwarns
    if body.strip():
        tools.append({"name": f"{sid}_guide", "description": f"Instructions of the {sid} Skill (read before using it)",
                      "permission": "read", "args": {}})
    if not tools:
        errs.append("a Skill needs instructions (Markdown body) or skill.py tools")
    clash = [t["name"] for t in tools if t["name"] in taken_tools]
    if clash:
        errs.append(f"tool names already used by another Skill: {', '.join(clash)}")
    perms = sorted({t["permission"] for t in tools if t.get("permission") in ("read", "action")})
    report = {"ok": not errs, "errors": errs, "warnings": warns, "id": sid, "title": str(meta.get("title") or sid),
              "description": str(meta.get("description", ""))[:500], "version_label": str(meta.get("version", "")),
              "capabilities": caps, "tools": tools, "permissions": perms, "has_code": bool(py and py.strip()),
              "sha256": _sha(md, py), "checked_at": time.time()}
    report["summary_he"], report["summary_en"] = _summaries(report)
    return report


_CAP_HE = {"storage": "אחסון מקומי קטן משלו"}
_TRUST_HE = {"unsigned": "לא חתום", "owner_signed": "חתום במפתח שלכם", "tampered": "חתימה פגומה"}


def _summaries(r: dict) -> tuple[str, str]:
    caps_he = ", ".join(_CAP_HE.get(c, f"גישה לרשת אל {c[8:]}") for c in r["capabilities"]) or "שום הרשאה"
    caps_en = ", ".join("own small local storage" if c == "storage" else f"network access to {c[8:]}"
                        for c in r["capabilities"]) or "nothing"
    tools_he = ", ".join(f"{t['name']} ({'קריאה' if t['permission'] == 'read' else 'פעולה'})" for t in r["tools"])
    tools_en = ", ".join(f"{t['name']} ({t['permission']})" for t in r["tools"])
    state_he = "עבר בדיקה" if r["ok"] else f"נכשל בבדיקה ({len(r['errors'])} בעיות)"
    state_en = "validation passed" if r["ok"] else f"validation FAILED ({len(r['errors'])} problems)"
    code_he = "כולל קוד פייתון" if r["has_code"] else "הוראות בלבד"
    code_en = "includes Python code" if r["has_code"] else "instructions only"
    m2 = r.get("manifest2") or {}
    trust_he = f" גרסה {m2['version']}, {_TRUST_HE.get(m2['trust'], m2['trust'])}." if m2 else ""
    trust_en = f" Version {m2['version']}, trust: {m2['trust']}." if m2 else ""
    return (f"כישור חדש: {r['title']} - {r['description']}. {code_he}. מבקש: {caps_he}. כלים: {tools_he}. {state_he}."
            f"{trust_he} יותקן כבוי; צריך לאשר, לתת הרשאות ולהפעיל בלוח הבקרה.",
            f"New Skill: {r['title']} - {r['description']}. {code_en}. Requests: {caps_en}. Tools: {tools_en}. "
            f"{state_en}.{trust_en} Installs DISABLED; approve, grant and enable it on the dashboard.")


# ---------------------------------------------------------------------- storage of drafts / installs
def _write_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


class GeneratedSkills:
    """Drafts, installs, grants and the runner for one data dir (Core and dashboard processes share the files)."""

    def __init__(self, home: Path, *, taken: callable | None = None, http=None, v2: callable | None = None) -> None:
        self.home = Path(home)
        self.root = self.home / ROOT
        self.drafts, self.installed, self.removed = self.root / "drafts", self.root / "installed", self.root / "removed"
        # () -> (built-in skill names, built-in tool names). The Core passes the live registry; the CLI and the dashboard
        # read the snapshot the Core writes on start (#314 item 3), so they refuse a clashing name too.
        self.taken = taken or self._snapshot_taken
        self.http = http                                   # httpx.AsyncClient-like (tests inject a fake)
        self.v2 = v2 or (lambda: False)                    # the skills_manifest2 switch (platform2.py); OFF = as before

    @property
    def _names_file(self) -> Path:
        return self.root / "builtin_names.json"

    def _snapshot_taken(self) -> tuple[set[str], set[str]]:
        d = _read_json(self._names_file, {})
        d = d if isinstance(d, dict) else {}
        return ({x for x in d.get("skills", []) if isinstance(x, str)}, {x for x in d.get("tools", []) if isinstance(x, str)})

    def save_taken_snapshot(self) -> None:
        """Core only: record the built-in Skill and tool names for the other processes (best effort)."""
        try:
            skills, tools = self.taken()
            self.root.mkdir(parents=True, exist_ok=True)
            _write_json(self._names_file, {"skills": sorted(skills), "tools": sorted(tools)})
        except OSError:
            logs.warn("skills", "SKILL_NAMES_SNAPSHOT_FAILED", "could not save the built-in names snapshot")

    # --- manifest v2 (issue #220; only consulted while the switch is on)
    def _apply_v2(self, rep: dict, md: str, py: str | None, upgrading: bool) -> dict:
        """Add trust + manifest facts to a validation report; a tampered / invalid manifest fails it (fail closed)."""
        from . import platform2
        info = platform2.check(md, py, key=platform2.owner_key(self.home), upgrading=upgrading,
                               present=set(self.installed_ids()) | set(self.taken()[0]))
        rep["manifest2"] = info
        if info["errors"]:
            rep["errors"] = list(rep["errors"]) + info["errors"]
            rep["ok"] = False
            logs.warn("skills", "SKILL_V2_REFUSED", "Skill manifest refused", skill=rep.get("id"),
                      trust=info["trust"], errors=len(info["errors"]))
        rep["summary_he"], rep["summary_en"] = _summaries(rep)
        return rep

    def _integrity(self, sid: str, version: int | None = None, files: tuple | None = None) -> str:
        """'' when the stored copy of a version matches the sha256 the owner approved and its signature is not
        tampered; otherwise a short reason (no file content)."""
        from . import platform2
        st = self.state(sid)
        v = int(st.get("version", 0)) if version is None else version
        try:
            md, py = files if files is not None else self._files_of(sid, v)
        except (OSError, UnicodeDecodeError):
            return f"the stored files of version {v} cannot be read"
        want = platform2.approved_sha(st, v)
        if not want or _sha(md, py) != want:
            return f"the stored copy of version {v} does not match the sha256 you approved"
        if platform2.check(md, py, key=platform2.owner_key(self.home))["trust"] == "tampered":
            return f"version {v} has a tampered signature"
        return ""

    def _files_of(self, sid: str, version: int) -> tuple[str, str | None]:
        d = self._version_dir(sid, version)
        py = d / "skill.py"
        return (d / "SKILL.md").read_text(encoding="utf-8"), (py.read_text(encoding="utf-8") if py.is_file() else None)

    # --- helpers
    def _id(self, sid: str) -> str:
        if not isinstance(sid, str) or not ID_RX.match(sid):
            raise SkillError("invalid Skill id")
        return sid

    def _taken(self, exclude: str | None = None) -> tuple[set[str], set[str]]:
        skills, tools = self.taken()
        skills, tools = set(skills), set(tools)
        for sid in self.installed_ids():
            if sid == exclude:
                continue
            skills.add(sid)
            tools |= {t["name"] for t in self.report(sid).get("tools", [])}
        if exclude:
            skills.discard(exclude)
            cur = self.report(exclude) if (self.installed / exclude).is_dir() else {}
            tools -= {t["name"] for t in cur.get("tools", [])}
        return skills, tools

    def installed_ids(self) -> list[str]:
        try:
            return sorted(p.name for p in self.installed.iterdir() if p.is_dir() and ID_RX.match(p.name))
        except OSError:
            return []

    def draft_ids(self) -> list[str]:
        try:
            return sorted(p.name for p in self.drafts.iterdir() if p.is_dir() and ID_RX.match(p.name))
        except OSError:
            return []

    def state(self, sid: str) -> dict:
        return _read_json(self.installed / sid / "state.json", {})

    def _set_state(self, sid: str, st: dict) -> None:
        _write_json(self.installed / sid / "state.json", st)

    def _version_dir(self, sid: str, version: int | None = None) -> Path:
        v = version if version is not None else int(self.state(sid).get("version", 0))
        return self.installed / sid / "versions" / str(v)

    def report(self, sid: str) -> dict:
        return _read_json(self._version_dir(sid) / "report.json", {})

    def files(self, sid: str, draft: bool = False) -> tuple[str, str | None]:
        d = self.drafts / sid if draft else self._version_dir(sid)
        md = (d / "SKILL.md").read_text(encoding="utf-8")
        py = d / "skill.py"
        return md, (py.read_text(encoding="utf-8") if py.is_file() else None)

    # --- propose / validate
    def propose(self, md: str, py: str | None = None, *, by: str = "voice") -> dict:
        """Write a draft and validate it. Never installs, never runs, never touches an installed version."""
        if len(md or "") > MAX_MD * 2 or len(py or "") > MAX_PY * 2:
            return {"ok": False, "errors": ["draft too large"], "warnings": [], "id": None}
        md = _lf(md)                      # #314 item 4: the draft is read back with LF endings, so hash what is stored
        py = _lf(py) if py is not None else None
        try:
            meta, _ = parse_skill_md(md)
            sid = self._id(str(meta.get("name", "")))
        except SkillError as e:
            return {"ok": False, "errors": [str(e)], "warnings": [], "id": None}
        upgrading = (self.installed / sid).is_dir()
        skills, tools = self._taken(exclude=sid if upgrading else None)
        v2 = self.v2()
        rep = validate(md, py, taken_skills=skills, taken_tools=tools, upgrading=upgrading, v2=v2)
        if v2:
            self._apply_v2(rep, md, py, upgrading)
        d = self.drafts / sid
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(md, encoding="utf-8")
        if py is not None and py.strip():
            (d / "skill.py").write_text(py, encoding="utf-8")
        rep["upgrade_of"] = int(self.state(sid).get("version", 0)) if upgrading else None
        _write_json(d / "report.json", rep)
        _write_json(d / "meta.json", {"proposed_by": by, "ts": time.time()})
        logs.info("skills", "SKILL_PROPOSED", "generated Skill draft proposed", skill=sid,
                  by=by, ok=rep["ok"], errors=len(rep["errors"]))
        return rep

    def draft(self, sid: str) -> dict:
        sid = self._id(sid)
        d = self.drafts / sid
        if not d.is_dir():
            raise SkillError("no such draft")
        md, py = self.files(sid, draft=True)
        out = {"id": sid, "report": _read_json(d / "report.json", {}), "meta": _read_json(d / "meta.json", {}),
               "skill_md": md, "skill_py": py}
        if self.v2():
            from . import platform2
            out["update_plan"] = platform2.plan_update(self, sid, quiet=True)     # added permissions, trust, refused or not
        return out

    # --- owner decisions (user routes only)
    @staticmethod
    def _user(by: str) -> None:
        if by not in USER_ROUTES:
            raise PermissionError("only the owner (dashboard / CLI) can approve, grant or enable a Skill")

    def approve(self, sid: str, sha256: str, *, by: str) -> dict:
        self._user(by)
        sid = self._id(sid)
        d = self.drafts / sid
        if not d.is_dir():
            raise SkillError("no such draft")
        md, py = self.files(sid, draft=True)
        upgrading = (self.installed / sid).is_dir()
        skills, tools = self._taken(exclude=sid if upgrading else None)
        v2 = self.v2()
        rep = validate(md, py, taken_skills=skills, taken_tools=tools, upgrading=upgrading, v2=v2)   # re-check now
        if v2:
            self._apply_v2(rep, md, py, upgrading)          # a tampered / invalid manifest is never installed
        if not rep["ok"]:
            raise SkillError("the draft does not pass validation: " + "; ".join(rep["errors"][:5]))
        if not isinstance(sha256, str) or sha256 != rep["sha256"]:
            raise SkillError("the draft changed since you reviewed it; review it again")
        st = self.state(sid) if upgrading else {"history": []}
        vroot = self.installed / sid / "versions"
        kept = [int(p.name) for p in vroot.iterdir() if p.name.isdigit()] if vroot.is_dir() else []
        version = max([int(st.get("version", 0)), *kept]) + 1     # after a rollback, never reuse a kept folder
        vd = vroot / str(version)
        vd.mkdir(parents=True, exist_ok=False)
        # Write the EXACT bytes we just validated and whose sha256 the owner confirmed - never re-read the draft
        # files from disk (a concurrent propose() could swap them between the check and the copy: TOCTOU).
        (vd / "SKILL.md").write_text(md, encoding="utf-8")
        if py is not None:
            (vd / "skill.py").write_text(py, encoding="utf-8")
        _write_json(vd / "report.json", rep)
        hist = list(st.get("history") or [])
        hist.append({"version": version, "sha256": rep["sha256"], "approved_by": by, "ts": time.time()})
        self._set_state(sid, {"version": version, "enabled": False, "grants": {"capabilities": [], "actions": False},
                              "history": hist, "last_error": None})
        shutil.rmtree(d)
        logs.info("skills", "SKILL_INSTALLED", "generated Skill installed DISABLED", skill=sid, version=version, by=by)
        return self.view(sid)

    def reject(self, sid: str, *, by: str) -> None:
        self._user(by)
        d = self.drafts / self._id(sid)
        if d.is_dir():
            shutil.rmtree(d)
            logs.info("skills", "SKILL_REJECTED", "generated Skill draft rejected", skill=sid, by=by)

    def _installed(self, sid: str) -> dict:
        sid = self._id(sid)
        st = self.state(sid)
        if not st:
            raise SkillError("no such installed Skill")
        return st

    def grant(self, sid: str, capabilities: list[str], actions: bool, *, by: str) -> dict:
        self._user(by)
        st = self._installed(sid)
        rep = self.report(sid)
        caps = [c for c in (capabilities or []) if isinstance(c, str)]
        extra = set(caps) - set(rep.get("capabilities", []))
        if extra:
            raise SkillError(f"not requested by the Skill, cannot be granted: {', '.join(sorted(extra))}")
        has_actions = any(t["permission"] == "action" for t in rep.get("tools", []))
        st["grants"] = {"capabilities": sorted(set(caps)), "actions": bool(actions) and has_actions}
        st.setdefault("history", []).append({"grant": st["grants"], "by": by, "ts": time.time()})
        self._set_state(sid, st)
        logs.info("skills", "SKILL_GRANTED", "generated Skill grants updated", skill=sid, caps=st["grants"]["capabilities"],
                  actions=st["grants"]["actions"], by=by)
        return self.view(sid)

    def set_enabled(self, sid: str, on: bool, *, by: str) -> dict:
        if on:
            self._user(by)          # turning OFF is always allowed (any route); ON only by the owner
        st = self._installed(sid)
        st["enabled"] = bool(on)
        st.setdefault("history", []).append({"enabled": bool(on), "by": by, "ts": time.time()})
        self._set_state(sid, st)
        logs.info("skills", "SKILL_ENABLED" if on else "SKILL_DISABLED", "generated Skill switched", on=on,
                  skill=sid, by=by)
        return self.view(sid)

    def rollback(self, sid: str, *, by: str) -> dict:
        self._user(by)
        st = self._installed(sid)
        cur = int(st.get("version", 0))
        away = {int(h["rollback_from"]) for h in st.get("history", []) if isinstance(h, dict) and str(h.get("rollback_from", "")).isdigit()}
        older = sorted(int(p.name) for p in (self.installed / sid / "versions").iterdir()      # a version the owner already
                       if p.name.isdigit() and int(p.name) < cur and int(p.name) not in away)   # rolled away from is never a target (#314)
        if not older:
            raise SkillError("no earlier version to roll back to")
        if self.v2() and (why := self._integrity(sid, older[-1])):
            logs.warn("skills", "SKILL_ROLLBACK_REFUSED", "Skill rollback refused", skill=sid, version=older[-1])
            raise SkillError(f"rollback refused: {why}")
        st.update(version=older[-1], enabled=False, grants={"capabilities": [], "actions": False})
        st.setdefault("history", []).append({"rollback_from": cur, "to": older[-1], "by": by, "ts": time.time()})
        self._set_state(sid, st)
        logs.info("skills", "SKILL_ROLLBACK", "generated Skill rolled back (disabled)", skill=sid, version=older[-1],
                  previous=cur, by=by)
        return self.view(sid)

    def uninstall(self, sid: str, *, by: str) -> None:
        self._user(by)
        self._installed(sid)
        self.removed.mkdir(parents=True, exist_ok=True)
        os.replace(self.installed / sid, self.removed / f"{sid}-{int(time.time())}")
        logs.info("skills", "SKILL_UNINSTALLED", "generated Skill uninstalled (kept in removed/)", skill=sid, by=by)

    # --- views
    def view(self, sid: str) -> dict:
        st = self.state(sid)
        rep = self.report(sid)
        return {"id": sid, "title": rep.get("title", sid), "description": rep.get("description", ""),
                "version": st.get("version"), "enabled": bool(st.get("enabled")), "grants": st.get("grants") or {},
                "has_code": bool(rep.get("has_code")),
                "requested": rep.get("capabilities", []), "tools": rep.get("tools", []),
                "permissions": rep.get("permissions", []), "summary_he": rep.get("summary_he", ""),
                "summary_en": rep.get("summary_en", ""), "last_error": st.get("last_error"),
                "versions": sorted(int(p.name) for p in (self.installed / sid / "versions").iterdir() if p.name.isdigit())
                if (self.installed / sid / "versions").is_dir() else [],
                "history": (st.get("history") or [])[-10:], "targets": TARGETS,
                "manifest2": self._v2_view(sid) if self.v2() else None}

    def _v2_view(self, sid: str) -> dict | None:
        from . import platform2
        try:
            md, py = self.files(sid)
        except (OSError, UnicodeDecodeError):
            return None
        info = platform2.check(md, py, key=platform2.owner_key(self.home), upgrading=True,
                               present=set(self.installed_ids()) | set(self.taken()[0]))
        info["integrity"] = self._integrity(sid, files=(md, py)) or "ok"
        return info

    def overview(self) -> dict:
        drafts = []
        for sid in self.draft_ids():
            d = self.drafts / sid
            drafts.append({"id": sid, "report": _read_json(d / "report.json", {}), "meta": _read_json(d / "meta.json", {})})
        return {"drafts": drafts, "installed": [self.view(s) for s in self.installed_ids()], "targets": TARGETS}

    # --- running
    async def run(self, sid: str, tool: str, args: dict, *, expect_version: int | None = None) -> ToolResult:
        """One tool call. Re-checks enabled / version / grants from disk on every call (defence in depth)."""
        try:
            st = self._installed(sid)
        except SkillError:
            return ToolResult(False, f"Skill {sid} is not installed", code="SKILL_UNKNOWN")
        if not st.get("enabled"):
            return ToolResult(False, f"skill {sid} is disabled", code="SKILL_DISABLED")
        if expect_version is not None and int(st.get("version", 0)) != expect_version:
            return ToolResult(False, f"skill {sid} changed version; try again", code="SKILL_STALE")
        rep = self.report(sid)
        spec = next((t for t in rep.get("tools", []) if t["name"] == tool), None)
        if spec is None:
            return ToolResult(False, f"unknown tool {tool}", code="TOOL_UNKNOWN")
        grants = st.get("grants") or {}
        if spec["permission"] == "action" and not grants.get("actions"):
            return ToolResult(False, f"{tool} needs the owner's 'actions' grant (dashboard)", code="SKILL_NOT_GRANTED")
        ver = int(st.get("version", 0))
        try:
            md, py = self._files_of(sid, ver)               # the version the grants above belong to (#314 item 2)
        except (OSError, UnicodeDecodeError):
            return ToolResult(False, f"skill {sid} changed; try again", code="SKILL_STALE")
        now = self.state(sid)
        if int(now.get("version", 0)) != ver or now.get("grants") != st.get("grants") or not now.get("enabled"):
            return ToolResult(False, f"skill {sid} changed version or grants; try again", code="SKILL_STALE")
        if self.v2() and (why := self._integrity(sid, version=ver, files=(md, py))):
            logs.warn("skills", "SKILL_TAMPERED", "Skill not run: stored copy changed", skill=sid, tool=tool)
            return ToolResult(False, f"skill {sid} not run: {why}", code="SKILL_TAMPERED")
        if tool == f"{sid}_guide":
            return ToolResult(True, parse_skill_md(md)[1][:6000], {"skill": sid})
        broker = _Broker(self, sid, set(grants.get("capabilities") or []))
        res = await run_sandboxed(py or "", tool, args, broker)
        if not res.get("ok"):
            # Re-read the state NOW and change only last_error (#564): the owner may have revoked a grant, disabled the Skill
            # or approved a new version while the sandbox ran, and writing back the old dict would undo that. (The broker
            # keeps the grants of the START of the call for the whole run: inherent, the next call sees the change.)
            cur = self.state(sid)
            if cur and int(cur.get("version", 0)) == ver:
                cur["last_error"] = {"tool": tool, "error": str(res.get("error", ""))[:300], "ts": time.time()}
                self._set_state(sid, cur)
            return ToolResult(False, f"{tool} failed: {str(res.get('error', ''))[:200]}", code="SKILL_RUN_FAILED")
        return ToolResult(True, str(res.get("message", ""))[:1500], res.get("data") or {})


class _Broker:
    """Executes the child's ctx calls - only what the owner granted."""

    def __init__(self, mgr: GeneratedSkills, sid: str, caps: set[str]) -> None:
        self.mgr, self.sid, self.caps = mgr, sid, caps
        self.data_path = mgr.installed / sid / "data.json"

    async def __call__(self, op: str, args: dict) -> Any:
        if op in ("storage_get", "storage_set"):
            if "storage" not in self.caps:
                raise PermissionError("storage not granted")
            data = _read_json(self.data_path, {})
            key = args.get("key")
            if not isinstance(key, str) or not 0 < len(key) <= 64:
                raise ValueError("bad key")
            if op == "storage_get":
                return data.get(key, args.get("default"))
            data[key] = args.get("value")
            blob = json.dumps(data, ensure_ascii=False)
            if len(blob.encode("utf-8")) > MAX_STORAGE:
                raise PermissionError(f"storage quota ({MAX_STORAGE} bytes) exceeded")
            _write_json(self.data_path, data)
            return True
        if op == "http_get":
            from urllib.parse import urlsplit, urlunsplit
            url = str(args.get("url", ""))
            u = urlsplit(url)
            host = (u.hostname or "").lower()
            if u.scheme != "https" or f"network:{host}" not in self.caps or u.username or u.password or \
                    (u.port not in (None, 443)):
                raise PermissionError(f"network access to {host or url[:40]} not granted (https only)")
            client = self.mgr.http
            if client is not None:                       # tests inject a fake client
                r = await client.get(url)
                return {"status": r.status_code, "text": r.text[:MAX_HTTP]}
            # DNS-rebinding defence: resolve ONCE, verify every answer is public, then CONNECT to that exact IP.
            # Resolving again inside httpx would let the name flip to a LAN/loopback address between check and use.
            ip = await _public_host(host)
            import httpx
            port = u.port or 443
            netloc = f"[{ip}]:{port}" if ":" in ip else f"{ip}:{port}"
            pinned = urlunsplit((u.scheme, netloc, u.path or "/", u.query, ""))   # URL points at the checked IP
            async with httpx.AsyncClient(follow_redirects=False, timeout=8.0) as c:
                # Host header + SNI/cert hostname stay the real name; we never follow redirects, so a 3xx to an
                # internal host is returned to the Skill as text, never re-resolved and fetched by us.
                req = c.build_request("GET", pinned, headers={"Host": host}, extensions={"sni_hostname": host})
                r = await c.send(req, stream=True)
                try:
                    buf = b""
                    async for chunk in r.aiter_bytes():
                        buf += chunk
                        if len(buf) >= MAX_HTTP:
                            break
                    return {"status": r.status_code, "text": buf[:MAX_HTTP].decode(r.encoding or "utf-8", "replace")}
                finally:
                    await r.aclose()
        if op == "now":
            from datetime import datetime
            return datetime.now().astimezone().isoformat(timespec="seconds")
        if op == "log":
            logs.info("skills", "SKILL_LOG", "generated Skill log line", skill=self.sid, text=str(args.get("text", ""))[:200])
            return True
        raise PermissionError(f"unknown ctx call {op}")


async def _public_host(host: str) -> str:
    """Resolve `host`, refuse if ANY answer is non-public, and return one verified public IP to connect to."""
    import ipaddress
    import socket
    infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not infos:
        raise PermissionError(f"{host} does not resolve")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise PermissionError(f"{host} resolves to a non-public address; refused")
    return infos[0][4][0]


_CHILD = Path(__file__).with_name("sandbox_child.py")


def _child_python() -> str:
    """The real interpreter. A Windows venv's python.exe is a small launcher that starts the base python.exe as a
    SECOND process, which the one-process job limit would refuse; -I -S never uses the venv's packages anyway."""
    base = getattr(sys, "_base_executable", "") or sys.executable
    return base if os.path.isfile(base) else sys.executable


async def run_sandboxed(code: str, tool: str, args: dict, broker, timeout_s: float = CALL_TIMEOUT_S) -> dict:
    """Run one tool function in a fresh, key-less child process; broker(op, args) serves its ctx calls."""
    env = {"PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
    if sys.platform == "win32":
        env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", r"C:\Windows")   # needed by Python's own startup / random
    work = tempfile.mkdtemp(prefix="jarves-skill-")
    # R-01 (#17): created inside its own OS limits (Windows: suspended -> Job Object -> resumed; one process,
    # 512 MB, 10 s CPU, kill on close, no breakaway, UI restricted. POSIX: own session; the child sets its rlimits)
    try:
        proc, guard = await isolation.exec_async(
            _child_python(), "-I", "-S", "-X", "utf8", str(_CHILD), limits=isolation.SKILL_LIMITS, cwd=work, env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            limit=MAX_HTTP * 3)
    except winsandbox.RestrictedLaunchError as e:       # [helper] restricted_token is on: never run it unrestricted
        shutil.rmtree(work, ignore_errors=True)
        logs.warn("skills", "SKILL_RESTRICTED_LAUNCH_FAILED", "restricted launch failed; Skill not run", error=str(e))
        return {"ok": False, "error": "restricted launch failed; the Skill was not run"}
    if guard.state != "active":
        logs.warn("skills", "SKILL_JOB_UNAVAILABLE", "the Skill child runs without its full OS limits",
                  state=guard.state, reason=guard.reason[:200])

    async def talk() -> dict:
        req = {"code": code, "tool": tool, "args": args, "imports": list(ALLOWED_IMPORTS)}
        proc.stdin.write((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))
        await proc.stdin.drain()
        for _ in range(MAX_RPC + 1):
            line = await proc.stdout.readline()
            if not line:
                return {"ok": False, "error": "the Skill process ended without a result"}
            msg = json.loads(line)
            if msg.get("done"):
                return msg
            try:
                rep = {"id": msg.get("id"), "ok": True, "result": await broker(str(msg.get("rpc")), msg.get("args") or {})}
            except Exception as e:      # denied / failed ctx call -> PermissionError inside the Skill
                rep = {"id": msg.get("id"), "ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
            proc.stdin.write((json.dumps(rep, ensure_ascii=False, default=str) + "\n").encode("utf-8"))
            await proc.stdin.drain()
        return {"ok": False, "error": f"more than {MAX_RPC} ctx calls"}

    try:
        return await asyncio.wait_for(talk(), timeout_s)
    except TimeoutError:
        return {"ok": False, "error": f"timed out after {timeout_s:.0f} s"}
    except (ValueError, OSError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as e:
        return {"ok": False, "error": f"bad output from the Skill process ({type(e).__name__})"}
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        guard.close()                                    # kill-on-close / process group: nothing stays behind
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------------- registry integration
def _args_model(sid: str, spec: dict) -> type[BaseModel]:
    fields = {k: (ARG_TYPES[v], Field(...)) for k, v in (spec.get("args") or {}).items() if v in ARG_TYPES}
    return create_model(f"Args_{spec['name']}", **fields)


class InstalledSkill(Skill):
    """An approved generated Skill as a registry Skill (one version)."""
    origin = "generated"

    def __init__(self, mgr: GeneratedSkills, sid: str) -> None:
        self.mgr, self.name = mgr, sid
        self.version = int(mgr.state(sid).get("version", 0))
        rep = mgr.report(sid)
        self.description = f"[generated] {rep.get('description', '')}"[:300]
        self._specs = list(rep.get("tools", []))
        self._net = any(c.startswith("network:") for c in rep.get("capabilities", []))

    def action_tools(self) -> set[str]:
        return {t["name"] for t in self._specs if t["permission"] == "action"}

    def tools(self) -> list[Tool]:
        out = []
        for spec in self._specs:
            async def handler(a: BaseModel, _name=spec["name"]) -> ToolResult:
                return await self.mgr.run(self.name, _name, a.model_dump(), expect_version=self.version)
            out.append(Tool(spec["name"], f"{spec['description']} (generated Skill {self.name})", handler,
                            _args_model(self.name, spec), permission=spec["permission"], timeout_s=CALL_TIMEOUT_S + 5,
                            untrusted_output=self._net and not spec["name"].endswith("_guide")))
        return out

    async def health(self) -> tuple[str, str]:
        err = self.mgr.state(self.name).get("last_error")
        return ("degraded", str(err.get("error", ""))) if err else ("ok", "")


_REFUSED_LOGGED: set[str] = set()       # a refused load is logged once, not every 2 s


def sync(mgr: GeneratedSkills, registry) -> list[str]:
    """Make the registry match the installed Skills on disk: register / re-register (new version) / unregister,
    disabled set and action pre-authorisation per the owner's switches and grants. Returns what changed."""
    changed = []
    ids = set(mgr.installed_ids())
    if mgr.v2():               # manifest v2 on: a changed / tampered stored copy is never loaded (fail closed)
        bad = {sid for sid in ids if mgr._integrity(sid)}
        for sid in sorted(bad - _REFUSED_LOGGED):
            logs.warn("skills", "SKILL_LOAD_REFUSED", "generated Skill not loaded: stored copy changed", skill=sid)
        _REFUSED_LOGGED.clear()
        _REFUSED_LOGGED.update(bad)
        ids -= bad
    for name, skill in list(registry.skills.items()):
        if isinstance(skill, InstalledSkill) and (name not in ids or skill.version != int(mgr.state(name).get("version", 0))):
            registry.allowed_actions -= skill.action_tools()
            registry.disabled.discard(name)
            registry.unregister(name)
            changed.append(f"-{name}")
    for sid in sorted(ids):
        st = mgr.state(sid)
        skill = registry.skills.get(sid)
        if skill is None:
            try:
                skill = InstalledSkill(mgr, sid)
                registry.register(skill)
                changed.append(f"+{sid}")
            except Exception:
                logs.error("skills", "SKILL_LOAD_FAILED", "generated Skill could not be registered", skill=sid, exc=True)
                continue
        if not isinstance(skill, InstalledSkill):
            continue                              # a built-in with the same name always wins
        on = bool(st.get("enabled"))
        if on:
            registry.disabled.discard(sid)
        else:
            registry.disabled.add(sid)
        acts = skill.action_tools()
        if on and (st.get("grants") or {}).get("actions"):
            registry.allowed_actions |= acts
        else:
            registry.allowed_actions -= acts
    return changed


# ---------------------------------------------------------------------- model tools (propose only)
class ProposeArgs(BaseModel):
    skill_md: str = Field(..., description="Full SKILL.md: '---' frontmatter (name lower_case id, title, description, capabilities [storage, network:<host>]) '---' then Markdown instructions")
    skill_py: str | None = Field(None, description="Optional Python: TOOLS = [{name:'<id>_<word>', description, permission: read|action, args:{x: string|integer|number|boolean}}] + def <tool>(args, ctx); stdlib only; ctx.storage_get/set, ctx.http_get(url)")


def make_builder_skill(mgr: GeneratedSkills) -> Skill:
    class BuilderSkill(Skill):
        name = "skill_builder"
        description = "Propose new Skills for the owner to review (never installs or enables anything)"

        def tools(self) -> list[Tool]:
            async def propose(a: ProposeArgs) -> ToolResult:
                rep = mgr.propose(a.skill_md, a.skill_py, by="voice")
                if not rep.get("ok"):
                    return ToolResult(False, "הטיוטה לא עברה בדיקה: " + "; ".join(rep.get("errors", [])[:4]),
                                      {"errors": rep.get("errors", [])[:10]}, "SKILL_DRAFT_INVALID")
                return ToolResult(True, "הכנתי טיוטה של כישור חדש. היא לא פעילה: צריך לאשר אותה בלוח הבקרה "
                                        "(עמוד Skills), לתת הרשאות ולהפעיל. " + rep["summary_he"],
                                  {"id": rep["id"], "summary": rep["summary_en"], "warnings": rep["warnings"]})

            async def list_(_) -> ToolResult:
                ov = mgr.overview()
                inst = "; ".join(f"{s['id']} v{s['version']} ({'on' if s['enabled'] else 'off'})" for s in ov["installed"])
                drafts = ", ".join(d["id"] for d in ov["drafts"])
                return ToolResult(True, f"installed: {inst or 'none'}. drafts waiting for the owner: {drafts or 'none'}.",
                                  {"installed": [{k: s[k] for k in ("id", "version", "enabled", "grants")}
                                                 for s in ov["installed"]], "drafts": [d["id"] for d in ov["drafts"]]})

            return [
                Tool("skill_propose", "Draft a NEW Skill (SKILL.md + optional Python) on request: 'learn a new skill', 'תלמד יכולת חדשה'. Draft only; the owner approves and enables it. Never claim it works before.", propose, ProposeArgs, permission="action",
                     timeout_s=15),
                Tool("skills_generated_list", "Generated Skills (on/off) and drafts awaiting approval: 'my new skills', 'אילו יכולות יצרת'.", list_, permission="read"),
            ]

    return BuilderSkill()


_PI = "https://github.com/Jarvis-igerman-ai/jarves/issues/3"
TARGETS = {"windows": {"status": "implemented_unverified",
                       "evidence": "pytest test_skill_creation (incl. malicious drafts incl. the string.Formatter "
                                   "getattr escape); child runs in a Job Object (memory + 1-process cap, kill-on-"
                                   "close); code is NOT OS-sandboxed (shown on the approval page); UX not owner-reviewed"},
           "linux_arm64_pi": {"status": "planned", "roadmap_issue": _PI,
                              "blocker": "sandbox runner not run on Pi OS; OS-level sandbox (bubblewrap/seccomp) open"}}
features.register("skill_creation", "יצירת כישורים חדשים", "Create new Skills", True, risk="medium",
                  tools=["skill_propose"], targets=TARGETS,
                  about="JARVES may draft a new Skill; you review, approve, grant and enable it here (installs off)")


def attach(core) -> GeneratedSkills:
    """Core integration (app.py one line): builder tools, installed Skills, a 2 s sync for dashboard decisions."""
    def taken() -> tuple[set[str], set[str]]:
        reg = core.registry
        skills = {n for n, s in reg.skills.items() if not isinstance(s, InstalledSkill)}
        tools = {t for t, (s, _) in reg._tools.items() if not isinstance(s, InstalledSkill)}
        return skills, tools

    mgr = GeneratedSkills(core.home, taken=taken, v2=lambda: features.active("skills_manifest2"))
    mgr.save_taken_snapshot()
    core.registry.allowed_actions |= {"skill_propose"}     # writes only an inert draft
    core.registry.register(make_builder_skill(mgr))
    sync(mgr, core.registry)

    async def watch() -> None:
        while True:
            await asyncio.sleep(2.0)
            try:
                if sync(mgr, core.registry):
                    _refresh_tools_md(core)
            except Exception:
                logs.error("skills", "SKILL_SYNC_FAILED", "generated Skills sync failed", exc=True)
    core._bg.append(asyncio.create_task(watch()))
    _refresh_tools_md(core)
    return mgr


def _refresh_tools_md(core) -> None:
    try:                                   # TOOLS.md was generated before these Skills existed
        core._write_tools_md()             # the Core's own writer (Skills + the Features section)
    except Exception:
        logs.warn("skills", "TOOLS_MD_FAILED", "could not refresh TOOLS.md with the generated Skills")
    if getattr(getattr(core, "agent", None), "_agent", None) is not None:
        core.agent._agent = None           # the text agent rebuilds its tool list on the next request


# ---------------------------------------------------------------------- dashboard (the owner)
def _mgr(ctx) -> GeneratedSkills:
    def v2() -> bool:
        try:
            return features._ctx_features(ctx).active("skills_manifest2")
        except Exception:                  # no config in this context: the switch counts as off
            return False
    return GeneratedSkills(ctx.home, v2=v2)


def api_list(ctx, m, body):
    return 200, _mgr(ctx).overview()


def api_draft(ctx, m, body):
    try:
        return 200, _mgr(ctx).draft(m.group(1))
    except SkillError as e:
        return 404, {"error": str(e)}


def api_action(ctx, m, body):
    mgr, sid, act, body = _mgr(ctx), m.group(1), m.group(2), body or {}
    try:
        if act == "approve":
            return 200, mgr.approve(sid, body.get("sha256", ""), by="dashboard")
        if act == "reject":
            mgr.reject(sid, by="dashboard")
            return 200, {"ok": True}
        if act == "grant":
            caps = body.get("capabilities") or []
            if not isinstance(caps, list):
                return 400, {"error": "capabilities must be a list"}
            return 200, mgr.grant(sid, caps, bool(body.get("actions")), by="dashboard")
        if act in ("enable", "disable"):
            return 200, mgr.set_enabled(sid, act == "enable", by="dashboard")
        if act == "rollback":
            return 200, mgr.rollback(sid, by="dashboard")
        if act == "uninstall":
            from .. import approvals
            ok, _w = approvals.ask_owner(ctx.home, f"Remove the skill '{sid}'? (kept in skills/removed)",
                                         title="dashboard_remove_skill", risk="medium",
                                         why="You pressed Uninstall on the dashboard Skills page.", details={"skill": sid})
            if not ok:
                return 403, dict(approvals.APPROVAL_REFUSED)
            mgr.uninstall(sid, by="dashboard")
            return 200, {"ok": True}
        if act == "test":
            tool, args = body.get("tool"), body.get("args") or {}
            if not isinstance(tool, str) or not isinstance(args, dict):
                return 400, {"error": "body needs {tool, args}"}
            res = asyncio.run(mgr.run(sid, tool, args))
            return 200, {"ok": res.ok, "message": res.message, "code": res.code, "data": res.data}
    except (SkillError, PermissionError) as e:
        return 400, {"error": str(e)}
    return 404, {"error": "unknown action"}


def dashboard_routes(register_route) -> None:
    register_route("GET", r"/api/skills/generated", api_list)
    register_route("GET", r"/api/skills/generated/([a-z0-9_]+)", api_draft)
    register_route("POST", r"/api/skills/generated/([a-z0-9_]+)/(approve|reject|grant|enable|disable|rollback|"
                           r"uninstall|test)", api_action)
