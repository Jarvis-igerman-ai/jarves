"""Hot-patch (issue #348): put a code-only fix into the INSTALLED build in about a minute instead of a 15 minute installer.

`jarvis hotfix apply [--from <git ref | folder>]`
  1. compares the source package (core/src/jarves) with the installed one, file by file (sha256);
  2. classifies the change (issue #459, owner rule "a hotfix is any hotfix"). Applied: Python source and plain data
     inside the jarves package. SKIPPED and listed as "only affects the next full installer" (output, manifest,
     `hotfix status`): build-time files that are not part of the installed tree (packaging/, CI, docs, tools) and
     package files this tool cannot place (binaries, installer code). SLICE 2 (issue #460, module hotfix_slice2): VERIFIED
     categories plugged into `classify`: a bridge pin/code change delivers the exe given with `--bridge-exe` only if its
     sha256 equals the pin in the target commit (bridge stopped via the admin shutdown path, replaced atomically with a
     backup, restarted only if it ran); a pyproject/uv.lock change installs the base->target lock delta as hash-pinned
     binary wheels (`pip install --require-hashes --no-deps --no-index` from a generated requirements file, backup of
     every touched site-packages file, revert restores them and removes new packages). Any hash/size mismatch, missing
     hash, sdist, non-registry source: HOTFIX_DEP_UNVERIFIED / HOTFIX_BRIDGE_UNVERIFIED, nothing changed. Still REFUSED
     (HOTFIX_NEEDS_SLICE2): other dependency files (requirements*.txt, setup.py...). An updated Python file importing a
     third-party module is allowed only when the locked wheels deliver it (HOTFIX_NEW_DEPENDENCY otherwise).
     Always refused (fail closed): unsafe path, symlink, case clash, a base
     commit that is not an ancestor of the target. When the source is a git checkout the whole repository change
     since the installed commit is checked, not just the package;
  3. `jarvis backup` first, then copies ONLY the changed files (temp file + atomic replace). The originals go to
     <home>/hotfix/<id>/originals/ and manifest.json records the sha256 of every original and of every new file;
  4. marks the build: `_build_info.json` gets a "hotfix" block (n, base version, commit, files) and `jarvis version`
     shows "<version>+hotfix.N". The numeric version is NOT changed, so any newer release replaces the hotfix like a
     normal update and a hotfix can never make an update look like a downgrade;
  5. restarts the Core ONLY if it ran before (recorded first), silently (no window, no sound), then runs `jarvis doctor`
     in a fresh process;
  6. fail closed: any error (copy, restart, doctor worse than before) restores the originals automatically.
  7. `--python-only` is kept as an alias (the default behaviour; the automatic hotfix passes it after a dry-run).
`jarvis hotfix revert` restores the originals from the manifest (refused when a normal update already replaced the
hotfix, so it can never downgrade). `jarvis hotfix status` shows the current mark.

Nothing here runs unless the user types the command. Tests use a fake install tree and fake hooks, never the real one.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import clilang
from . import features as feat
from . import hebrew

FEATURE = "hotfix"
BUILD_INFO_NAME = "_build_info.json"
HOTFIX_DIR = "hotfix"
# Plain text/data files that may be hot-patched. Anything else (binaries, archives, unknown types) needs the installer.
DATA_SUFFIXES = {".py", ".toml", ".json", ".js", ".css", ".html", ".md", ".txt", ".svg", ".png", ".ico", ".csv"}
BINARY_SUFFIXES = {".exe", ".dll", ".pyd", ".so", ".dylib", ".whl", ".zip", ".pak", ".bin"}
# Files that only exist in an installed build (written by the build): never compared, never replaced here.
INSTALLED_ONLY = {BUILD_INFO_NAME, "_google_app_client.json"}
SKIP_DIRS = {"__pycache__", ".pytest_cache"}
# Repository paths whose change always needs the full installer.
FULL_INSTALLER_PREFIXES = (".github/", "packaging/", "bridge/", "godot/", "core/pyproject.toml", "core/uv.lock", "uv.lock",
                           "pyproject.toml", "core/run_core.cmd", "jarvis.cmd")
PACKAGE_PREFIX = "core/src/jarves/"
INSTALLER_CODE_NAMES = ("installer", "uninstall", "rollback_windows")


class HotfixError(Exception):
    def __init__(self, code: str, message: str, action: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.action = code, message, action


class Refused(HotfixError):
    """The change needs the full installer (or the request is unsafe). Nothing was touched."""


# ---------------------------------------------------------------------- small helpers
def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _now_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.gmtime())


def _read_json(p: Path) -> dict:
    try:
        v = json.loads(p.read_text(encoding="utf-8"))
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(p: Path, data: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


def _atomic_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".hotfix-tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


def _rel(p: Path, root: Path) -> str:
    return p.relative_to(root).as_posix()


def tree_files(root: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for p in sorted(root.rglob("*")):
        if p.is_symlink():                       # never follow a link out of the source tree
            continue
        if p.is_file() and not (set(p.relative_to(root).parts[:-1]) & SKIP_DIRS) and not p.name.endswith((".pyc", ".hotfix-tmp")):
            out[_rel(p, root)] = p
    return out


# ---------------------------------------------------------------------- the plan and the refuse rules
@dataclass
class Plan:
    replace: list[str] = field(default_factory=list)      # exist in both, content differs
    add: list[str] = field(default_factory=list)          # only in the source
    removed_in_source: list[str] = field(default_factory=list)   # only installed: left alone (reported)
    bridge: object = None                                         # slice 2: hotfix_slice2.BridgeAction (verified) or None
    deps: object = None                                           # slice 2: hotfix_slice2.DepPlan (verified) or None

    @property
    def files(self) -> list[str]:
        return sorted(self.replace + self.add)


def build_plan(src_pkg: Path, installed_pkg: Path) -> Plan:
    """What would change if the source package were put over the installed one. Test files and the build's own
    files are ignored."""
    src, inst = tree_files(src_pkg), tree_files(installed_pkg)
    plan = Plan()
    inst_lower = {k.lower() for k in inst}
    for rel, p in src.items():
        if rel in INSTALLED_ONLY or rel.startswith("tests/"):
            continue
        if rel.startswith(("/", "\\")) or ".." in Path(rel).parts or ":" in rel:
            raise Refused("HOTFIX_BAD_PATH", f"unsafe path in the source: {rel}")
        if rel not in inst and rel.lower() in inst_lower:       # Windows is case-insensitive: would overwrite unsaved
            raise Refused("HOTFIX_CASE_CLASH", f"{rel} differs from an installed file only by letter case")
        if rel not in inst:
            plan.add.append(rel)
        elif sha256(p) != sha256(inst[rel]):
            plan.replace.append(rel)
    plan.removed_in_source = sorted(r for r in inst if r not in src and r not in INSTALLED_ONLY)
    return plan


# ---------------------------------------------------------------------- source resolution
def _git(repo: Path, *args: str, check: bool = True) -> str:
    # local only, and no code from the (possibly untrusted) repo config: no fsmonitor, no hooks
    r = subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull, "-C", str(repo), *args],
                       capture_output=True, text=True, timeout=120)
    if check and r.returncode != 0:
        raise HotfixError("HOTFIX_GIT", f"git {' '.join(args[:2])} failed: {r.stderr.strip()[:200]}")
    return r.stdout


@dataclass
class Source:
    pkg: Path                                  # folder holding the new jarves package files
    repo_changes: list[str] | None            # repository files changed since the installed build; None = not checkable
    commit: str | None
    note: str = ""
    cleanup: Path | None = None
    base: str | None = None                    # commit the installed build was made from (for the ancestor check)
    git_root: Path | None = None               # repository holding base and commit (None = not checkable)
    checkout: Path | None = None               # working tree the target files are read from (a checkout folder source)
    reader: Callable[[str, str], str | None] | None = None   # tests: (repo path, "base"|"target") -> text


def resolve_source(spec: str | None, installed_commit: str | None, *, repo: Path | None = None,
                   base: str | None = None) -> Source:
    """`spec` = a folder (a repository checkout or a bare jarves package folder) or a git ref in `repo` (default: the
    repository around the current folder). The repository change set since the installed commit is read with git."""
    base = base or installed_commit
    p = Path(spec) if spec else None
    if p is not None and p.is_dir():
        if (p / PACKAGE_PREFIX).is_dir():                              # repository checkout (working tree)
            changes = None
            commit = None
            if (p / ".git").exists():
                commit = _git(p, "rev-parse", "HEAD").strip()
                if not base:
                    raise Refused("HOTFIX_NO_BASE", "the installed build does not record its commit, so the change set "
                                  "cannot be checked", "pass --base <commit the installed build was made from>")
                changes = [x for x in _git(p, "diff", "--name-only", base).splitlines() if x]
            return Source(p / PACKAGE_PREFIX.rstrip("/"), changes, commit,
                          "" if changes is not None else "plain folder: only the package files are compared",
                          base=base, git_root=p if commit else None, checkout=p)
        if (p / "__init__.py").is_file():                              # bare package folder
            return Source(p, None, None, "bare package folder: only its files are compared")
        raise Refused("HOTFIX_BAD_SOURCE", f"{p} is neither a repository checkout nor a jarves package folder")
    ref = spec or "HEAD"
    root = Path(_git(repo or Path.cwd(), "rev-parse", "--show-toplevel").strip())
    if not base:
        raise Refused("HOTFIX_NO_BASE", "the installed build does not record its commit, so the change set cannot be "
                      "checked", "pass --base <commit the installed build was made from>")
    if _git(root, "cat-file", "-t", base, check=False).strip() != "commit":
        raise Refused("HOTFIX_NO_BASE", f"commit {base[:12]} of the installed build is not in this repository",
                      "fetch it, or pass --base with a commit that is present")
    commit = _git(root, "rev-parse", ref + "^{commit}").strip()
    changes = [x for x in _git(root, "diff", "--name-only", base, commit).splitlines() if x]
    tmp = Path(tempfile.mkdtemp(prefix="jarves-hotfix-"))
    tar = tmp / "src.tar"
    r = subprocess.run(["git", "-C", str(root), "archive", "--format=tar", "-o", str(tar), commit,
                        PACKAGE_PREFIX.rstrip("/")], capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise HotfixError("HOTFIX_GIT", f"git archive failed: {r.stderr.strip()[:200]}")
    import tarfile
    with tarfile.open(tar) as t:
        t.extractall(tmp / "x", filter="data")
    return Source(tmp / "x" / "core" / "src" / "jarves", changes, commit, cleanup=tmp, base=base, git_root=root)


def read_repo_file(src: Source, rel: str, which: str) -> str | None:
    """Text of a repository file at the installed build's commit ("base") or in the target ("target"); None = absent or
    not readable. Slice 2 reads the lock and the bridge pin through this, never from the network."""
    if src.reader is not None:
        return src.reader(rel, which)
    if which == "target" and src.checkout is not None:
        f = src.checkout / rel
        return f.read_text(encoding="utf-8") if f.is_file() and not f.is_symlink() else None
    rev = src.base if which == "base" else src.commit
    if not src.git_root or not rev:
        return None
    r = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(src.git_root), "show", f"{rev}:{rel}"],
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    return r.stdout if r.returncode == 0 else None


# ---------------------------------------------------------------------- hooks (real ones; tests pass fakes)
@dataclass
class Hooks:
    core_running: Callable[[], bool]
    stop_core: Callable[[], None]
    start_core: Callable[[], None]
    backup: Callable[[], str]
    doctor: Callable[[], int]
    log: Callable[[str], None] = print
    bridge_running: Callable[[], bool] | None = None     # slice 2: the existing bridge status/stop/start path
    stop_bridge: Callable[[], None] | None = None
    start_bridge: Callable[[], None] | None = None


def default_hooks(cfg, home: Path, installed_pkg: Path) -> Hooks:
    from . import backup as backup_mod
    from . import cli

    def running() -> bool:
        return cli._ws_call(home, cfg.link.port, {"type": "status.request", "payload": {}}, "status", timeout=2) is not None

    def stop() -> None:
        a = None
        cli.cmd_stop(a, cfg, home)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and running():
            time.sleep(0.5)
        if running():
            raise HotfixError("HOTFIX_STOP", "the Core did not stop within 30 s")

    def start() -> None:
        cli._background_spawn(home, False)             # pythonw, no window, no console, no sound
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            time.sleep(0.5)
            if running():
                return
        raise HotfixError("HOTFIX_START", "the Core did not answer within 90 s after the restart")

    def doctor() -> int:
        env = dict(os.environ, PYTHONPATH=str(installed_pkg.parent))
        r = subprocess.run([sys.executable, "-m", "jarves", "doctor"], capture_output=True, text=True, timeout=300,
                           env=env, cwd=str(home),
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0)
        (home / HOTFIX_DIR).mkdir(parents=True, exist_ok=True)
        (home / HOTFIX_DIR / "last-doctor.txt").write_text((r.stdout or "") + (r.stderr or ""), encoding="utf-8")
        return r.returncode

    def bridge_running() -> bool:
        from . import whatsapp_bridge as wab
        return wab.status_or_none(home) is not None

    def stop_bridge() -> None:
        from . import whatsapp_bridge as wab                   # the same admin /shutdown the CLI `whatsapp stop` uses
        try:
            wab.BridgeClient(home, admin=True, timeout=5.0).call("POST", "/shutdown")
        except wab.BridgeError:
            pass
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and bridge_running():
            time.sleep(0.5)
        if bridge_running():
            raise HotfixError("HOTFIX_BRIDGE_STOP", "the WhatsApp bridge did not stop within 30 s")

    def start_bridge() -> None:
        from . import whatsapp_bridge as wab
        ok, msg = wab.start_bridge(home)
        if not ok:
            raise HotfixError("HOTFIX_BRIDGE_START", f"the WhatsApp bridge did not start: {msg}")

    return Hooks(running, stop, start, lambda: str(backup_mod.create(home, "manual", note="before a hotfix")), doctor,
                 bridge_running=bridge_running, stop_bridge=stop_bridge, start_bridge=start_bridge)


# ---------------------------------------------------------------------- classification (#459)
DEPENDENCY_NAMES = ("pyproject.toml", "uv.lock", "poetry.lock", "pipfile", "pipfile.lock", "setup.py", "setup.cfg")
SLICE2 = ("is not covered by the verified hotfix (slice 2 delivers pyproject.toml/uv.lock dependency changes and the pinned "
          "bridge exe only); use the full installer")


@dataclass
class Slice2Ctx:
    """What classify needs to verify dependency and bridge changes (no network here)."""
    src: Source
    installed_pkg: Path
    opts: object                                   # hotfix_slice2.S2Options

    @property
    def site(self) -> Path:
        return Path(self.installed_pkg).parent


def _is_dependency_file(r: str) -> bool:
    name = r.rsplit("/", 1)[-1].lower()
    return name in DEPENDENCY_NAMES or (name.startswith("requirements") and name.endswith((".txt", ".in")))


def _is_bridge_change(r: str) -> bool:
    low = r.lower()
    return "bridge_pin" in low or "bridge-pin" in low or low.startswith("bridge/")


JARVES_REMOTE = re.compile(r"github\.com[/:](jarvis-igerman-ai|igalgerman)/jarves(\.git)?/?$", re.I)


def require_trusted_target(src: Source) -> None:
    """Review #465: slice 2 installs an exe and wheels whose ONLY trust anchors (bridge pin, uv.lock hashes) come from the
    target itself, so the target must be a commit the project published: a git ref reachable from `main` of the jarves
    remote. A folder or a working tree (anything can be put there) or a branch that is not on main is refused for the
    dependency and bridge categories; plain code fixes from such sources are unchanged."""
    if src.reader is not None:                          # tests inject the repository texts; the CLI never sets a reader
        return
    if src.checkout is not None or not src.git_root or not src.commit:
        raise Refused("HOTFIX_UNTRUSTED_SOURCE", "dependency and bridge changes are applied only from a git ref (a folder "
                      "or working tree is not a published commit)", "jarvis hotfix apply --from <a commit on jarves main>")
    names = set()
    for ln in _git(src.git_root, "remote", "-v", check=False).splitlines():
        parts = ln.split()
        if len(parts) >= 2 and JARVES_REMOTE.search(parts[1]):
            names.add(parts[0])
    for r in sorted(names):
        rc = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(src.git_root), "merge-base", "--is-ancestor",
                             src.commit, f"refs/remotes/{r}/main"], capture_output=True, text=True, timeout=120).returncode
        if rc == 0:
            return
    raise Refused("HOTFIX_UNTRUSTED_SOURCE", f"commit {src.commit[:12]} is not on main of the jarves remote: dependency and "
                  "bridge changes are applied only from published commits", "merge first (and git fetch), or use the full installer")


def _s2_dependencies(ctx: Slice2Ctx, lock_files: list[str]):
    from . import hotfix_slice2 as s2
    opts = ctx.opts
    py = s2.resolve_python(ctx.installed_pkg, opts.python)
    env = opts.py_env or s2.probe_env(py, opts.runner)
    lock_rel = next((x for x in lock_files if x.endswith("uv.lock")), None)
    target = read_repo_file(ctx.src, lock_rel, "target") if lock_rel else None
    if target is None:
        raise Refused(s2.UNVERIFIED, f"the dependency lock ({lock_rel}) cannot be read from the target", "use the full installer")
    base = read_repo_file(ctx.src, lock_rel, "base")
    return s2.build_dep_plan(base, target, env, ctx.site, opts)


def _s2_bridge(ctx: Slice2Ctx):
    from . import hotfix_slice2 as s2
    pin = read_repo_file(ctx.src, s2.PIN_PATH, "target")
    return s2.plan_bridge(pin, s2.install_root_of(ctx.installed_pkg), ctx.opts)


def classify(plan: Plan, repo_changes: list[str] | None, ctx: Slice2Ctx | None = None) -> tuple[Plan, list[str]]:
    """Split the change into what is applied and what 'only affects the next full installer'. Dependency (pyproject.toml,
    uv.lock) and bridge (pin / code) changes are VERIFIED categories (slice 2, issue #460): they become `ok.deps` /
    `ok.bridge` or a Refused with its own code (HOTFIX_DEP_UNVERIFIED, HOTFIX_BRIDGE_UNVERIFIED). Without a context
    (nothing to verify against) or for other dependency files: Refused(HOTFIX_NEEDS_SLICE2). Never skipped silently."""
    deps: list[str] = []
    other_deps: list[str] = []
    bridge: list[str] = []
    pending: list[str] = []
    for rel in repo_changes or []:
        r = rel.replace("\\", "/")
        if _is_dependency_file(r):
            (deps if r.rsplit("/", 1)[-1].lower() in ("pyproject.toml", "uv.lock") else other_deps).append(r)
        elif _is_bridge_change(r):
            bridge.append(r)
        elif r.lower().startswith(FULL_INSTALLER_PREFIXES):      # case-insensitive: Windows paths
            pending.append(r)
    unplaceable: list[str] = []
    for rel in plan.files:
        suffix = Path(rel).suffix.lower()
        if (suffix in BINARY_SUFFIXES or suffix not in DATA_SUFFIXES
                or (suffix == ".py" and any(Path(rel).stem.startswith(n) for n in INSTALLER_CODE_NAMES))):
            unplaceable.append(PACKAGE_PREFIX + rel)           # inside the installed package: runtime, never skipped
    if other_deps or unplaceable or ((deps or bridge) and ctx is None):
        parts = []
        if unplaceable:
            parts.append("package files that cannot be hot-patched (binary, unknown type or installer code: "
                         + ", ".join(unplaceable[:4]) + ")")
        if deps or other_deps:
            parts.append("dependencies changed (" + ", ".join((other_deps + deps)[:4]) + ")")
        if bridge:
            parts.append("bridge pin/binary changed (" + ", ".join(bridge[:4]) + ")")
        raise Refused("HOTFIX_NEEDS_SLICE2", "; ".join(parts) + ": " + SLICE2, "use the full installer for this change")
    ok = Plan(removed_in_source=plan.removed_in_source)
    if (deps and any(x.endswith("uv.lock") for x in deps)) or bridge:
        require_trusted_target(ctx.src)
    if deps:
        if any(x.endswith("uv.lock") for x in deps):
            dp = _s2_dependencies(ctx, deps)
            ok.deps = dp                                 # may be empty (everything already installed): reported
        else:
            pending.extend(deps)                         # pyproject metadata without a lock change: nothing to install
    if bridge:
        act = _s2_bridge(ctx)
        ok.bridge = act
        if act is None:
            pending.extend(bridge)                       # the installed exe already is the pinned one
    for rel in plan.files:
        if rel in plan.replace:
            ok.replace.append(rel)
        else:
            ok.add.append(rel)
    return ok, sorted(set(pending))


def _site_dirs(installed_pkg: Path) -> list[Path]:
    dirs = [installed_pkg.parent]
    try:
        if installed_pkg.resolve() == Path(__file__).resolve().parent:      # patching the running build: its environment
            import site
            dirs += [Path(x) for x in site.getsitepackages() + [site.getusersitepackages()]]
    except Exception:
        pass
    return [d for d in dirs if d.is_dir()]


_ZIP_TOPS: dict[tuple[str, int, int], set[str] | None] = {}      # per run: (path, size, mtime) -> top-level names
_ZIP_MAX_ENTRIES = 200_000
_ARCHIVE_EXT = (".zip", ".egg", ".whl")


def _zip_tops(zp: Path) -> set[str] | None:
    """Top-level module/package names inside a zip archive (what the interpreter's zipimport would find), or None when
    the archive is unreadable/corrupt/too big (fail closed: nothing in it counts as present). Names with absolute paths,
    drive letters or '..' parts are ignored."""
    import zipfile
    try:
        st = zp.stat()
        key = (str(zp), st.st_size, int(st.st_mtime))
    except OSError:
        return None
    if key in _ZIP_TOPS:
        return _ZIP_TOPS[key]
    tops: set[str] | None
    try:
        with zipfile.ZipFile(zp) as z:
            names = z.namelist()
        if len(names) > _ZIP_MAX_ENTRIES:
            raise ValueError("too many entries")
        tops = set()
        for n in names:
            n = n.replace("\\", "/")
            parts = n.split("/")
            if n.startswith("/") or ".." in parts or ":" in parts[0] or not parts[0]:
                continue
            first = parts[0]
            if len(parts) > 1:
                tops.add(first)
            elif first.endswith(".py"):
                tops.add(first[:-3])
            elif "." in first and first.rsplit(".", 1)[1] in ("pyd", "so", "pyc"):
                tops.add(first.split(".")[0])
    except Exception:
        tops = None
    _ZIP_TOPS[key] = tops
    return tops


def _expand_pth(dirs: list[Path]) -> tuple[list[Path], list[Path]]:
    """Honour *.pth files like site.py: plain lines name extra directories or zip archives (relative to the .pth's
    folder); 'import' lines are never executed. Returns (directories, zip archives)."""
    out_dirs = list(dirs)
    zips: list[Path] = []
    for d in dirs:
        try:
            pths = sorted(d.glob("*.pth"))[:200]
        except OSError:
            continue
        for pth in pths:
            try:
                lines = pth.read_text(encoding="utf-8-sig", errors="replace").splitlines()[:1000]
            except OSError:
                continue
            for line in lines:
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("import"):
                    continue
                t = d / line
                if t.is_dir():
                    if t not in out_dirs:
                        out_dirs.append(t)
                elif t.is_file() and t.suffix.lower() in _ARCHIVE_EXT:
                    zips.append(t)
    return out_dirs, zips


def _module_present(top: str, dirs: list[Path]) -> bool:
    """True when the interpreter would find `top` in these site folders: a package folder, a module file, or a top-level
    entry of a zip archive named by a .pth file (or an extra .pth directory). Unreadable zips count as missing."""
    import re
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", top):
        return False
    all_dirs, zips = _expand_pth(dirs)
    for d in all_dirs:
        if (d / top).is_dir() or (d / (top + ".py")).is_file() or any(d.glob(top + ".*")):
            return True
    for zp in zips:
        tops = _zip_tops(zp)
        if tops is not None and top in tops:
            return True
    return False


def scan_new_imports(pkg: Path, rels: list[str], installed_pkg: Path, site_dirs: list[Path] | None = None) -> list[str]:
    """Third-party top-level modules imported by the given Python files (any import statement, anywhere in the file:
    conservative) that are not in the installed environment. Unparseable files are reported too."""
    import ast
    dirs = site_dirs if site_dirs is not None else _site_dirs(installed_pkg)
    std = set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}
    missing: dict[str, str] = {}
    for rel in rels:
        if not rel.endswith(".py"):
            continue
        try:
            tree = ast.parse((pkg / rel).read_text(encoding="utf-8"), filename=rel)
        except (SyntaxError, ValueError, OSError):
            missing.setdefault("<unparseable>", rel)
            continue
        known: set[str] = set()                          # modules the installed copy already imports: not new
        try:
            old = ast.parse((installed_pkg / rel).read_text(encoding="utf-8"))
            for n0 in ast.walk(old):
                if isinstance(n0, ast.Import):
                    known.update(a.name.split(".")[0] for a in n0.names)
                elif isinstance(n0, ast.ImportFrom) and n0.level == 0 and n0.module:
                    known.add(n0.module.split(".")[0])
        except (SyntaxError, ValueError, OSError):
            pass
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for n in names:
                top = n.split(".")[0]
                if top in std or top == "jarves" or top in missing or top in known:
                    continue
                if not _module_present(top, dirs):
                    missing[top] = rel
    return [f"{m} (in {r})" for m, r in sorted(missing.items())]


def check_ancestor(src: Source) -> None:
    """For a git source the installed build's commit must be an ancestor of the target commit (fail closed)."""
    if not src.git_root or not src.base or not src.commit:
        return                                          # not a git source: only the package files can be compared
    r = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(src.git_root), "merge-base", "--is-ancestor",
                        src.base, src.commit], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise Refused("HOTFIX_BASE_NOT_ANCESTOR", f"the installed build's commit {src.base[:12]} is not an ancestor of "
                      f"{src.commit[:12]}", "use the full installer, or pass the right --base")


# ---------------------------------------------------------------------- apply / revert
def hotfix_root(home: Path) -> Path:
    return Path(home) / HOTFIX_DIR


def installed_info(installed_pkg: Path) -> dict:
    return _read_json(installed_pkg / BUILD_INFO_NAME)


def latest_manifest(home: Path) -> tuple[Path, dict] | None:
    root = hotfix_root(home)
    if not root.is_dir():
        return None
    for d in sorted((x for x in root.iterdir() if (x / "manifest.json").is_file()), reverse=True):
        return d, _read_json(d / "manifest.json")
    return None


def _restart(hooks: Hooks, was_running: bool) -> None:
    if was_running:
        if hooks.core_running():
            hooks.stop_core()
        hooks.start_core()


def _s2_active(plan: Plan) -> tuple[bool, bool]:
    return bool(plan.deps is not None and plan.deps.items), plan.bridge is not None


def apply(installed_pkg: Path, src: Source, home: Path, hooks: Hooks, *, dry_run: bool = False,
          restart: bool = True, python_only: bool = False, site_dirs: list[Path] | None = None,
          s2opts=None) -> dict:
    """Returns the manifest. Raises Refused before touching anything; after the first write any failure restores
    the originals and raises HotfixError('HOTFIX_REVERTED'). `s2opts` (hotfix_slice2.S2Options) carries --bridge-exe,
    --python and the test fakes for the dependency/bridge slice."""
    from . import hotfix_slice2 as s2
    installed_pkg, home = Path(installed_pkg), Path(home)
    if not (installed_pkg / "__init__.py").is_file():
        raise Refused("HOTFIX_NO_INSTALL", f"{installed_pkg} is not a jarves package folder")
    opts = s2opts or s2.S2Options()
    plan = build_plan(src.pkg, installed_pkg)
    check_ancestor(src)
    plan, pending = classify(plan, src.repo_changes, Slice2Ctx(src, installed_pkg, opts))
    has_deps, has_bridge = _s2_active(plan)
    missing = scan_new_imports(src.pkg, plan.files, installed_pkg, site_dirs) if plan.files else []
    if missing and not has_deps:
        raise Refused("HOTFIX_NEW_DEPENDENCY", "an updated Python file imports a module that is not installed: "
                      + "; ".join(missing[:6]) + ": " + SLICE2, "change uv.lock in the same commit so the dependency is "
                      "delivered, or use the full installer (an unreadable libraries archive also counts as missing)")
    if not plan.files and not has_deps and not has_bridge:
        return {"status": "nothing", "files": [], "pending_installer": pending,
                "deps_already_installed": list(plan.deps.already_installed) if plan.deps else []}
    if dry_run:
        out = {"status": "dry-run", "files": plan.files, "replace": plan.replace, "add": plan.add, "pending_installer": pending}
        if plan.deps is not None:
            out["deps"] = plan.deps.describe()
            out["deps_already_installed"] = list(plan.deps.already_installed)
            out["deps_not_removed"] = list(plan.deps.removed_in_target)
            out["deps_requirements"] = plan.deps.requirements()
        if has_bridge:
            out["bridge"] = {"version": plan.bridge.version, "sha256": plan.bridge.sha256[:12], "dest": str(plan.bridge.dest),
                             "replaces": (plan.bridge.old_sha256 or "none")[:12]}
        if missing:
            out["imports_to_check_after_download"] = missing
        return out
    stage = None
    wheels: dict[str, Path] = {}
    try:
        if has_deps:                                  # network ONLY here: the locked wheels, verified before any write
            stage = s2.make_tmp()
            wheels = s2.download_wheels(plan.deps, stage / "wheels", opts)
            for whl in wheels.values():
                s2.verify_wheel(whl, installed_pkg.parent)  # paths, scripts, start-up hooks, RECORD hashes: before the first write
            if missing:
                provided = {p.split("/")[0].removesuffix(".py").lower() for w in wheels.values() for p in s2.wheel_paths(w)[0]}
                still = [m for m in missing if m.split(" ")[0].lower() not in provided]
                if still:
                    raise Refused("HOTFIX_NEW_DEPENDENCY", "an updated Python file imports a module that neither is installed "
                                  "nor comes with the locked dependencies: " + "; ".join(still[:6]), "use the full installer")
        return _apply_locked(installed_pkg, src, home, hooks, plan, pending, restart, opts, wheels, stage)
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)


