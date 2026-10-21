"""Private file workspace and folder grants (SW-V2 WP-M3, spec 9, decision D07).

Generated files live ONLY in the per-install sandbox <state_dir>/files (user data, never next to the application
binaries, so installs/updates do not touch it). Folders outside it are reachable only through an explicit,
revocable GRANT that the user approved on the dashboard (a model can only REQUEST one: it stays pending).

Every path from any route (voice/agent tools, scheduled jobs, notes export, dashboard API) goes through
`Sandbox.resolve`, which refuses (SANDBOX_DENIED): absolute paths, drive letters, UNC, alternate data streams
(':'), '..', NUL/control characters, Windows reserved device names, names ending in '.'/' ', and any symlink or
junction on the way; the resolved path must stay inside its root. Writes also refuse: file types outside a small
document allowlist (no executables or scripts: nothing here is ever launched), hard-linked targets, overwriting
without overwrite=true or a read-only / read-grant file (SANDBOX_EXISTS), files over files.max_file_mb and a sandbox
over files.quota_mb (SANDBOX_QUOTA). Writes are atomic (temp file + replace in the same folder).

Paths: "reports/today.md" = inside the sandbox; "grant:<id>/sub/file.txt" = inside approved grant <id>.
"""
from __future__ import annotations

import os
import re
import stat
import sys
import time
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, Field

from . import logs
from .userdata import UserDB, ctx_db

WRITE_TYPES = {".md", ".txt", ".csv", ".tsv", ".json", ".html", ".htm", ".xml", ".yaml", ".yml", ".ics", ".log"}
READ_MAX_BYTES = 1_000_000
MAX_PATH_CHARS = 240
_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³]|conin\$|conout\$)(\..*)?$", re.I)
_BAD_CHARS = re.compile(r"[<>:\"|?*\x00-\x1f]")
GRANT_RX = re.compile(r"^grant:(\d+)(?:/(.*))?$")


class SandboxError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def _deny(why: str) -> SandboxError:
    return SandboxError("SANDBOX_DENIED", why)


def clean_relpath(raw: str) -> PurePosixPath:
    """Validate a user/model-supplied relative path (no filesystem access)."""
    if not isinstance(raw, str):
        raise _deny("path must be text")
    p = raw.replace("\\", "/")
    if p != p.strip():
        raise _deny("path starts or ends with a space")
    if not p or p in (".", "/"):
        return PurePosixPath(".")
    if len(p) > MAX_PATH_CHARS:
        raise _deny("path too long")
    if p.startswith(("/", "~")) or re.match(r"^[a-zA-Z]:", p) or p.startswith("//"):
        raise _deny("absolute paths are not allowed; use a path inside the workspace")
    parts = [x for x in p.split("/") if x not in ("", ".")]
    for part in parts:
        if part == "..":
            raise _deny("'..' is not allowed")
        if _BAD_CHARS.search(part):
            raise _deny(f"bad character in {part!r}")
        if part.endswith((".", " ")) or part != part.strip():
            raise _deny(f"name {part!r} ends with a dot or space")
        if _RESERVED.match(part):
            raise _deny(f"{part!r} is a reserved device name")
    return PurePosixPath(*parts) if parts else PurePosixPath(".")


def _is_link(p: Path) -> bool:
    try:
        if p.is_symlink():
            return True
        is_junction = getattr(p, "is_junction", None)          # Python 3.12+
        if is_junction is not None and is_junction():
            return True
        if sys.platform == "win32" and p.exists():
            attrs = getattr(os.lstat(p), "st_file_attributes", 0)
            return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    except OSError:
        return True
    return False


def _inside(child: Path, root: Path) -> bool:
    return child == root or root in child.parents


def _forbidden_grant_roots(home: Path) -> list[Path]:
    """Folders a grant may never cover (system, programs, JARVES's own state with credentials)."""
    from .profiles import data_roots
    out = list(data_roots(home))           # a named profile: the base folder (default profile + all profiles) too
    env = os.environ
    for k in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData", "LOCALAPPDATA", "APPDATA"):
        if env.get(k):
            out.append(Path(env[k]))
    for p in ("/etc", "/bin", "/sbin", "/usr", "/boot", "/dev", "/proc", "/sys", "/var", "/root", "/lib"):
        out.append(Path(p))
    out.append(Path.home() / ".ssh")
    return [p.resolve() if p.exists() else p for p in out]


