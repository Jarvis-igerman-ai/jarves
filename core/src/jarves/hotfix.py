"""Hot-patch (issue #348): put a code-only fix into the INSTALLED build in about a minute instead of a 15 minute installer.

`jarvis hotfix apply [--from <git ref | folder>]`
  1. compares the source package (core/src/jarves) with the installed one, file by file (sha256);
  2. REFUSES (and says "use the full installer") unless the change is only Python source plus plain data files inside
     the jarves package: nothing under packaging/, bridge/, godot/, no dependency files (pyproject, lock), no
     installer code, no binaries (.exe/.dll/.pyd ...). When the source is a git checkout the whole repository change
     since the installed commit is checked, not just the package;
  3. `jarvis backup` first, then copies ONLY the changed files (temp file + atomic replace). The originals go to
     <home>/hotfix/<id>/originals/ and manifest.json records the sha256 of every original and of every new file;
  4. marks the build: `_build_info.json` gets a "hotfix" block (n, base version, commit, files) and `jarvis version`
     shows "<version>+hotfix.N". The numeric version is NOT changed, so any newer release replaces the hotfix like a
     normal update and a hotfix can never make an update look like a downgrade;
  5. restarts the Core ONLY if it ran before (recorded first), silently (no window, no sound), then runs `jarvis doctor`
     in a fresh process;
  6. fail closed: any error (copy, restart, doctor worse than before) restores the originals automatically.
  7. `--python-only` (issue #459, used by the automatic hotfix after a dry-run): files of categories this tool never
     applies (packaging, bridge, face app, installer code, binaries) are SKIPPED and listed as "pending for the next
     full installer" (output, manifest, `hotfix status`) instead of refusing the run. Still refused (fail closed): a
     dependency file change (pyproject, lock, requirements), a changed bridge pin or binary, an allowed Python file
     importing a third-party module that is not installed, and a base commit that is not an ancestor of the target.
`jarvis hotfix revert` restores the originals from the manifest (refused when a normal update already replaced the
hotfix, so it can never downgrade). `jarvis hotfix status` shows the current mark.

Nothing here runs unless the user types the command. Tests use a fake install tree and fake hooks, never the real one.
"""
from __future__ import annotations

import hashlib
import json
import os
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
FULL_INSTALLER_PREFIXES = ("packaging/", "bridge/", "godot/", "core/pyproject.toml", "core/uv.lock", "uv.lock",
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


def check_policy(plan: Plan, repo_changes: list[str] | None = None) -> None:
    """Raise Refused('use the full installer') unless the change is code-only. Fail closed."""
    why: list[str] = []
    for rel in plan.files:
        suffix = Path(rel).suffix.lower()
        if suffix in BINARY_SUFFIXES:
            why.append(f"{rel}: binary file")
        elif suffix not in DATA_SUFFIXES:
            why.append(f"{rel}: file type {suffix or '(none)'} is not plain source or data")
        elif suffix == ".py" and any(Path(rel).stem.startswith(n) for n in INSTALLER_CODE_NAMES):
            why.append(f"{rel}: installer code")
    for rel in repo_changes or []:
        r = rel.replace("\\", "/")
        if r.startswith(FULL_INSTALLER_PREFIXES):
            why.append(f"{r}: packaging, bridge, face app or dependencies")
    if why:
        shown = "; ".join(why[:6]) + (f" (+{len(why) - 6} more)" if len(why) > 6 else "")
        raise Refused("HOTFIX_NEEDS_INSTALLER", f"this change cannot be hot-patched: {shown}",
                      "use the full installer for this change")


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
                          base=base, git_root=p if commit else None)
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


# ---------------------------------------------------------------------- hooks (real ones; tests pass fakes)
@dataclass
class Hooks:
    core_running: Callable[[], bool]
    stop_core: Callable[[], None]
    start_core: Callable[[], None]
    backup: Callable[[], str]
    doctor: Callable[[], int]
    log: Callable[[str], None] = print


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

    return Hooks(running, stop, start, lambda: str(backup_mod.create(home, "manual", note="before a hotfix")), doctor)


# ---------------------------------------------------------------------- --python-only (#459)
DEPENDENCY_NAMES = ("pyproject.toml", "uv.lock", "poetry.lock", "pipfile", "pipfile.lock", "setup.py", "setup.cfg")


def _is_dependency_file(r: str) -> bool:
    name = r.rsplit("/", 1)[-1].lower()
    return name in DEPENDENCY_NAMES or (name.startswith("requirements") and name.endswith((".txt", ".in")))