def _apply_locked(installed_pkg: Path, src: Source, home: Path, hooks: Hooks, plan: Plan, pending: list[str],
                  restart: bool, opts, wheels: dict[str, Path], stage: Path | None) -> dict:
    from . import hotfix_slice2 as s2
    has_deps, has_bridge = _s2_active(plan)
    sp = installed_pkg.parent
    info = installed_info(installed_pkg)
    prev_hotfix = info.get("hotfix") if isinstance(info.get("hotfix"), dict) else {}
    hid = _now_id()
    d = hotfix_root(home) / hid
    (d / "originals").mkdir(parents=True, exist_ok=True)
    was_running = bool(hooks.core_running())
    backup_name = hooks.backup()
    baseline = hooks.doctor()
    manifest: dict = {"id": hid, "status": "applying", "installed_pkg": str(installed_pkg), "backup": backup_name,
                      "core_was_running": was_running, "doctor_before": baseline, "source_commit": src.commit,
                      "base_version": prev_hotfix.get("base_version") or info.get("version"),
                      "base_commit": prev_hotfix.get("base_commit") or info.get("commit"),
                      "replaced": [], "added": [], "build_info_original": None,
                      "pending_installer": pending}
    # originals first (a full copy of what will be replaced), then the manifest, then the first write
    for rel in plan.replace:
        dst = d / "originals" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(installed_pkg / rel, dst)
        manifest["replaced"].append({"path": rel, "sha256_original": sha256(dst), "sha256_new": sha256(src.pkg / rel)})
    for rel in plan.add:
        manifest["added"].append({"path": rel, "sha256_new": sha256(src.pkg / rel)})
    bi = installed_pkg / BUILD_INFO_NAME
    if bi.is_file():
        shutil.copyfile(bi, d / "originals" / BUILD_INFO_NAME)
        manifest["build_info_original"] = sha256(d / "originals" / BUILD_INFO_NAME)
    if has_deps:
        manifest["deps"] = s2.begin_dep_backup(plan.deps, wheels, sp, d / "deps_originals")
    if has_bridge:
        manifest["bridge"] = s2.bridge_backup(plan.bridge, d / "bridge_original")
        manifest["bridge"]["was_running"] = bool(hooks.bridge_running and hooks.bridge_running())
    _write_json(d / "manifest.json", manifest)
    try:
        if (has_deps or has_bridge) and was_running:
            hooks.stop_core()                           # releases locked .pyd/.dll files; restarted below
        if has_bridge:
            if manifest["bridge"]["was_running"] and hooks.stop_bridge:
                hooks.stop_bridge()
            s2.bridge_install(plan.bridge)
        if has_deps:
            tmp = stage or s2.make_tmp()
            manifest["dep_method"] = s2.install_deps(plan.deps, wheels, manifest["deps"], sp, _s2_python(installed_pkg, opts),
                                                     opts, tmp, hooks.log)
        for rel in plan.files:
            _atomic_copy(src.pkg / rel, installed_pkg / rel)
        _mark_build(installed_pkg, info, manifest, plan)
        if has_bridge and manifest["bridge"]["was_running"] and hooks.start_bridge:
            hooks.start_bridge()
        if restart:
            _restart(hooks, was_running)
        rc = hooks.doctor()
        manifest["doctor_rc"] = rc
        if rc != 0 and baseline == 0:         # worse than before the hotfix (a problem that was already there is not ours)
            raise HotfixError("HOTFIX_DOCTOR", f"jarvis doctor reported problems after the hotfix (exit {rc})")
        manifest["status"] = "applied"
        _write_json(d / "manifest.json", manifest)
        return manifest
    except BaseException as e:
        try:
            if has_deps and hooks.core_running():
                hooks.stop_core()
            _revert_dir(installed_pkg, d, manifest, hooks, bridge_was_running=bool(manifest.get("bridge", {}).get("was_running")))
            if restart:
                _restart(hooks, was_running)
        except Exception as e2:                     # the revert itself failed: say so loudly, keep the manifest
            manifest["status"] = "revert-failed"
            _write_json(d / "manifest.json", manifest)
            raise HotfixError("HOTFIX_REVERT_FAILED", f"hotfix failed ({e}) and the automatic revert also failed ({e2}); "
                              f"originals are in {d / 'originals'}", "run: jarvis hotfix revert") from e
        manifest["status"] = "reverted"
        manifest["error"] = str(e)
        _write_json(d / "manifest.json", manifest)
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        raise HotfixError("HOTFIX_REVERTED", f"hotfix failed and was reverted automatically: {e}") from e


