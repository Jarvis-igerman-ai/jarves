"""Hotfix slice 2 (issue #460): the two categories the plain code hotfix could not carry, as VERIFIED steps.

(1) Bridge exe. When packaging/bridge_pin.json or the bridge code changes, the new exe is taken ONLY from a file the
    operator names with `--bridge-exe PATH` (a local build or a CI artifact) and ONLY if its sha256 equals the pin in the
    TARGET commit. Nothing is downloaded. The bridge is stopped through the existing admin /shutdown path (never by
    process name), the exe is replaced atomically with a backup, restarted only if it ran, and revert restores it.

(2) Dependencies. When pyproject/uv.lock changed, the delta between the base lock and the target lock (only packages
    reachable from the project with the installer's extras, markers evaluated for the installed Python) is installed:
    hash-pinned binary wheels only, from the URLs the lock records (https, the lock's index host), each wheel checked
    against the lock's sha256 and size BEFORE anything is touched, then installed with `pip install --require-hashes
    --no-deps --no-index` from a generated requirements file (or, when the installed Python has no pip, by a small
    built-in extractor that re-checks every file against the wheel's RECORD). The touched files are backed up and revert
    restores them (and removes newly added packages). A missing hash, a hash or size mismatch, an sdist, a source other
    than a registry, a wheel with scripts or data outside site-packages: HOTFIX_DEP_UNVERIFIED, nothing is changed.

Everything here is pure or takes its I/O as arguments, so the tests use fake trees and a local fake index.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .hotfix import HotfixError, Refused, sha256

DEP_FILES = ("pyproject.toml", "uv.lock")
LOCK_NAMES = ("core/uv.lock", "uv.lock")
PIN_PATH = "packaging/bridge_pin.json"
BRIDGE_DEST_OK = re.compile(r"^bridge/whatsapp_bridge(\.exe)?$")   # the file whatsapp_bridge.bridge_command starts, nothing else
# Review #465: hosts are an allow-list, never taken from the (target-controlled) lock.
REGISTRY_HOSTS = {"pypi.org"}
WHEEL_HOSTS = {"files.pythonhosted.org", "pypi.org"}
TEST_HOSTS = {"127.0.0.1"}                              # only with allow_insecure_urls (tests)
SAFE_NAME = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
SAFE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+!_-]*$")
SAFE_WHEEL_FILE = re.compile(r"^[A-Za-z0-9_.+!-]+\.whl$")
MAX_UNPACKED_BYTES = 3 * 1024 * 1024 * 1024
STARTUP_HOOKS = ("sitecustomize.py", "usercustomize.py")
EXTRAS = ("voice", "camera", "tray", "browser")        # what packaging/build_windows.ps1 installs (no dev)
MAX_WHEEL_BYTES = 600 * 1024 * 1024
HEX64 = re.compile(r"^[0-9a-f]{64}$")
UNVERIFIED = "HOTFIX_DEP_UNVERIFIED"
BRIDGE_UNVERIFIED = "HOTFIX_BRIDGE_UNVERIFIED"


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


# ---------------------------------------------------------------------- options (tests inject fakes)
@dataclass
class S2Options:
    bridge_exe: Path | None = None            # --bridge-exe
    python: Path | None = None                # --python (default: <install root>/python/python.exe)
    py_env: dict | None = None                # marker/tag environment of the installed Python (default: ask it)
    allow_insecure_urls: bool = False         # tests only (http://127.0.0.1, file://); the CLI never sets it
    opener: Callable | None = None            # url -> binary file-like (tests); default urllib with an https-only redirect rule
    runner: Callable | None = None            # argv list -> (rc, output) for pip (tests); default subprocess
    extras: tuple[str, ...] = EXTRAS


# ---------------------------------------------------------------------- environment markers (PEP 508 subset)
_TOK = re.compile(r"\s*(\(|\)|===|==|!=|<=|>=|~=|<|>|'[^']*'|\"[^\"]*\"|[A-Za-z_][A-Za-z0-9_.]*)")


def _ver(s: str) -> tuple:
    out = []
    for p in re.split(r"[.+\-]", s):
        out.append(int(p) if p.isdigit() else 0)
    return tuple(out)


def eval_marker(expr: str | None, env: dict) -> bool:
    """True when the marker holds for `env`. An unknown variable or a syntax this subset does not know evaluates to True
    (conservative: the package is considered, the wheel selection then decides)."""
    if not expr:
        return True
    toks, pos = [], 0
    s = expr.strip()
    while pos < len(s):
        m = _TOK.match(s, pos)
        if not m:
            return True
        toks.append(m.group(1))
        pos = m.end()
    i = [0]

    def peek():
        return toks[i[0]] if i[0] < len(toks) else None

    def take():
        i[0] += 1
        return toks[i[0] - 1]

    def operand(tok):
        if tok[0] in "'\"":
            return tok[1:-1], True
        return env.get(tok), False

    def atom():
        t = peek()
        if t == "(":
            take()
            v = or_()
            if peek() == ")":
                take()
            return v
        a = take()
        op = take()
        if op == "not":
            take()
            op = "not in"
        b = take()
        (av, a_lit), (bv, b_lit) = operand(a), operand(b)
        if av is None or bv is None:
            return True
        if (a in ("python_full_version", "python_version", "implementation_version") and b_lit) or \
           (b in ("python_full_version", "python_version", "implementation_version") and a_lit):
            av, bv = _ver(str(av)), _ver(str(bv))
        try:
            return {"==": av == bv, "===": av == bv, "!=": av != bv, "<": av < bv, "<=": av <= bv, ">": av > bv,
                    ">=": av >= bv, "~=": av >= bv, "in": str(av) in str(bv), "not in": str(av) not in str(bv)}[op]
        except (KeyError, TypeError):
            return True

    def and_():
        v = atom()
        while peek() == "and":
            take()
            v = atom() and v
        return v

    def or_():
        v = and_()
        while peek() == "or":
            take()
            v = and_() or v
        return v

    try:
        return bool(or_())
    except IndexError:
        return True


# ---------------------------------------------------------------------- lock parsing
@dataclass
class Pkg:
    name: str
    version: str
    source: dict
    wheels: list[dict]
    has_sdist: bool
    deps: list[tuple[str, tuple[str, ...], str | None]]                       # (name, extras, marker)
    optional: dict[str, list[tuple[str, tuple[str, ...], str | None]]]


def _dep_list(items) -> list:
    return [(norm(str(d.get("name", ""))), tuple(d.get("extra") or ()), d.get("marker")) for d in (items or []) if isinstance(d, dict)]


def parse_lock(text: str) -> dict[str, Pkg]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise Refused(UNVERIFIED, f"the lock file does not parse: {e}", "use the full installer") from e
    out: dict[str, Pkg] = {}
    for p in data.get("package", []) or []:
        name = norm(str(p.get("name", "")))
        if not name:
            continue
        out[name] = Pkg(name, str(p.get("version", "")), dict(p.get("source") or {}), list(p.get("wheels") or []),
                        bool(p.get("sdist")), _dep_list(p.get("dependencies")),
                        {k: _dep_list(v) for k, v in (p.get("optional-dependencies") or {}).items()})
    return out


def root_name(lock: dict[str, Pkg]) -> str | None:
    for n, p in lock.items():
        if "editable" in p.source or "virtual" in p.source:
            return n
    return None


def reachable(lock: dict[str, Pkg], env: dict, extras: tuple[str, ...]) -> set[str]:
    """Names the installed environment can contain: the project's dependencies and the installer's extras, transitively,
    markers evaluated for the installed Python. The dev group is never included."""
    root = root_name(lock)
    if root is None:
        raise Refused(UNVERIFIED, "the lock has no project package; cannot tell which packages are installed", "use the full installer")
    visited: set[tuple[str, tuple[str, ...]]] = set()
    names: set[str] = set()
    stack: list[tuple[str, tuple[str, ...]]] = [(root, tuple(extras))]
    while stack:
        n, ex = stack.pop()
        key = (n, tuple(sorted(ex)))
        if key in visited:
            continue
        visited.add(key)
        names.add(n)
        p = lock.get(n)
        if p is None:
            continue
        edges = list(p.deps) + [e for x in ex for e in p.optional.get(x, [])]
        for dn, dex, marker in edges:
            if eval_marker(marker, env):
                stack.append((dn, tuple(dex)))
    names.discard(root)
    return names


# ---------------------------------------------------------------------- wheel selection
def _parse_wheel_name(fn: str) -> tuple[str, str, str, str, str] | None:
    if not fn.endswith(".whl"):
        return None
    parts = fn[:-4].split("-")
    if len(parts) == 5:
        return parts[0], parts[1], parts[2], parts[3], parts[4]
    if len(parts) == 6:
        return parts[0], parts[1], parts[3], parts[4], parts[5]
    return None


def wheel_score(fn: str, env: dict) -> int | None:
    """Compatibility score of a wheel file name with the installed Python (None = not installable here)."""
    parsed = _parse_wheel_name(fn)
    if not parsed:
        return None
    _, _, py, abi, plat = parsed
    minor = int(str(env.get("python_version", "3.12")).split(".")[1])
    best_py = 0
    for pt in py.split("."):
        for at in abi.split("."):
            if at == "none" and (pt == "py3" or (pt.startswith("py3") and pt[3:].isdigit() and int(pt[3:]) <= minor)):
                best_py = max(best_py, 1)
            elif at == "none" and pt == f"cp3{minor}":
                best_py = max(best_py, 3)
            elif at == "abi3" and pt.startswith("cp3") and pt[3:].isdigit() and int(pt[3:]) <= minor:
                best_py = max(best_py, 2)
            elif at == f"cp3{minor}" and pt == at:
                best_py = max(best_py, 3)
    if not best_py:
        return None
    sysplat, mach = env.get("sys_platform"), str(env.get("platform_machine", "")).lower()
    best_pl = None
    for tag in plat.split("."):
        if tag == "any":
            best_pl = max(best_pl or 0, 0)
        elif sysplat == "win32":
            want = {"amd64": "win_amd64", "x86_64": "win_amd64", "arm64": "win_arm64"}.get(mach, "win32")
            if tag == want:
                best_pl = 1
        elif sysplat == "linux":
            arch = {"amd64": "x86_64"}.get(mach, mach)
            if tag.startswith("manylinux") and tag.endswith("_" + arch):
                best_pl = 1
        elif sysplat == "darwin":
            if tag.startswith("macosx_") and (tag.endswith("_" + mach) or tag.endswith("_universal2")):
                best_pl = 1
    if best_pl is None:
        return None
    return best_py * 10 + best_pl


@dataclass
class DepItem:
    name: str
    version: str
    old_version: str | None
    filename: str
    url: str
    sha256: str
    size: int | None


def select_wheel(pkg: Pkg, env: dict, allow_insecure: bool) -> DepItem:
    reg = str(pkg.source.get("registry") or "")
    if not reg:
        raise Refused(UNVERIFIED, f"{pkg.name} {pkg.version} does not come from a package registry "
                      f"({', '.join(pkg.source) or 'no source'}): only hash-pinned registry wheels are installed")
    if not SAFE_NAME.match(pkg.name) or not SAFE_VERSION.match(pkg.version):
        raise Refused(UNVERIFIED, f"{pkg.name!r} {pkg.version!r}: not a plain package name/version")
    reg_u = urllib.parse.urlparse(reg)
    if reg_u.scheme != "https" or (reg_u.hostname or "") not in REGISTRY_HOSTS:
        raise Refused(UNVERIFIED, f"{pkg.name} {pkg.version}: the registry {reg_u.hostname or reg[:40]} is not on the allow-list "
                      f"({', '.join(sorted(REGISTRY_HOSTS))})")
    allowed_hosts = set(WHEEL_HOSTS)
    scored = []
    for w in pkg.wheels:
        url = str(w.get("url") or "")
        fn = urllib.parse.unquote(url.rsplit("/", 1)[-1])
        sc = wheel_score(fn, env)
        if sc is not None:
            scored.append((sc, fn, w))
    if not scored:
        why = "only a source distribution (sdist) exists" if pkg.has_sdist and not pkg.wheels else "no wheel fits this Python/platform"
        raise Refused(UNVERIFIED, f"{pkg.name} {pkg.version}: {why}; building from source is never done", "use the full installer")
    scored.sort(key=lambda t: (-t[0], t[1]))
    _, fn, w = scored[0]
    h = str(w.get("hash") or "")
    if not h.startswith("sha256:") or not HEX64.match(h[7:]):
        raise Refused(UNVERIFIED, f"{pkg.name} {pkg.version}: the wheel {fn} has no sha256 hash in the lock")
    url = str(w["url"])
    u = urllib.parse.urlparse(url)
    if not (u.scheme == "https" or (allow_insecure and u.scheme == "http" and (u.hostname or "") in TEST_HOSTS)):
        raise Refused(UNVERIFIED, f"{pkg.name} {pkg.version}: the wheel URL is not https ({u.scheme or 'none'})")
    if not SAFE_WHEEL_FILE.match(fn):
        raise Refused(UNVERIFIED, f"{pkg.name} {pkg.version}: unsafe wheel file name {fn[:80]!r}")
    if u.scheme == "https" and (u.hostname or "") not in allowed_hosts:
        raise Refused(UNVERIFIED, f"{pkg.name} {pkg.version}: the wheel host {u.hostname} is not the lock's index host "
                      f"({', '.join(sorted(allowed_hosts))})")
    parsed = _parse_wheel_name(fn)
    if not parsed or (norm(parsed[0]) != pkg.name or parsed[1].replace("_", "-") != pkg.version.replace("_", "-")):
        raise Refused(UNVERIFIED, f"{pkg.name} {pkg.version}: the wheel file name {fn} names a different package or version")
    size = w.get("size")
    return DepItem(pkg.name, pkg.version, None, fn, url, h[7:], int(size) if isinstance(size, int) else None)


# ---------------------------------------------------------------------- the plan
@dataclass
class DepPlan:
    items: list[DepItem] = field(default_factory=list)
    already_installed: list[str] = field(default_factory=list)
    removed_in_target: list[str] = field(default_factory=list)       # dropped from the lock: left installed, reported

    def requirements(self) -> str:
        return "".join(f"{i.name}=={i.version} --hash=sha256:{i.sha256}\n" for i in self.items)

    def describe(self) -> list[dict]:
        return [{"name": i.name, "version": i.version, "old_version": i.old_version, "sha256": i.sha256[:12],
                 "size": i.size, "file": i.filename, "host": urllib.parse.urlparse(i.url).hostname or "file"} for i in self.items]


def installed_versions(sp: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if sp.is_dir():
        for d in sp.glob("*.dist-info"):
            stem = d.name[:-len(".dist-info")]
            if "-" in stem:
                n, v = stem.rsplit("-", 1)
                out[norm(n)] = v
    return out


def build_dep_plan(base_lock_text: str | None, target_lock_text: str, env: dict, sp: Path, opts: S2Options) -> DepPlan:
    target = parse_lock(target_lock_text)
    base = parse_lock(base_lock_text) if base_lock_text else {}
    names = reachable(target, env, opts.extras)
    have = installed_versions(sp)
    plan = DepPlan()
    for n in sorted(names):
        p = target[n]
        if base.get(n) is not None and base[n].version == p.version:
            continue                                                   # not part of the delta
        if have.get(n) == p.version.replace("-", "_") or have.get(n) == p.version:
            plan.already_installed.append(f"{n}=={p.version}")
            continue
        item = select_wheel(p, env, opts.allow_insecure_urls)
        item.old_version = have.get(n)
        plan.items.append(item)
    plan.removed_in_target = sorted(n for n in base if n not in target)
    return plan


# ---------------------------------------------------------------------- download + verify (before anything is touched)
class _HttpsOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        u = urllib.parse.urlparse(newurl)
        if u.scheme != "https" or (u.hostname or "") not in WHEEL_HOSTS:
            raise Refused(UNVERIFIED, f"a download was redirected to a non-allow-listed address: {newurl[:80]}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _default_open(url: str):
    op = urllib.request.build_opener(_HttpsOnly())
    return op.open(url, timeout=60)


def download_wheels(plan: DepPlan, dest: Path, opts: S2Options) -> dict[str, Path]:
    """Download every wheel of the plan into `dest`, checking size and sha256 as the bytes arrive. Any mismatch deletes
    the file and raises HOTFIX_DEP_UNVERIFIED."""
    dest.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    opener = opts.opener or _default_open
    for it in plan.items:
        target = dest / it.filename
        h = hashlib.sha256()
        total = 0
        cap = it.size if it.size is not None else MAX_WHEEL_BYTES
        try:
            with opener(it.url) as r, open(target, "wb") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > cap:
                        raise Refused(UNVERIFIED, f"{it.name} {it.version}: the download is larger than the locked size ({cap} bytes)")
                    h.update(chunk)
                    f.write(chunk)
        except Refused:
            target.unlink(missing_ok=True)
            raise
        except Exception as e:                                         # network/file errors: fail closed, nothing touched
            target.unlink(missing_ok=True)
            raise HotfixError("HOTFIX_DEP_DOWNLOAD", f"{it.name} {it.version}: download failed ({type(e).__name__}: {str(e)[:120]})") from e
        if it.size is not None and total != it.size:
            target.unlink(missing_ok=True)
            raise Refused(UNVERIFIED, f"{it.name} {it.version}: size {total} differs from the locked size {it.size}")
        if h.hexdigest() != it.sha256:
            target.unlink(missing_ok=True)
            raise Refused(UNVERIFIED, f"{it.name} {it.version}: sha256 {h.hexdigest()[:12]} differs from the locked {it.sha256[:12]}")
        out[it.filename] = target
    return out


# ---------------------------------------------------------------------- wheel inspection
def _safe_member(name: str) -> bool:
    return bool(name) and not name.startswith(("/", "\\")) and ":" not in name and ".." not in name.replace("\\", "/").split("/")


def wheel_paths(whl: Path) -> tuple[list[str], str]:
    """Relative paths (to site-packages) the wheel installs, and its dist-info dir name. Refuses anything that would
    write outside site-packages or create scripts."""
    with zipfile.ZipFile(whl) as z:
        names = z.namelist()
        records = [n for n in names if n.count("/") == 1 and n.endswith(".dist-info/RECORD")]
        if len(records) != 1:
            raise Refused(UNVERIFIED, f"{whl.name}: not a normal wheel (no single RECORD)")
        dist = records[0].split("/")[0]
        if sum(i.file_size for i in z.infolist()) > MAX_UNPACKED_BYTES or len(names) != len(set(names)):
            raise Refused(UNVERIFIED, f"{whl.name}: unpacked size too large or duplicate member names")
        ep = f"{dist}/entry_points.txt"
        if ep in names:
            txt = z.read(ep).decode("utf-8", "replace")
            if re.search(r"^\[(console_scripts|gui_scripts)\]", txt, re.M):
                raise Refused(UNVERIFIED, f"{whl.name}: the wheel creates command scripts (outside site-packages)")
        out = []
        for n in names:
            if n.endswith("/"):
                continue
            if not _safe_member(n):
                raise Refused(UNVERIFIED, f"{whl.name}: unsafe path {n!r}")
            top = n.split("/")[0]
            if top.endswith(".data"):
                parts = n.split("/", 2)
                if len(parts) == 3 and parts[1] in ("purelib", "platlib"):
                    out.append(parts[2])
                    continue
                raise Refused(UNVERIFIED, f"{whl.name}: installs {parts[1] if len(parts) > 1 else '?'} data outside site-packages")
            out.append(n)
    return sorted(set(out)), dist


def _checked_members(whl: Path):
    """Yield (member name, destination path relative to site-packages, bytes) for every file of the wheel after the
    path rules and the RECORD sha256 of each file were checked."""
    paths, dist = wheel_paths(whl)
    with zipfile.ZipFile(whl) as z:
        rec = {}
        for row in csv.reader(io.StringIO(z.read(f"{dist}/RECORD").decode("utf-8"))):
            if row and len(row) >= 2:
                rec[row[0]] = row[1]
        for info in z.infolist():
            n = info.filename
            if n.endswith("/"):
                continue
            rel = n.split("/", 2)[2] if n.split("/")[0].endswith(".data") else n
            data = z.read(info)
            if n != f"{dist}/RECORD" and not n.endswith((".jws", ".p7s")):
                want = rec.get(n, "")
                if not want.startswith("sha256="):
                    raise Refused(UNVERIFIED, f"{whl.name}: {n} has no sha256 in the wheel RECORD")
                got = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
                if got != want[7:]:
                    raise Refused(UNVERIFIED, f"{whl.name}: {n} does not match the wheel RECORD")
            yield n, rel, data


def verify_wheel(whl: Path, sp: Path | None = None) -> None:
    """Everything checkable without writing: member paths, no scripts/data outside site-packages, RECORD hashes, and (with
    `sp`) no NEW interpreter start-up hook (*.pth at the top of site-packages, sitecustomize/usercustomize) that is not
    already installed: such a file runs code in every Python process, not only when the package is imported."""
    paths = wheel_paths(whl)[0]
    if sp is not None:
        for rel in paths:
            if "/" not in rel and (rel.lower().endswith(".pth") or rel.lower() in STARTUP_HOOKS) and not (sp / rel).is_file():
                raise Refused(UNVERIFIED, f"{whl.name}: adds the start-up hook {rel} (runs in every Python process)")
    for _ in _checked_members(whl):
        pass


def extract_wheel(whl: Path, sp: Path) -> list[str]:
    """Built-in installer (used when pip cannot be run): each file is written atomically and was checked against the
    wheel's RECORD hash first."""
    written = []
    dist = wheel_paths(whl)[1]
    verify_wheel(whl)                                          # all checks before the first write (streamed, not held in memory)
    for _, rel, data in _checked_members(whl):                 # re-checked member by member while writing
        dst = sp / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".hotfix-tmp")
        tmp.write_bytes(data)
        os.replace(tmp, dst)
        written.append(rel)
    (sp / dist / "INSTALLER").write_text("jarves-hotfix\n", encoding="utf-8")
    return written