class Sandbox:
    def __init__(self, root: Path, db: UserDB, *, home: Path | None = None) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.root = self.root.resolve()
        self.db = db
        self.home = (home or self.root.parent).resolve()

    # ------------------------------------------------------------------ grants
    def grants(self, status: str | None = None) -> list[dict]:
        if status:
            return self.db.rows("SELECT * FROM grants WHERE status=? ORDER BY id DESC", (status,))
        return self.db.rows("SELECT * FROM grants ORDER BY id DESC")

    def _check_grant_path(self, path: str) -> Path:
        p = Path(str(path)).expanduser()
        if not p.is_absolute():
            raise SandboxError("SANDBOX_BAD_PATH", "a folder grant needs a full folder path")
        if not p.is_dir():                           # a typo, not a refusal: 400 with the message (SW-V4 K, D-15)
            raise SandboxError("SANDBOX_BAD_PATH", "that folder does not exist")
        if _is_link(p):
            raise _deny("the folder is a link/junction")
        r = p.resolve()
        if r.parent == r or len(r.parts) <= 1:
            raise _deny("a whole drive cannot be granted")
        if r == Path.home().resolve():
            raise _deny("the whole user folder cannot be granted; pick a sub-folder")
        for bad in _forbidden_grant_roots(self.home):
            if _inside(r, bad) or (bad != Path("/") and _inside(bad, r) and bad.exists()):
                raise _deny("system, program or JARVES data folders cannot be granted")
        if _inside(r, self.root) or _inside(self.root, r):
            raise _deny("the workspace itself needs no grant")
        return r

    def request_grant(self, path: str, mode: str = "read", *, by: str = "voice", reason: str = "") -> dict:
        """A model/voice request: recorded as PENDING, the user approves it on the dashboard."""
        if mode not in ("read", "readwrite"):
            raise _deny("mode is read or readwrite")
        r = self._check_grant_path(path)
        cur = self.db.x("INSERT INTO grants (path, mode, status, reason, requested_by, created) VALUES (?,?,?,?,?,?)",
                        (str(r), mode, "pending", reason[:200], by, time.time()))
        self.db.audit(by, "grant_request", cur.lastrowid, {"path": str(r), "mode": mode})
        return self.db.row("SELECT * FROM grants WHERE id=?", (cur.lastrowid,))

    def add_grant(self, path: str, mode: str = "read", *, by: str = "dashboard") -> dict:
        """The user's own grant from the dashboard (the request IS the approval)."""
        g = self.request_grant(path, mode, by=by, reason="added by the user")
        return self.decide(g["id"], "approve", by=by)

    def decide(self, grant_id: int, op: str, *, by: str) -> dict:
        g = self.db.row("SELECT * FROM grants WHERE id=?", (int(grant_id),))
        if g is None:
            raise SandboxError("GRANT_NOT_FOUND", "no such grant")
        new = {"approve": "active", "deny": "denied", "revoke": "revoked"}.get(op)
        if new is None:
            raise SandboxError("BAD_OP", "op is approve, deny or revoke")
        if op == "approve":
            if g["status"] != "pending":
                raise SandboxError("GRANT_STATE", f"grant is {g['status']}")
            self._check_grant_path(g["path"])          # re-checked at approval (the folder may have changed)
        self.db.x("UPDATE grants SET status=?, decided_at=?, decided_by=? WHERE id=?", (new, time.time(), by, g["id"]))
        self.db.audit(by, f"grant_{op}", g["id"], {"path": g["path"], "mode": g["mode"]})
        logs.info("files", "GRANT_" + op.upper(), "folder grant decided", op=op, grant=g["id"], by=by)
        return self.db.row("SELECT * FROM grants WHERE id=?", (g["id"],))

    # ------------------------------------------------------------------ paths
    def resolve(self, raw: str, *, write: bool = False) -> tuple[Path, Path, dict | None]:
        """(absolute path, its root, grant row or None). Refuses anything outside the root."""
        raw = raw if isinstance(raw, str) else ""
        g = GRANT_RX.match(raw.replace("\\", "/").strip())
        grant = None
        if g:
            grant = self.db.row("SELECT * FROM grants WHERE id=? AND status='active'", (int(g.group(1)),))
            if grant is None:
                raise _deny("no active folder grant with that id")
            if write and grant["mode"] != "readwrite":
                raise _deny("that folder grant is read-only")
            root = Path(grant["path"])
            if _is_link(root) or not root.is_dir():
                raise _deny("the granted folder is missing or became a link")
            root = root.resolve()
            rel = clean_relpath(g.group(2) or "")
        else:
            root, rel = self.root, clean_relpath(raw)
        target = root
        for part in rel.parts:
            target = target / part
            if target.exists() or target.is_symlink():
                if _is_link(target):
                    raise _deny("links/junctions are not followed")
        final = target.resolve()
        if not _inside(final, root):
            raise _deny("path leaves the workspace")
        return final, root, grant

    def display(self, p: Path, root: Path, grant: dict | None) -> str:
        rel = p.relative_to(root).as_posix() if p != root else ""
        return f"grant:{grant['id']}/{rel}".rstrip("/") if grant else (rel or ".")

    # ------------------------------------------------------------------ files
    def usage_bytes(self) -> int:
        total = 0
        for dirpath, dirnames, files in os.walk(self.root, followlinks=False):
            for f in files:
                try:
                    total += os.lstat(os.path.join(dirpath, f)).st_size
                except OSError:
                    pass
        return total

    def write_text(self, raw: str, text: str, *, overwrite: bool = False, by: str = "agent") -> dict:
        p, root, grant = self.resolve(raw, write=True)
        if p == root:
            raise _deny("needs a file name")
        if p.suffix.lower() not in WRITE_TYPES:
            raise SandboxError("SANDBOX_TYPE", f"only document files can be written ({', '.join(sorted(WRITE_TYPES))})")
        data = str(text).encode("utf-8")
        max_file = int(float(self.db.setting("files.max_file_mb")) * 1024 * 1024)
        if len(data) > max_file:
            raise SandboxError("SANDBOX_QUOTA", f"file larger than {max_file // (1024 * 1024)} MB")
        old, existed = 0, p.exists()
        if existed:
            if not p.is_file():
                raise _deny("a folder has that name")
            st = os.lstat(p)
            if not overwrite:
                raise SandboxError("SANDBOX_EXISTS", "file exists (overwrite=true to replace it)")
            if st.st_nlink > 1:
                raise _deny("hard-linked files are not replaced")
            if not (st.st_mode & stat.S_IWRITE) or getattr(st, "st_file_attributes", 0) & 1:   # read-only
                raise SandboxError("SANDBOX_EXISTS", "the file is protected (read-only)")
            old = st.st_size
        if grant is None:
            quota = int(float(self.db.setting("files.quota_mb")) * 1024 * 1024)
            if self.usage_bytes() - old + len(data) > quota:
                raise SandboxError("SANDBOX_QUOTA", f"workspace quota of {quota // (1024 * 1024)} MB reached")
        p.parent.mkdir(parents=True, exist_ok=True)
        if not _inside(p.parent.resolve(), root):              # created folders re-checked (race with a link)
            raise _deny("path leaves the workspace")
        tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, p)
        logs.info("files", "FILE_WRITTEN", "file written", user_path=self.display(p, root, grant), bytes=len(data), by=by)
        return {"path": self.display(p, root, grant), "bytes": len(data), "replaced": existed}

    def read_text(self, raw: str) -> dict:
        p, root, grant = self.resolve(raw)
        if not p.is_file():
            raise SandboxError("SANDBOX_NOT_FOUND", "no such file")
        if os.lstat(p).st_size > READ_MAX_BYTES:
            raise SandboxError("SANDBOX_QUOTA", "file too large to read here")
        return {"path": self.display(p, root, grant), "text": p.read_bytes().decode("utf-8", errors="replace"),
                "bytes": os.lstat(p).st_size, "granted": grant is not None}

    def list(self, raw: str = "") -> dict:
        p, root, grant = self.resolve(raw)
        if not p.is_dir():
            raise SandboxError("SANDBOX_NOT_FOUND", "no such folder")
        items = []
        for c in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower()))[:500]:
            if c.name.startswith(".") and c.name.endswith(".tmp"):
                continue
            link = _is_link(c)
            st = os.lstat(c)
            items.append({"name": c.name, "dir": c.is_dir() and not link, "link": link, "bytes": st.st_size,
                          "modified": st.st_mtime, "path": None if link else self.display(c, root, grant)})
        return {"path": self.display(p, root, grant), "items": items, "granted": grant is not None}