def _s2_python(installed_pkg: Path, opts) -> Path:
    from . import hotfix_slice2 as s2
    return s2.resolve_python(installed_pkg, opts.python)


def _mark_build(installed_pkg: Path, info: dict, manifest: dict, plan: Plan) -> None:
    prev = info.get("hotfix") if isinstance(info.get("hotfix"), dict) else {}
    new = dict(info)
    new["hotfix"] = {"id": manifest["id"], "n": int(prev.get("n", 0)) + 1, "base_version": manifest["base_version"],
                     "base_commit": manifest["base_commit"], "commit": manifest["source_commit"],
                     "applied": manifest["id"], "files": len(plan.files)}
    if plan.bridge is not None:
        new["hotfix"]["bridge"] = {"version": plan.bridge.version, "sha256": plan.bridge.sha256}
    if plan.deps is not None and plan.deps.items:
        new["hotfix"]["deps"] = [f"{i.name}=={i.version}" for i in plan.deps.items]
    if manifest["source_commit"]:
        new["commit"] = manifest["source_commit"]
    hashes = new.get("payload_sha256")
    if isinstance(hashes, dict):                       # keep the installer self-check (canary) honest
        for rel in plan.files:
            if rel in hashes:
                hashes[rel] = sha256(installed_pkg / rel)
    _write_json(installed_pkg / BUILD_INFO_NAME, new)