# ---------------------------------------------------------------------- transaction with backup
def _record_paths(dist_dir: Path, sp: Path) -> list[str]:
    rec = dist_dir / "RECORD"
    out = []
    if rec.is_file():
        for row in csv.reader(io.StringIO(rec.read_text(encoding="utf-8", errors="replace"))):
            if row and row[0] and _safe_member(row[0]):
                out.append(row[0])
    out.append(f"{dist_dir.name}/RECORD")
    return out


def _old_dist(sp: Path, name: str, version: str | None) -> Path | None:
    if not version:
        return None
    for d in sp.glob("*.dist-info"):
        stem = d.name[:-len(".dist-info")]
        if "-" in stem and norm(stem.rsplit("-", 1)[0]) == name and stem.rsplit("-", 1)[1] == version:
            return d
    return None


def begin_dep_backup(plan: DepPlan, wheels: dict[str, Path], sp: Path, backup_dir: Path) -> list[dict]:
    """Copy every file the install can touch (old package files per its RECORD, and any file the new wheel would
    overwrite) into backup_dir. Returns the manifest entries; nothing in site-packages changed yet."""
    entries = []
    for it in plan.items:
        new_paths, new_dist = wheel_paths(wheels[it.filename])
        old = _old_dist(sp, it.name, it.old_version)
        old_paths = _record_paths(old, sp) if old else []
        touched = sorted(set(old_paths) | {p for p in new_paths if (sp / p).is_file()})
        saved = []
        for rel in touched:
            src = sp / rel
            if src.is_file() and not src.is_symlink():
                dst = backup_dir / norm(it.name) / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
                saved.append({"path": rel, "sha256": sha256(dst)})
        entries.append({"name": it.name, "old_version": it.old_version, "new_version": it.version, "wheel_sha256": it.sha256,
                        "new_paths": new_paths, "new_dist": new_dist, "old_paths": old_paths, "backed_up": saved,
                        "old_dist": old.name if old else None})
    return entries