def _is_bridge_pin_or_binary(r: str) -> bool:
    low = r.lower()
    name = low.rsplit("/", 1)[-1]
    if "bridge_pin" in low or "bridge-pin" in low:
        return True
    return low.startswith("bridge/") and ("pin" in name or Path(name).suffix in BINARY_SUFFIXES)


def classify_python_only(plan: Plan, repo_changes: list[str] | None) -> tuple[Plan, list[str]]:
    """Split the change into what may be applied and what is 'pending for the next full installer'. Raises Refused for
    what must never be skipped silently: dependency files and a changed bridge pin or binary."""
    hard: list[str] = []
    pending: list[str] = []
    for rel in repo_changes or []:
        r = rel.replace("\\", "/")
        if _is_dependency_file(r):
            hard.append(f"{r}: dependencies")
        elif _is_bridge_pin_or_binary(r):
            hard.append(f"{r}: bridge pin or binary")
        elif r.startswith(FULL_INSTALLER_PREFIXES):
            pending.append(r)
    if hard:
        shown = "; ".join(hard[:6]) + (f" (+{len(hard) - 6} more)" if len(hard) > 6 else "")
        raise Refused("HOTFIX_NEEDS_INSTALLER", f"this change cannot be hot-patched even with --python-only: {shown}",
                      "use the full installer for this change")
    ok = Plan(removed_in_source=plan.removed_in_source)
    for rel in plan.files:
        suffix = Path(rel).suffix.lower()
        blocked = (suffix in BINARY_SUFFIXES or suffix not in DATA_SUFFIXES
                   or (suffix == ".py" and any(Path(rel).stem.startswith(n) for n in INSTALLER_CODE_NAMES)))
        if blocked:
            pending.append(PACKAGE_PREFIX + rel)
        elif rel in plan.replace:
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


def _module_present(top: str, dirs: list[Path]) -> bool:
    for d in dirs:
        if (d / top).is_dir() or (d / (top + ".py")).is_file() or any(d.glob(top + ".*")):
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
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for n in names:
                top = n.split(".")[0]
                if top in std or top == "jarves" or top in missing:
                    continue
                if not _module_present(top, dirs):
                    missing[top] = rel
    return [f"{m} (in {r})" for m, r in sorted(missing.items())]


def check_ancestor(src: Source) -> None:
    """--python-only needs a git source: the installed build's commit must be an ancestor of the target commit."""
    if not src.git_root or not src.base or not src.commit:
        raise Refused("HOTFIX_NO_BASE", "--python-only needs a git source with the installed build's commit, so the "
                      "change set can be checked", "use --from <git ref or checkout> (and --base if needed)")
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


def apply(installed_pkg: Path, src: Source, home: Path, hooks: Hooks, *, dry_run: bool = False,
          restart: bool = True, python_only: bool = False, site_dirs: list[Path] | None = None) -> dict:
    """Returns the manifest. Raises Refused before touching anything; after the first write any failure restores
    the originals and raises HotfixError('HOTFIX_REVERTED')."""
    installed_pkg, home = Path(installed_pkg), Path(home)
    if not (installed_pkg / "__init__.py").is_file():
        raise Refused("HOTFIX_NO_INSTALL", f"{installed_pkg} is not a jarves package folder")
    plan = build_plan(src.pkg, installed_pkg)
    pending: list[str] = []
    if python_only:
        check_ancestor(src)
        plan, pending = classify_python_only(plan, src.repo_changes)
        bad = scan_new_imports(src.pkg, plan.files, installed_pkg, site_dirs) if plan.files else []
        if bad:
            raise Refused("HOTFIX_NEW_DEPENDENCY", "an updated Python file imports a module that is not installed: "
                          + "; ".join(bad[:6]), "use the full installer (it brings new dependencies)")
    if not plan.files:
        return {"status": "nothing", "files": [], "pending_installer": pending}
    if not python_only:
        check_policy(plan, src.repo_changes)
    if dry_run:
        return {"status": "dry-run", "files": plan.files, "replace": plan.replace, "add": plan.add,
                "pending_installer": pending}
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
                      "python_only": python_only, "pending_installer": pending}
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
    _write_json(d / "manifest.json", manifest)
    try:
        for rel in plan.files:
            _atomic_copy(src.pkg / rel, installed_pkg / rel)
        _mark_build(installed_pkg, info, manifest, plan)
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
            _revert_dir(installed_pkg, d, manifest)
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