def _revert_dir(installed_pkg: Path, d: Path, manifest: dict, hooks: Hooks | None = None, *,
                bridge_was_running: bool = False) -> list[str]:
    """Put back every original, delete every added file, restore the build info. Verifies the saved originals first.
    Slice 2: the dependency files and the bridge exe are restored too (the bridge is stopped through the hooks first and
    restarted only if it ran)."""
    from . import hotfix_slice2 as s2
    orig = d / "originals"
    for item in manifest.get("replaced", []):
        saved = orig / item["path"]
        if not saved.is_file() or sha256(saved) != item["sha256_original"]:
            raise HotfixError("HOTFIX_ORIGINAL_BAD", f"saved original of {item['path']} is missing or changed")
    done = []
    if manifest.get("bridge"):
        if hooks and hooks.bridge_running and hooks.stop_bridge and hooks.bridge_running():
            hooks.stop_bridge()
        s2.bridge_revert(manifest["bridge"], d / "bridge_original")
        done.append("bridge")
        if bridge_was_running and hooks and hooks.start_bridge:
            hooks.start_bridge()
    if manifest.get("deps"):
        s2.revert_deps(manifest["deps"], installed_pkg.parent, d / "deps_originals")
        done.extend(f"{e['name']}=={e['old_version'] or '(removed)'}" for e in manifest["deps"])
    for item in manifest.get("replaced", []):
        _atomic_copy(orig / item["path"], installed_pkg / item["path"])
        done.append(item["path"])
    for item in manifest.get("added", []):
        try:
            (installed_pkg / item["path"]).unlink()
            done.append(item["path"])
        except FileNotFoundError:
            pass
    saved_bi = orig / BUILD_INFO_NAME
    if saved_bi.is_file():
        _atomic_copy(saved_bi, installed_pkg / BUILD_INFO_NAME)
    else:                                             # there was none: drop the one we wrote
        (installed_pkg / BUILD_INFO_NAME).unlink(missing_ok=True)
    return done