def _prune_empty(sp: Path, rel: str) -> None:
    p = (sp / rel).parent
    while p != sp and sp in p.parents:
        try:
            p.rmdir()
        except OSError:
            break
        p = p.parent


def remove_dep_files(sp: Path, entry: dict, *, new: bool) -> None:
    paths = entry["new_paths"] if new else entry["old_paths"]
    for rel in paths:
        f = sp / rel
        if f.is_file() or f.is_symlink():
            f.unlink()
            _prune_empty(sp, rel)
    dist = entry["new_dist"] if new else entry["old_dist"]
    if dist and (sp / dist).is_dir():
        shutil.rmtree(sp / dist, ignore_errors=True)               # INSTALLER/REQUESTED and anything pip added


def install_deps(plan: DepPlan, wheels: dict[str, Path], entries: list[dict], sp: Path, py: Path, opts: S2Options,
                 tmp: Path, log: Callable[[str], None] = print) -> str:
    """Remove the old versions (already backed up), install the new wheels. Returns the method used."""
    for it in plan.items:                                      # the SAME verified file, re-hashed right before use
        if sha256(wheels[it.filename]) != it.sha256:
            raise HotfixError(UNVERIFIED, f"{it.filename} changed after it was verified")
    for e in entries:                                          # a .pyd/.dll loaded by another Python process cannot be removed:
        for rel in e["old_paths"] + [b["path"] for b in e["backed_up"]]:   # stop here, before anything is removed
            f = sp / rel
            if f.suffix.lower() in (".pyd", ".dll") and f.is_file():
                try:
                    open(f, "r+b").close()
                except OSError as ex:
                    raise HotfixError("HOTFIX_DEP_LOCKED", f"{rel} is in use by a running program ({ex.__class__.__name__}); "
                                      "close JARVES (tray, dashboard) and retry") from ex
    for e in entries:
        if e["old_version"]:
            remove_dep_files(sp, e, new=False)
    run = opts.runner or _run
    method = "builtin"
    pip_ok = False
    if opts.runner or (py and Path(py).is_file()):
        rc, _ = run([str(py), "-I", "-m", "pip", "--version"])
        pip_ok = rc == 0
    if pip_ok:
        method = "pip"
        req = tmp / "requirements.txt"
        req.write_text(plan.requirements(), encoding="utf-8")
        wd = next(iter(wheels.values())).parent
        cmd = [str(py), "-I", "-m", "pip", "--isolated", "install", "--require-hashes", "--no-deps", "--no-index", "--only-binary", ":all:",
               "--find-links", str(wd), "--disable-pip-version-check", "--no-input", "-r", str(req)]
        rc, out = run(cmd)
        if rc != 0:
            raise HotfixError("HOTFIX_DEP_INSTALL", f"pip install failed (exit {rc}): {out[-300:]}")
    else:
        for it in plan.items:
            extract_wheel(wheels[it.filename], sp)
    for e in entries:
        missing = [p for p in e["new_paths"][:50] if not (sp / p).exists()]
        if missing:
            raise HotfixError("HOTFIX_DEP_INSTALL", f"{e['name']}: expected files are missing after the install ({missing[0]})")
    return method