def _mark_build(installed_pkg: Path, info: dict, manifest: dict, plan: Plan) -> None:
    prev = info.get("hotfix") if isinstance(info.get("hotfix"), dict) else {}
    new = dict(info)
    new["hotfix"] = {"id": manifest["id"], "n": int(prev.get("n", 0)) + 1, "base_version": manifest["base_version"],
                     "base_commit": manifest["base_commit"], "commit": manifest["source_commit"],
                     "applied": manifest["id"], "files": len(plan.files)}
    if manifest["source_commit"]:
        new["commit"] = manifest["source_commit"]
    hashes = new.get("payload_sha256")
    if isinstance(hashes, dict):                       # keep the installer self-check (canary) honest
        for rel in plan.files:
            if rel in hashes:
                hashes[rel] = sha256(installed_pkg / rel)
    _write_json(installed_pkg / BUILD_INFO_NAME, new)


def _revert_dir(installed_pkg: Path, d: Path, manifest: dict) -> list[str]:
    """Put back every original, delete every added file, restore the build info. Verifies the saved originals first."""
    orig = d / "originals"
    for item in manifest.get("replaced", []):
        saved = orig / item["path"]
        if not saved.is_file() or sha256(saved) != item["sha256_original"]:
            raise HotfixError("HOTFIX_ORIGINAL_BAD", f"saved original of {item['path']} is missing or changed")
    done = []
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
    done = _revert_dir(installed_pkg, d, man)
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
                                      "apply [--from <git ref|folder>] [--python-only] | revert | status (refuses what needs the installer)")
    p.add_argument("action", nargs="?", default="status", choices=["apply", "revert", "status"])
    p.add_argument("--from", dest="source", help="git ref (default HEAD) or a folder: a checkout or a jarves package")
    p.add_argument("--repo", help="repository for a git ref (default: the current folder's)")
    p.add_argument("--base", help="commit the installed build was made from (when the build does not record it)")
    p.add_argument("--install", help="site-packages folder of another install (default: this installed build)")
    p.add_argument("--dry-run", action="store_true", help="show what would change; touch nothing")
    p.add_argument("--no-restart", action="store_true", help="never restart the Core")
    p.add_argument("--python-only", action="store_true", help="apply only Python/dashboard/guide/locale/data files; "
                   "skip and list packaging, bridge, face app, installer and binary files as pending for the next full "
                   "installer (dependency changes, a changed bridge pin/binary and new third-party imports are still refused)")


def _say(he: str, en: str) -> str:
    return clilang.fmt(he, en)


def _print_pending(files: list[str]) -> None:
    if files:
        print(_say("ממתינים למתקין המלא הבא", "pending for the next full installer") + f" ({len(files)}): "
              + ", ".join(files[:20]) + (" ..." if len(files) > 20 else ""))


def cmd_hotfix(a, cfg, home) -> int:
    home = Path(home)
    try:
        installed = locate_installed(a.install)
        if a.action == "status":
            s = status(installed, home)
            hf = s["hotfix"]
            print(_say("גרסה", "version") + f" {s['version']}" + (f" +hotfix.{hf['n']} ({hf.get('commit') or '?'})" if hf else ""))
            print(_say("תיקון מהיר אחרון", "latest hotfix") + f": {s['latest'] or '-'}")
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
            r = apply(installed, src, home, hooks, dry_run=a.dry_run, restart=not a.no_restart,
                      python_only=bool(getattr(a, "python_only", False)))
        finally:
            if src.cleanup:
                shutil.rmtree(src.cleanup, ignore_errors=True)
        if r["status"] == "nothing":
            print(_say("אין מה לעדכן: הקוד המותקן זהה", "nothing to do: the installed code is identical"))
        elif r["status"] == "dry-run":
            print(_say("היה משתנה", "would change") + f" ({len(r['files'])}): " + ", ".join(r["files"][:20]))
        else:
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
         "Python files, restarts the Core only if it ran, runs doctor, and one command reverts it. Anything that touches "
         "the installer, the bridge or dependencies is refused")
hebrew.TEXT.update({
    ABOUT: "מכניס תיקון קוד בלבד לגרסה המותקנת תוך כדקה: מגבה קודם, מעתיק רק קבצי פייתון ששונו, מפעיל מחדש את הליבה רק "
           "אם רצה, מריץ doctor, ופקודה אחת מבטלת. כל שינוי שנוגע במתקין, בגשר או בתלויות נדחה",
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