def revert(installed_pkg: Path, home: Path, hooks: Hooks, *, restart: bool = True) -> dict:
    installed_pkg, home = Path(installed_pkg), Path(home)
    found = latest_manifest(home)
    if not found:
        raise Refused("HOTFIX_NONE", "no hotfix to revert")
    d, man = found
    if man.get("status") not in ("applied", "applying", "revert-failed"):
        raise Refused("HOTFIX_NONE", f"the latest hotfix ({man.get('id')}) is already {man.get('status')}")
    cur = (installed_info(installed_pkg).get("hotfix") or {})
    if man.get("status") == "applied" and cur.get("id") != man.get("id"):
        raise Refused("HOTFIX_REPLACED", "a normal update already replaced this hotfix; nothing to revert",
                      "reverting now would downgrade the build")
    was_running = bool(hooks.core_running())
    if man.get("deps") and was_running:
        hooks.stop_core()                              # locked files; restarted below
    bridge_was_running = bool(man.get("bridge") and hooks.bridge_running and hooks.bridge_running())
    done = _revert_dir(installed_pkg, d, man, hooks, bridge_was_running=bridge_was_running)
    if restart:
        _restart(hooks, was_running)
    man["status"] = "reverted"
    _write_json(d / "manifest.json", man)
    return {"status": "reverted", "id": man["id"], "files": done}