def _run(cmd: list[str]) -> tuple[int, str]:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("PIP_", "PYTHON"))}
    env["PIP_CONFIG_FILE"] = os.devnull
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=env,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def revert_deps(entries: list[dict], sp: Path, backup_dir: Path) -> None:
    """Remove what the install added, put the saved files back (verified first). Newly added packages disappear."""
    for e in entries:
        for item in e["backed_up"]:
            saved = backup_dir / norm(e["name"]) / item["path"]
            if not saved.is_file() or sha256(saved) != item["sha256"]:
                raise HotfixError("HOTFIX_ORIGINAL_BAD", f"saved original of {item['path']} ({e['name']}) is missing or changed")
    for e in reversed(entries):
        remove_dep_files(sp, e, new=True)
        for item in e["backed_up"]:
            dst = sp / item["path"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(dst.name + ".hotfix-tmp")
            shutil.copyfile(backup_dir / norm(e["name"]) / item["path"], tmp)
            os.replace(tmp, dst)


# ---------------------------------------------------------------------- bridge
@dataclass
class BridgeAction:
    src: Path
    dest: Path
    sha256: str
    version: str
    old_sha256: str | None


def read_pin(text: str) -> dict:
    try:
        pin = json.loads(text)
    except ValueError as e:
        raise Refused(BRIDGE_UNVERIFIED, f"{PIN_PATH} does not parse: {e}") from e
    sha = str(pin.get("sha256", "")).lower() if isinstance(pin, dict) else ""
    path = str(pin.get("path", "")) if isinstance(pin, dict) else ""
    if not HEX64.match(sha):
        raise Refused(BRIDGE_UNVERIFIED, f"{PIN_PATH} has no valid sha256")
    if not BRIDGE_DEST_OK.match(path):
        raise Refused(BRIDGE_UNVERIFIED, f"{PIN_PATH}: the path {path!r} is not a file directly inside bridge/")
    return {"sha256": sha, "path": path, "version": str(pin.get("version", "?"))}


def plan_bridge(pin_text: str | None, install_root: Path, opts: S2Options) -> BridgeAction | None:
    """None = the installed exe already is the pinned one (nothing to deliver). Otherwise a verified action."""
    if pin_text is None:
        raise Refused(BRIDGE_UNVERIFIED, f"the bridge changed but {PIN_PATH} cannot be read from the target commit")
    pin = read_pin(pin_text)
    dest = install_root / pin["path"]
    old = sha256(dest) if dest.is_file() else None
    if old == pin["sha256"]:
        return None
    exe = opts.bridge_exe
    if exe is None:
        raise Refused(BRIDGE_UNVERIFIED, f"the pinned bridge (sha256 {pin['sha256'][:12]}, version {pin['version']}) differs from the "
                      f"installed one and no exe was given", "build or download the pinned exe and pass --bridge-exe PATH")
    exe = Path(exe)
    if not exe.is_file() or exe.is_symlink():
        raise Refused(BRIDGE_UNVERIFIED, f"--bridge-exe {exe} is not a regular file")
    got = sha256(exe)
    if got != pin["sha256"]:
        raise Refused(BRIDGE_UNVERIFIED, f"--bridge-exe sha256 {got[:12]} does not equal the pin {pin['sha256'][:12]} in the target commit")
    return BridgeAction(exe, dest, pin["sha256"], pin["version"], old)


def _atomic_replace_file(src: Path, dst: Path, tries: int = 20) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".hotfix-tmp")
    shutil.copyfile(src, tmp)
    for n in range(tries):                     # a just-stopped exe stays locked for a moment on Windows
        try:
            os.replace(tmp, dst)
            return
        except PermissionError:
            if n == tries - 1:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.5)