# ---------------------------------------------------------------------- voice / agent tools
class WriteArgs(BaseModel):
    path: str = Field(..., min_length=1, max_length=MAX_PATH_CHARS,
                      description="file inside my workspace, e.g. 'reports/summary.md' (or grant:<id>/file.txt)")
    text: str = Field(..., max_length=200_000)
    overwrite: bool = Field(False, description="replace an existing file")


class PathArgs(BaseModel):
    path: str = Field("", max_length=MAX_PATH_CHARS, description="file or folder in my workspace ('' = top)")


class GrantArgs(BaseModel):
    path: str = Field(..., min_length=3, max_length=400, description="full path of the folder the user wants shared")
    mode: str = Field("read", pattern="^(read|readwrite)$")
    reason: str = Field("", max_length=200)


def make_skill(sb: Sandbox):
    from .skills import Skill, Tool, ToolResult

    def fail(e: SandboxError) -> ToolResult:
        return ToolResult(False, e.message, code=e.code)

    class FileSkill(Skill):
        name = "files"
        description = "my private file workspace (documents I generate) and approved folders"

        def tools(self) -> list:
            async def write(a: WriteArgs):
                try:
                    r = sb.write_text(a.path, a.text, overwrite=a.overwrite, by="agent")
                except SandboxError as e:
                    return fail(e)
                return ToolResult(True, f"saved {r['path']}", r)

            async def read(a: PathArgs):
                try:
                    p, _, _ = sb.resolve(a.path)
                    if p.is_dir():
                        r = sb.list(a.path)
                        return ToolResult(True, f"{len(r['items'])} item(s) in {r['path']}", r)
                    r = sb.read_text(a.path)
                except SandboxError as e:
                    return fail(e)
                return ToolResult(True, f"{r['path']} ({r['bytes']} bytes)", {**r, "text": r["text"][:6000]})

            async def grant(a: GrantArgs):
                try:
                    g = sb.request_grant(a.path, a.mode, by="voice", reason=a.reason)
                except SandboxError as e:
                    return fail(e)
                return ToolResult(True, "requested; the user must approve it on the dashboard Files page before I "
                                        "can use that folder", {"grant_id": g["id"], "status": g["status"]})

            return [
                Tool("file_write", "Save a document I generated (Markdown, text, CSV, JSON, HTML...) in my private "
                     "workspace. Never executables.", write, WriteArgs, permission="action"),
                Tool("file_read", "Read a file, or list a folder, in my workspace or an approved folder.", read,
                     PathArgs, permission="read", untrusted_output=True),
                Tool("folder_grant_request", "Ask for access to one of the user's folders (stays pending until the "
                     "user approves it on the dashboard).", grant, GrantArgs, permission="action"),
            ]

    return FileSkill()