def status(installed_pkg: Path, home: Path) -> dict:
    info = installed_info(installed_pkg)
    found = latest_manifest(home)
    pending = found[1].get("pending_installer") if found and found[1].get("status") == "applied" else None
    return {"version": info.get("version"), "hotfix": info.get("hotfix"),
            "latest": ({"id": found[1].get("id"), "status": found[1].get("status")} if found else None),
            "pending_installer": list(pending or [])}


# ---------------------------------------------------------------------- CLI
def locate_installed(override: str | None) -> Path:
    from . import updates
    if override:
        p = Path(override)
        return p / "jarves" if (p / "jarves" / "__init__.py").is_file() else p
    if not updates.is_installed_build():
        raise Refused("HOTFIX_NOT_INSTALLED", "this is not an installed build (a development checkout)",
                      "pass --install <site-packages folder> to patch another install")
    return Path(__file__).resolve().parent


def add_cli(sub) -> None:
    p = sub.add_parser("hotfix", help="hot-patch a code-only fix into the installed build in ~1 minute: "
                                      "apply [--from <git ref|folder>] [--python-only] | revert | status (any code fix; build-only files are listed, not refused)")
    p.add_argument("action", nargs="?", default="status", choices=["apply", "revert", "status"])
    p.add_argument("--from", dest="source", help="git ref (default HEAD) or a folder: a checkout or a jarves package")
    p.add_argument("--repo", help="repository for a git ref (default: the current folder's)")
    p.add_argument("--base", help="commit the installed build was made from (when the build does not record it)")
    p.add_argument("--install", help="site-packages folder of another install (default: this installed build)")
    p.add_argument("--dry-run", action="store_true", help="show what would change; touch nothing")
    p.add_argument("--no-restart", action="store_true", help="never restart the Core")
    p.add_argument("--bridge-exe", help="slice 2: a locally built or CI-artifact whatsapp_bridge.exe; used ONLY if its sha256 equals "
                   "packaging/bridge_pin.json of the target commit")
    p.add_argument("--python", help="slice 2: the installed python.exe (default: <install>/python/python.exe) used to install the locked wheels")
    p.add_argument("--python-only", action="store_true", help="alias of the default: files that only affect the next full installer are skipped "
                   "and listed, never a reason to refuse")