def bridge_backup(act: BridgeAction, backup_dir: Path) -> dict:
    backup_dir.mkdir(parents=True, exist_ok=True)
    entry = {"dest": str(act.dest), "sha256_new": act.sha256, "sha256_original": act.old_sha256, "version": act.version,
             "had_sidecar": False}
    if act.dest.is_file():
        shutil.copyfile(act.dest, backup_dir / "exe")
    side = act.dest.with_name(act.dest.name + ".sha256")
    if side.is_file():
        shutil.copyfile(side, backup_dir / "sidecar")
        entry["had_sidecar"] = True
    return entry


def bridge_install(act: BridgeAction) -> None:
    if sha256(act.src) != act.sha256:                                  # re-check right before the write
        raise HotfixError(BRIDGE_UNVERIFIED, "the bridge exe changed after it was verified")
    _atomic_replace_file(act.src, act.dest)
    if sha256(act.dest) != act.sha256:
        raise HotfixError(BRIDGE_UNVERIFIED, "the installed bridge does not match the pin after the copy")
    side = act.dest.with_name(act.dest.name + ".sha256")
    tmp = side.with_name(side.name + ".hotfix-tmp")
    tmp.write_text(f"{act.sha256}  {act.dest.name}\n", encoding="utf-8")
    os.replace(tmp, side)