ALLOWED_ACTIONS = {"file_write", "folder_grant_request"}


# ---------------------------------------------------------------------- dashboard
def ctx_sandbox(ctx) -> Sandbox:
    s = getattr(ctx, "_sandbox", None)
    if s is None:
        s = Sandbox(ctx.home / "files", ctx_db(ctx), home=ctx.home)
        ctx._sandbox = s
    return s


def _err(e: SandboxError):
    return (404 if e.code in ("SANDBOX_NOT_FOUND", "GRANT_NOT_FOUND") else 403 if e.code == "SANDBOX_DENIED"
            else 400), {"error": e.message, "code": e.code}


def api_files(ctx, m, body, q=None):
    sb = ctx_sandbox(ctx)
    try:
        listing = sb.list(((q or {}).get("path") or [""])[0])
    except SandboxError as e:
        return _err(e)
    return 200, {**listing, "root": str(sb.root), "usage_bytes": sb.usage_bytes(),
                 "quota_mb": sb.db.setting("files.quota_mb"), "grants": sb.grants()}


def api_file_read(ctx, m, body, q=None):
    try:
        return 200, ctx_sandbox(ctx).read_text(((q or {}).get("path") or [""])[0])
    except SandboxError as e:
        return _err(e)


def api_grant_add(ctx, m, body):
    b = body or {}
    try:
        return 200, ctx_sandbox(ctx).add_grant(str(b.get("path", "")), str(b.get("mode", "read")), by="dashboard")
    except SandboxError as e:
        return _err(e)


def api_grant_op(ctx, m, body):
    try:
        return 200, ctx_sandbox(ctx).decide(int(m.group(1)), str((body or {}).get("op", "")), by="dashboard")
    except SandboxError as e:
        return _err(e)


def dashboard_routes(register) -> None:
    register("GET", r"/api/files", api_files)
    register("GET", r"/api/files/read", api_file_read)
    register("POST", r"/api/files/grants", api_grant_add)
    register("POST", r"/api/files/grants/(\d+)", api_grant_op)