def _say(he: str, en: str) -> str:
    return clilang.fmt(he, en)


def _print_pending(files: list[str]) -> None:
    if files:
        print(_say("משפיעים רק על המתקין המלא הבא", "only affects the next full installer") + f" ({len(files)}): "
              + ", ".join(files[:20]) + (" ..." if len(files) > 20 else ""))


def _print_s2(r: dict) -> None:
    for x in r.get("deps") or []:
        print(_say("היה מותקן גלגל", "would install wheel") + f": {x['name']} {x['old_version'] or '-'} -> {x['version']}  sha256 {x['sha256']}...  "
              f"{x['size'] if x['size'] is not None else '?'} bytes  {x['host']}  {x['file']}")
    if r.get("deps_already_installed"):
        print(_say("כבר מותקן", "already installed") + ": " + ", ".join(r["deps_already_installed"]))
    if r.get("deps_not_removed"):
        print(_say("הוסרו מהנעילה ולא נמחקו", "dropped from the lock, left installed") + ": " + ", ".join(r["deps_not_removed"]))
    b = r.get("bridge")
    if b:
        print(_say("גשר וואטסאפ יוחלף", "bridge exe would be replaced") + f": {b['replaces']}... -> {b['sha256']}... (v{b['version']}) {b['dest']}")
    if r.get("imports_to_check_after_download"):
        print(_say("ייבוא שייבדק מול הגלגלים אחרי ההורדה", "imports checked against the wheels after the download")
              + ": " + ", ".join(r["imports_to_check_after_download"]))