def bridge_revert(entry: dict, backup_dir: Path) -> None:
    dest = Path(entry["dest"])
    saved = backup_dir / "exe"
    if entry.get("sha256_original"):
        if not saved.is_file() or sha256(saved) != entry["sha256_original"]:
            raise HotfixError("HOTFIX_ORIGINAL_BAD", "the saved original bridge exe is missing or changed")
        _atomic_replace_file(saved, dest)
    else:
        dest.unlink(missing_ok=True)
    side = dest.with_name(dest.name + ".sha256")
    if entry.get("had_sidecar") and (backup_dir / "sidecar").is_file():
        _atomic_replace_file(backup_dir / "sidecar", side)
    else:
        side.unlink(missing_ok=True)


# ---------------------------------------------------------------------- target environment of the installed Python
def probe_env(py: Path, runner: Callable | None = None) -> dict:
    code = ("import sys,platform,os,json;v=sys.version_info;print(json.dumps({'sys_platform':sys.platform,"
            "'platform_system':platform.system(),'platform_machine':platform.machine(),"
            "'python_full_version':platform.python_version(),'python_version':'%d.%d'%(v[0],v[1]),'os_name':os.name,"
            "'implementation_name':sys.implementation.name,'platform_python_implementation':platform.python_implementation()}))")
    rc, out = (runner or _run)([str(py), "-I", "-c", code])
    try:
        env = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        env = None
    if rc != 0 or not isinstance(env, dict):
        raise Refused(UNVERIFIED, f"cannot ask the installed Python ({py}) for its platform", "pass --python PATH of the installed python.exe")
    return env


def install_root_of(installed_pkg: Path) -> Path:
    """<root>/python/Lib/site-packages/jarves  ->  <root>"""
    return Path(installed_pkg).parents[3] if len(Path(installed_pkg).parents) > 3 else Path(installed_pkg).parent


def default_python(installed_pkg: Path) -> Path:
    root = install_root_of(installed_pkg)
    return root / "python" / ("python.exe" if sys.platform == "win32" else "bin/python3")


def resolve_python(installed_pkg: Path, given: Path | None) -> Path:
    """The interpreter used for pip and the platform probe: the installed one. `--python` may only name a file inside the
    install root (review #465: never an arbitrary interpreter)."""
    if given is None:
        return default_python(installed_pkg)
    root = install_root_of(installed_pkg).resolve()
    p = Path(given).resolve()
    if root not in p.parents:
        raise Refused(UNVERIFIED, f"--python {given} is not inside the install ({root})", "omit --python to use the installed one")
    return p


def make_tmp() -> Path:
    return Path(tempfile.mkdtemp(prefix="jarves-hotfix-deps-"))