def cmd_hotfix(a, cfg, home) -> int:
    home = Path(home)
    try:
        installed = locate_installed(a.install)
        if a.action == "status":
            s = status(installed, home)
            hf = s["hotfix"]
            print(_say("גרסה", "version") + f" {s['version']}" + (f" +hotfix.{hf['n']} ({hf.get('commit') or '?'})" if hf else ""))
            print(_say("תיקון מהיר אחרון", "latest hotfix") + f": {s['latest'] or '-'}")
            if s["latest"] and s["latest"].get("status") in ("applying", "revert-failed"):
                print(_say("תיקון מהיר נקטע באמצע: הריצו  jarvis hotfix revert  (או התקינו גרסה מלאה)",
                           "a hotfix was interrupted half way: run  jarvis hotfix revert  (or use the full installer)"), file=sys.stderr)
            _print_pending(s["pending_installer"])
            return 0
        if a.action == "apply":
            from .cli import _cli_features
            if not _cli_features(cfg, home).active(FEATURE):
                raise Refused("HOTFIX_OFF", "the hot-patch feature is switched off", "jarvis features on hotfix")
        hooks = default_hooks(cfg, home, installed)
        if a.action == "revert":
            r = revert(installed, home, hooks, restart=not a.no_restart)
            print(_say("שוחזרו הקבצים המקוריים", "restored the original files") + f": {len(r['files'])} ({r['id']})")
            return 0
        info = installed_info(installed)
        src = resolve_source(a.source, info.get("commit"), repo=Path(a.repo) if a.repo else None, base=a.base)
        try:
            from . import hotfix_slice2 as s2
            be, py = getattr(a, "bridge_exe", None), getattr(a, "python", None)
            r = apply(installed, src, home, hooks, dry_run=a.dry_run, restart=not a.no_restart,
                      python_only=bool(getattr(a, "python_only", False)),
                      s2opts=s2.S2Options(bridge_exe=Path(be) if be else None, python=Path(py) if py else None))
        finally:
            if src.cleanup:
                shutil.rmtree(src.cleanup, ignore_errors=True)
        if r["status"] in ("dry-run", "nothing"):
            _print_s2(r)
        if r["status"] == "nothing":
            print(_say("אין מה לעדכן: הקוד המותקן זהה", "nothing to do: the installed code is identical"))
        elif r["status"] == "dry-run":
            print(_say("היה משתנה", "would change") + f" ({len(r['files'])}): " + ", ".join(r["files"][:20]))
        else:
            if r.get("deps"):
                print(_say("תלויות הותקנו", "dependencies installed") + ": " + ", ".join(f"{e['name']}=={e['new_version']}" for e in r["deps"])
                      + f" ({r.get('dep_method')})")
            if r.get("bridge"):
                print(_say("גשר וואטסאפ הוחלף", "bridge exe replaced") + f": {r['bridge']['sha256_new'][:12]}... v{r['bridge']['version']}")
            print(_say("התיקון המהיר הותקן", "hotfix applied") + f": {len(r['replaced']) + len(r['added'])} file(s), "
                  + _say("הקור הופעל מחדש" if r["core_was_running"] and not a.no_restart else "הקור לא הופעל מחדש",
                         "core restarted" if r["core_was_running"] and not a.no_restart else "core not restarted")
                  + f". {_say('לביטול', 'undo with')}: jarvis hotfix revert")
        _print_pending(r.get("pending_installer") or [])
        if src.note:
            print("note: " + src.note)
        return 0
    except Refused as e:
        print(f"REFUSED: {e.message}" + (f"\n  {e.action}" if e.action else ""), file=sys.stderr)
        return 2
    except HotfixError as e:
        print(f"ERROR ({e.code}): {e.message}" + (f"\n  {e.action}" if e.action else ""), file=sys.stderr)
        return 1


COMMANDS = {"hotfix": cmd_hotfix}


# ---------------------------------------------------------------------- catalog
EVIDENCE = "UNIT test_hotfix.py (fake install tree and fake hooks); never run against a real install by the tests"
BLOCKER = "no real installed-build hotfix recorded yet (owner PC run pending)"
ABOUT = ("puts a code-only fix into the installed build in about a minute: backs up first, copies only the changed "
         "Python files, restarts the Core only if it ran, runs doctor, and one command reverts it. Build-only files are "
         "listed as affecting only the next installer; a pinned bridge exe and locked dependency wheels are delivered only after their sha256 hashes are verified")
hebrew.TEXT.update({
    ABOUT: "מכניס תיקון קוד בלבד לגרסה המותקנת תוך כדקה: מגבה קודם, מעתיק רק קבצי פייתון ששונו, מפעיל מחדש את הליבה רק "
           "אם רצה, מריץ doctor, ופקודה אחת מבטלת. קבצי בנייה בלבד מדווחים כמשפיעים רק על המתקין הבא; קובץ גשר מנועץ וגלגלי תלויות נעולים מותקנים רק אחרי אימות sha256",
    EVIDENCE: "בדיקות יחידה test_hotfix.py (עץ התקנה מזויף וקרסים מזויפים); הבדיקות אף פעם לא רצות על התקנה אמיתית",
    BLOCKER: "עוד לא תועד תיקון מהיר אמיתי בגרסה מותקנת (הרצה במחשב הבעלים ממתינה)",
    "rewrites the installed program files": "משכתב את קבצי התוכנה המותקנים",
    "the Pi runs from a git checkout, not an installed build": "ה-Pi רץ מ-git checkout ולא מגרסה מותקנת",
})

feat.register(FEATURE, "תיקון מהיר", "Hot-patch", True, risk="high", about=ABOUT,
              targets={"windows": {"status": "implemented_unverified", "evidence": EVIDENCE, "blocker": BLOCKER},
                       "linux_arm64_pi": {"status": "not_applicable",
                                          "blocker": "the Pi runs from a git checkout, not an installed build"}},
              permissions=["rewrites the installed program files"],
              settings="jarvis hotfix apply --from <ref>")
