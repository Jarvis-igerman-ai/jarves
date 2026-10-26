"""#460 hotfix slice 2: verified bridge exe and dependency wheels. Fake install trees, fake hooks and a local fake index
(http.server on 127.0.0.1 serving fixture wheels). No real PyPI, no real install, nothing outside tmp_path."""
from __future__ import annotations

import base64
import hashlib
import http.server
import io
import json
import threading
import zipfile
from pathlib import Path

import pytest

from jarves import hotfix as hf
from jarves import hotfix_slice2 as s2

ENV = {"sys_platform": "win32", "platform_system": "Windows", "platform_machine": "AMD64", "python_full_version": "3.12.15",
       "python_version": "3.12", "os_name": "nt", "implementation_name": "cpython"}
BRIDGE_OLD, BRIDGE_NEW = b"old-bridge-exe", b"new-bridge-exe"


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def make_wheel(name: str, version: str, files: dict[str, str], *, scripts: bool = False, evil_path: str | None = None,
               tag: str = "py3-none-any", bad_record: bool = False) -> tuple[str, bytes]:
    dist = f"{name}-{version}.dist-info"
    entries = dict(files)
    entries[f"{dist}/METADATA"] = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
    entries[f"{dist}/WHEEL"] = "Wheel-Version: 1.0\n"
    if scripts:
        entries[f"{dist}/entry_points.txt"] = "[console_scripts]\nx = y:z\n"
    rows = []
    for n, t in entries.items():
        d = base64.urlsafe_b64encode(hashlib.sha256(t.encode()).digest()).rstrip(b"=").decode()
        rows.append(f"{n},sha256={'AAAA' if bad_record and n == next(iter(files)) else d},{len(t)}")
    rows.append(f"{dist}/RECORD,,")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, t in entries.items():
            z.writestr(n, t)
        if evil_path:
            z.writestr(evil_path, "x")
        z.writestr(f"{dist}/RECORD", "\n".join(rows) + "\n")
    return f"{name}-{version}-{tag}.whl", buf.getvalue()


class Index:
    """A local fake package index: serves fixture wheels, counts requests."""

    def __init__(self, tmp_path):
        self.files: dict[str, bytes] = {}
        self.hits: list[str] = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.hits.append(self.path)
                body = outer.files.get(self.path.lstrip("/"))
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def add(self, fn: str, data: bytes) -> dict:
        self.files[fn] = data
        return {"url": f"{self.base}/{fn}", "hash": f"sha256:{sha(data)}", "size": len(data)}

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def lock_text(pkgs: list[dict], root_deps: list[str], dev_only: list[str] = ()) -> str:
    out = ['version = 1', 'requires-python = ">=3.12"', '',
           '[[package]]', 'name = "jarves"', 'version = "4.5.0"', 'source = { editable = "." }', 'dependencies = [']
    out += [f'    {{ name = "{d}" }},' for d in root_deps] + [']', '']
    for p in pkgs:
        out += ['[[package]]', f'name = "{p["name"]}"', f'version = "{p["version"]}"',
                'source = { registry = "https://pypi.org/simple" }']
        if p.get("deps"):
            out += ['dependencies = ['] + [f'    {{ name = "{d}" }},' for d in p["deps"]] + [']']
        if p.get("sdist"):
            out += ['sdist = { url = "https://files.pythonhosted.org/x.tar.gz", hash = "sha256:' + "a" * 64 + '", size = 1 }']
        if p.get("wheels"):
            out += ['wheels = ['] + [
                '    { url = "%s", %s size = %d },' % (w["url"], f'hash = "{w["hash"]}",' if w.get("hash") else "", w["size"])
                for w in p["wheels"]] + [']']
        out.append('')
    return "\n".join(out)


def write(root: Path, rel: str, text: str | bytes) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text if isinstance(text, bytes) else text.encode())


class Fake:
    def __init__(self, running=False, bridge_running=False, doctor=(0, 0)):
        self.running, self.bridge, self.doctor_rcs, self.calls = running, bridge_running, list(doctor), []

    def hooks(self):
        def stop():
            self.calls.append("stop")
            self.running = False

        def start():
            self.calls.append("start")
            self.running = True

        def bstop():
            self.calls.append("bstop")
            self.bridge = False

        def bstart():
            self.calls.append("bstart")
            self.bridge = True

        return hf.Hooks(lambda: self.running, stop, start, lambda: self.calls.append("backup") or "b.zip",
                        lambda: self.calls.append("doctor") or self.doctor_rcs.pop(0), log=lambda s: None,
                        bridge_running=lambda: self.bridge, stop_bridge=bstop, start_bridge=bstart)


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "JARVES"
    sp = root / "python" / "Lib" / "site-packages"
    inst = sp / "jarves"
    src = tmp_path / "src" / "jarves"
    for r in (inst, src):
        write(r, "__init__.py", "__version__='4.5.0'\n")
        write(r, "a.py", "A=1\n")
    write(inst, hf.BUILD_INFO_NAME, json.dumps({"version": "4.5.0", "commit": "abc123"}))
    write(root, "bridge/whatsapp_bridge.exe", BRIDGE_OLD)
    write(root, "bridge/whatsapp_bridge.exe.sha256", f"{sha(BRIDGE_OLD)}  whatsapp_bridge.exe\n")
    idx = Index(tmp_path)
    yield type("W", (), dict(root=root, sp=sp, inst=inst, src=src, home=tmp_path / "home", idx=idx, tmp=tmp_path))
    idx.close()


def source(w, changes, *, pin=None, base_lock=None, target_lock=None):
    texts = {}
    if pin is not None:
        texts[(s2.PIN_PATH, "target")] = json.dumps(pin)
    if base_lock is not None:
        texts[("core/uv.lock", "base")] = base_lock
    if target_lock is not None:
        texts[("core/uv.lock", "target")] = target_lock
    return hf.Source(w.src, changes, "def456", reader=lambda rel, which: texts.get((rel, which)))


def opts(w, **kw):
    kw.setdefault("py_env", ENV)
    kw.setdefault("python", w.root / "python" / "python.exe")
    kw.setdefault("allow_insecure_urls", True)
    kw.setdefault("runner", lambda cmd: (1, "No module named pip"))
    return s2.S2Options(**kw)


# ---------------------------------------------------------------------- small units
def test_markers_and_wheel_scores():
    assert s2.eval_marker("sys_platform == 'win32'", ENV) and not s2.eval_marker("sys_platform == 'linux'", ENV)
    assert s2.eval_marker("python_full_version < '3.15'", ENV) and not s2.eval_marker("python_full_version >= '3.13' and sys_platform == 'win32'", ENV)
    assert s2.eval_marker("(sys_platform == 'linux' or os_name == 'nt') and python_version >= '3.9'", ENV)
    assert s2.eval_marker("unknown_var == 'x'", ENV) and s2.eval_marker(None, ENV)
    sc = lambda fn: s2.wheel_score(fn, ENV)
    assert sc("a-1-py3-none-any.whl") is not None and sc("a-1-cp312-cp312-win_amd64.whl") > sc("a-1-cp312-abi3-win_amd64.whl") > sc("a-1-py3-none-any.whl")
    for bad in ("a-1-cp311-cp311-win_amd64.whl", "a-1-cp312-cp312-manylinux2014_x86_64.whl", "a-1-cp313-abi3-win_amd64.whl", "a-1.tar.gz"):
        assert sc(bad) is None


def test_reachable_set_ignores_dev_and_unreachable(world):
    t = lock_text([{"name": "a", "version": "1", "deps": ["b"]}, {"name": "b", "version": "1"}, {"name": "pytest", "version": "9"}], ["a"])
    assert s2.reachable(s2.parse_lock(t), ENV, s2.EXTRAS) == {"a", "b"}


# ---------------------------------------------------------------------- bridge
PIN = lambda sha_hex=sha(BRIDGE_NEW): {"path": "bridge/whatsapp_bridge.exe", "sha256": sha_hex, "version": "0.2.0"}


def test_bridge_verified_exe_replaced_with_backup_restart_only_if_running_and_revert(world):
    w = world
    exe = w.tmp / "built.exe"
    exe.write_bytes(BRIDGE_NEW)
    for running in (False, True):
        f = Fake(bridge_running=running)
        m = hf.apply(w.inst, source(w, ["packaging/bridge_pin.json"], pin=PIN()), w.home, f.hooks(), s2opts=opts(w, bridge_exe=exe))
        dest = w.root / "bridge" / "whatsapp_bridge.exe"
        assert dest.read_bytes() == BRIDGE_NEW and (dest.parent / "whatsapp_bridge.exe.sha256").read_text().startswith(sha(BRIDGE_NEW))
        assert ("bstop" in f.calls) == running and ("bstart" in f.calls) == running
        assert f.calls.index("backup") < (f.calls.index("bstop") if running else 99)
        assert m["bridge"]["sha256_original"] == sha(BRIDGE_OLD)
        f2 = Fake(bridge_running=running)
        hf.revert(w.inst, w.home, f2.hooks())
        assert dest.read_bytes() == BRIDGE_OLD and (dest.parent / "whatsapp_bridge.exe.sha256").read_text().startswith(sha(BRIDGE_OLD))
        assert ("bstart" in f2.calls) == running
        import time
        time.sleep(1.1)                                  # next hotfix id (second resolution)


def test_bridge_refusals_leave_everything_untouched(world):
    w = world
    bad = w.tmp / "bad.exe"
    bad.write_bytes(b"something else")
    good = w.tmp / "good.exe"
    good.write_bytes(BRIDGE_NEW)
    cases = [(None, PIN(), "no exe was given"), (bad, PIN(), "does not equal the pin"),
             (good, {"path": "bridge/whatsapp_bridge.exe", "sha256": "zz"}, "valid sha256"),
             (good, {"path": "../evil.exe", "sha256": sha(BRIDGE_NEW)}, "not a file directly inside bridge/"),
             (tmp_missing := w.tmp / "nope.exe", PIN(), "not a regular file")]
    for exe, pin, why in cases:
        with pytest.raises(hf.Refused) as e:
            hf.apply(w.inst, source(w, ["packaging/bridge_pin.json"], pin=pin), w.home, Fake().hooks(), s2opts=opts(w, bridge_exe=exe))
        assert e.value.code == s2.BRIDGE_UNVERIFIED and why in e.value.message
    assert (w.root / "bridge/whatsapp_bridge.exe").read_bytes() == BRIDGE_OLD and not (w.home / "hotfix").exists()
    with pytest.raises(hf.Refused) as e:                          # pin unreadable in the target
        hf.apply(w.inst, source(w, ["bridge/main.go"]), w.home, Fake().hooks(), s2opts=opts(w, bridge_exe=good))
    assert e.value.code == s2.BRIDGE_UNVERIFIED


def test_bridge_code_change_with_unchanged_pin_is_listed_not_delivered(world):
    w = world
    r = hf.apply(w.inst, source(w, ["bridge/whatsapp/main.go"], pin=PIN(sha(BRIDGE_OLD))), w.home, Fake().hooks(), s2opts=opts(w))
    assert r["status"] == "nothing" and r["pending_installer"] == ["bridge/whatsapp/main.go"]


def test_bridge_auto_reverts_when_doctor_gets_worse(world):
    w = world
    exe = w.tmp / "built.exe"
    exe.write_bytes(BRIDGE_NEW)
    f = Fake(bridge_running=True, doctor=(0, 3))
    with pytest.raises(hf.HotfixError) as e:
        hf.apply(w.inst, source(w, ["packaging/bridge_pin.json"], pin=PIN()), w.home, f.hooks(), s2opts=opts(w, bridge_exe=exe))
    assert e.value.code == "HOTFIX_REVERTED"
    assert (w.root / "bridge/whatsapp_bridge.exe").read_bytes() == BRIDGE_OLD and f.bridge is True


def test_bridge_dry_run_shows_plan_and_changes_nothing(world):
    w = world
    exe = w.tmp / "built.exe"
    exe.write_bytes(BRIDGE_NEW)
    r = hf.apply(w.inst, source(w, ["packaging/bridge_pin.json"], pin=PIN()), w.home, Fake().hooks(), dry_run=True, s2opts=opts(w, bridge_exe=exe))
    assert r["status"] == "dry-run" and r["bridge"]["sha256"] == sha(BRIDGE_NEW)[:12]
    assert (w.root / "bridge/whatsapp_bridge.exe").read_bytes() == BRIDGE_OLD and not (w.home / "hotfix").exists()


# ---------------------------------------------------------------------- dependencies
def dep_world(w, *, wheel_kw=None, new_pkg=True, hash_ok=True, size_ok=True):
    """Installed: foo 1.0 (old). Target lock: foo 2.0 (upgrade) and bar 1.0 (new)."""
    fn_old, _ = make_wheel("foo", "1.0", {"foo/__init__.py": "V=1\n", "foo/old_only.py": "O=1\n"})
    for rel, t in (("foo/__init__.py", "V=1\n"), ("foo/old_only.py", "O=1\n")):
        write(w.sp, rel, t)
    write(w.sp, "foo-1.0.dist-info/METADATA", "Name: foo\nVersion: 1.0\n")
    write(w.sp, "foo-1.0.dist-info/RECORD", "foo/__init__.py,,\nfoo/old_only.py,,\nfoo-1.0.dist-info/METADATA,,\nfoo-1.0.dist-info/RECORD,,\n")
    fn2, data2 = make_wheel("foo", "2.0", {"foo/__init__.py": "V=2\n", "foo/new.py": "N=1\n"}, **(wheel_kw or {}))
    fnb, datab = make_wheel("bar", "1.0", {"bar.py": "B=1\n"})
    wf, wb = w.idx.add(fn2, data2), w.idx.add(fnb, datab)
    if not hash_ok:
        wf["hash"] = "sha256:" + "0" * 64
    if not size_ok:
        wf["size"] += 5
    base = lock_text([{"name": "foo", "version": "1.0", "wheels": [wf]}], ["foo"])
    pk = [{"name": "foo", "version": "2.0", "wheels": [wf]}] + ([{"name": "bar", "version": "1.0", "wheels": [wb]}] if new_pkg else [])
    target = lock_text(pk, ["foo"] + (["bar"] if new_pkg else []))
    return base, target


def snapshot(sp: Path) -> dict[str, bytes]:
    return {p.relative_to(sp).as_posix(): p.read_bytes() for p in sorted(sp.rglob("*")) if p.is_file() and "jarves" not in p.parts[len(sp.parts):][:1]}


def test_dependency_dry_run_lists_plan_and_uses_no_network(world):
    w = world
    base, target = dep_world(w)
    before = snapshot(w.sp)
    r = hf.apply(w.inst, source(w, ["core/uv.lock", "core/pyproject.toml"], base_lock=base, target_lock=target), w.home,
                 Fake().hooks(), dry_run=True, s2opts=opts(w))
    assert w.idx.hits == [] and snapshot(w.sp) == before and not (w.home / "hotfix").exists()
    names = {(d["name"], d["version"], d["old_version"]) for d in r["deps"]}
    assert names == {("foo", "2.0", "1.0"), ("bar", "1.0", None)}
    d0 = r["deps"][0]
    assert len(d0["sha256"]) == 12 and isinstance(d0["size"], int) and d0["host"] == "127.0.0.1"
    assert "--hash=sha256:" in r["deps_requirements"] and "foo==2.0" in r["deps_requirements"]


@pytest.mark.parametrize("pip", [False, True])
def test_dependency_apply_upgrade_add_and_revert(world, pip):
    w = world
    base, target = dep_world(w)
    before = snapshot(w.sp)
    cmds = []
    wheels_dir = {}

    def runner(cmd):
        if "--version" in cmd:
            return (0, "pip 25") if pip else (1, "no pip")
        cmds.append(cmd)                                    # fake pip: install what the requirements file names, from --find-links
        fl = Path(cmd[cmd.index("--find-links") + 1])
        for whl in fl.glob("*.whl"):
            s2.extract_wheel(whl, w.sp)
        return 0, "ok"

    f = Fake(running=True)
    m = hf.apply(w.inst, source(w, ["core/uv.lock"], base_lock=base, target_lock=target), w.home, f.hooks(),
                 s2opts=opts(w, runner=runner))
    assert (w.sp / "foo/__init__.py").read_text() == "V=2\n" and (w.sp / "foo/new.py").exists() and not (w.sp / "foo/old_only.py").exists()
    assert (w.sp / "bar.py").exists() and (w.sp / "bar-1.0.dist-info/RECORD").exists() and not (w.sp / "foo-1.0.dist-info").exists()
    assert f.calls[:2] == ["backup", "doctor"] and "stop" in f.calls and f.calls.count("start") == 1 and f.calls[-1] == "doctor"
    assert m["dep_method"] == ("pip" if pip else "builtin")
    if pip:
        c = cmds[0]
        assert "--require-hashes" in c and "--no-deps" in c and "--no-index" in c and "--only-binary" in c and "-r" in c
    info = json.loads((w.inst / hf.BUILD_INFO_NAME).read_text())
    assert sorted(info["hotfix"]["deps"]) == ["bar==1.0", "foo==2.0"]
    hf.revert(w.inst, w.home, Fake(running=True).hooks())
    assert snapshot(w.sp) == before                           # old files back, new package gone, nothing else changed


def test_dependency_failures_are_refused_before_any_write(world):
    w = world
    for kw, why in (({"hash_ok": False}, "sha256"), ({"size_ok": False}, "larger|size"),
                    ({"wheel_kw": {"scripts": True}}, "command scripts"),
                    ({"wheel_kw": {"evil_path": "../../evil.py"}}, "unsafe path"),
                    ({"wheel_kw": {"evil_path": "foo-2.0.data/scripts/x.exe"}}, "outside site-packages")):
        base, target = dep_world(w, **kw)
        before = snapshot(w.sp)
        with pytest.raises(hf.Refused) as e:
            hf.apply(w.inst, source(w, ["core/uv.lock"], base_lock=base, target_lock=target), w.home, Fake().hooks(), s2opts=opts(w))
        import re
        assert e.value.code == s2.UNVERIFIED and re.search(why, e.value.message), e.value.message
        assert snapshot(w.sp) == before and not (w.home / "hotfix").exists()
        w.idx.hits.clear()


def test_lock_level_refusals_missing_hash_sdist_url_and_source(world):
    w = world
    fn, data = make_wheel("foo", "2.0", {"foo/__init__.py": "V=2\n"})
    good = w.idx.add(fn, data)
    nohash = dict(good, hash="")
    cases = {
        "no sha256": lock_text([{"name": "foo", "version": "2.0", "wheels": [nohash]}], ["foo"]),
        "sdist": lock_text([{"name": "foo", "version": "2.0", "sdist": True}], ["foo"]),
        "not https": lock_text([{"name": "foo", "version": "2.0", "wheels": [good]}], ["foo"]),
        "does not come from a package registry": lock_text([{"name": "foo", "version": "2.0", "wheels": [good]}], ["foo"]).replace(
            'source = { registry = "https://pypi.org/simple" }', 'source = { git = "https://example.com/x.git" }'),
    }
    for why, target in cases.items():
        o = opts(w, allow_insecure_urls=(why != "not https"))
        with pytest.raises(hf.Refused) as e:
            hf.apply(w.inst, source(w, ["core/uv.lock"], base_lock="", target_lock=target), w.home, Fake().hooks(), s2opts=o)
        assert e.value.code == s2.UNVERIFIED and why in e.value.message, (why, e.value.message)
    assert w.idx.hits == []


def test_downloaded_https_host_must_be_the_index_host():
    pkg = s2.Pkg("foo", "2.0", {"registry": "https://pypi.org/simple"},
                 [{"url": "https://evil.example.com/foo-2.0-py3-none-any.whl", "hash": "sha256:" + "a" * 64, "size": 5}], False, [], {})
    with pytest.raises(hf.Refused) as e:
        s2.select_wheel(pkg, ENV, False)
    assert "not the lock's index host" in e.value.message
    pkg.wheels[0]["url"] = "https://files.pythonhosted.org/foo-2.0-py3-none-any.whl"
    assert s2.select_wheel(pkg, ENV, False).sha256 == "a" * 64


def test_dependency_install_failure_and_doctor_failure_auto_revert(world):
    w = world
    base, target = dep_world(w)
    before = snapshot(w.sp)

    def boom(cmd):
        if "--version" in cmd:
            return 0, "pip"
        return 1, "ERROR: simulated"                       # pip failed half way: partial files may exist

    with pytest.raises(hf.HotfixError) as e:
        hf.apply(w.inst, source(w, ["core/uv.lock"], base_lock=base, target_lock=target), w.home, Fake().hooks(), s2opts=opts(w, runner=boom))
    assert e.value.code == "HOTFIX_REVERTED" and snapshot(w.sp) == before
    f = Fake(doctor=(0, 2))
    with pytest.raises(hf.HotfixError) as e:
        hf.apply(w.inst, source(w, ["core/uv.lock"], base_lock=base, target_lock=target), w.home, f.hooks(), s2opts=opts(w))
    assert e.value.code == "HOTFIX_REVERTED" and snapshot(w.sp) == before


def test_already_installed_and_unchanged_packages_are_not_downloaded(world):
    w = world
    base, target = dep_world(w, new_pkg=False)
    (w.sp / "foo-1.0.dist-info").rename(w.sp / "foo-2.0.dist-info")          # target version already installed
    r = hf.apply(w.inst, source(w, ["core/uv.lock"], base_lock=base, target_lock=target), w.home, Fake().hooks(), s2opts=opts(w))
    assert r["status"] == "nothing" and r["deps_already_installed"] == ["foo==2.0"] and w.idx.hits == []


def test_new_import_allowed_only_when_locked_wheels_deliver_it(world):
    w = world
    base, target = dep_world(w)
    write(w.src, "a.py", "import bar\nimport foo\n")
    ch = ["core/src/jarves/a.py", "core/uv.lock"]
    r = hf.apply(w.inst, source(w, ch, base_lock=base, target_lock=target), w.home, Fake().hooks(), dry_run=True, s2opts=opts(w, ),
                 site_dirs=[w.sp])
    assert r["imports_to_check_after_download"] and "bar" in r["imports_to_check_after_download"][0]
    hf.apply(w.inst, source(w, ch, base_lock=base, target_lock=target), w.home, Fake().hooks(), s2opts=opts(w), site_dirs=[w.sp])
    assert (w.inst / "a.py").read_text().startswith("import bar") and (w.sp / "bar.py").exists()
    write(w.src, "a.py", "import bar\nimport nothing_delivers_this\n")
    with pytest.raises(hf.Refused) as e:
        hf.apply(w.inst, source(w, ch, base_lock=base, target_lock=target), w.home, Fake().hooks(), s2opts=opts(w), site_dirs=[w.sp])
    assert e.value.code == "HOTFIX_NEW_DEPENDENCY"
    # without any lock change the old rule stands
    with pytest.raises(hf.Refused) as e:
        hf.apply(w.inst, source(w, ["core/src/jarves/a.py"]), w.home, Fake().hooks(), s2opts=opts(w), site_dirs=[w.sp])
    assert e.value.code == "HOTFIX_NEW_DEPENDENCY"


def test_other_dependency_files_and_missing_context_still_refuse(world):
    w = world
    with pytest.raises(hf.Refused) as e:
        hf.apply(w.inst, source(w, ["core/requirements.txt"]), w.home, Fake().hooks(), s2opts=opts(w))
    assert e.value.code == "HOTFIX_NEEDS_SLICE2"
    with pytest.raises(hf.Refused) as e:                       # no context: the plain classify cannot verify anything
        hf.classify(hf.Plan(), ["core/uv.lock"])
    assert e.value.code == "HOTFIX_NEEDS_SLICE2" and "slice 2" in e.value.message


def test_pyproject_only_change_is_listed_not_installed(world):
    w = world
    r = hf.apply(w.inst, source(w, ["core/pyproject.toml"]), w.home, Fake().hooks(), s2opts=opts(w))
    assert r["status"] == "nothing" and r["pending_installer"] == ["core/pyproject.toml"]


def test_builtin_extractor_checks_record_hashes(world, tmp_path):
    w = world
    fn, data = make_wheel("zed", "1.0", {"zed.py": "Z=1\n"}, bad_record=True)
    whl = tmp_path / fn
    whl.write_bytes(data)
    with pytest.raises(hf.Refused) as e:
        s2.extract_wheel(whl, w.sp)
    assert "RECORD" in e.value.message


# ---------------------------------------------------------------------- review #465 hardening
def _git(repo, *a):
    import subprocess
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *a],
                   check=True, capture_output=True)


def test_slice2_needs_a_published_commit_not_a_folder_or_side_branch(tmp_path):
    import subprocess
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    write(repo, "f.txt", "1")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "c1")
    on_main = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    _git(repo, "remote", "add", "jarves", "https://github.com/Jarvis-igerman-ai/jarves.git")
    _git(repo, "update-ref", "refs/remotes/jarves/main", on_main)
    write(repo, "f.txt", "2")
    _git(repo, "commit", "-qam", "c2")                          # a side commit: not on jarves/main
    side = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    hf.require_trusted_target(hf.Source(tmp_path, None, on_main, git_root=repo, base=on_main))
    for src in (hf.Source(tmp_path, None, side, git_root=repo, base=on_main),                     # branch not merged
                hf.Source(tmp_path, ["core/uv.lock"], side, git_root=repo, checkout=repo),        # working tree folder
                hf.Source(tmp_path, None, None)):                                                  # bare folder
        with pytest.raises(hf.Refused) as e:
            hf.require_trusted_target(src)
        assert e.value.code == "HOTFIX_UNTRUSTED_SOURCE"
    _git(repo, "remote", "set-url", "jarves", "https://github.com/someone-else/jarves.git")       # look-alike remote
    with pytest.raises(hf.Refused):
        hf.require_trusted_target(hf.Source(tmp_path, None, on_main, git_root=repo, base=on_main))


def test_registry_and_wheel_hosts_are_an_allow_list_not_the_lock():
    w = [{"url": "https://evil.example.com/foo-2.0-py3-none-any.whl", "hash": "sha256:" + "a" * 64, "size": 5}]
    with pytest.raises(hf.Refused) as e:                       # a lock that names its own index cannot widen the hosts
        s2.select_wheel(s2.Pkg("foo", "2.0", {"registry": "https://evil.example.com/simple"}, w, False, [], {}), ENV, False)
    assert "allow-list" in e.value.message
    with pytest.raises(hf.Refused):                            # http to anything but 127.0.0.1 even in test mode
        s2.select_wheel(s2.Pkg("foo", "2.0", {"registry": "https://pypi.org/simple"},
                               [dict(w[0], url="http://example.com/foo-2.0-py3-none-any.whl")], False, [], {}), ENV, True)
    with pytest.raises(hf.Refused):
        s2._HttpsOnly().redirect_request(None, None, 302, "", {}, "https://evil.example.com/x.whl")


def test_traversal_in_lock_names_and_wheel_file_names_is_refused():
    h = "sha256:" + "a" * 64
    for name, url in (("../../x", "https://files.pythonhosted.org/..%2F..%2Fx-1-py3-none-any.whl"),
                      ("x", "https://files.pythonhosted.org/..%5Cx-1-py3-none-any.whl")):
        with pytest.raises(hf.Refused):
            s2.select_wheel(s2.Pkg(s2.norm(name), "1", {"registry": "https://pypi.org/simple"},
                                   [{"url": url, "hash": h, "size": 1}], False, [], {}), ENV, False)
    with pytest.raises(hf.Refused):                            # option injection through the version into requirements.txt
        s2.select_wheel(s2.Pkg("x", "1\n--index-url https://evil", {"registry": "https://pypi.org/simple"},
                               [{"url": "https://files.pythonhosted.org/x-1-py3-none-any.whl", "hash": h, "size": 1}], False, [], {}), ENV, False)


def test_new_pth_or_sitecustomize_is_refused_unless_already_installed(world, tmp_path):
    w = world
    for hook in ("evil.pth", "sitecustomize.py"):
        fn, data = make_wheel("zed", "1.0", {"zed.py": "Z=1\n", hook: "import os\n"})
        whl = tmp_path / fn
        whl.write_bytes(data)
        with pytest.raises(hf.Refused) as e:
            s2.verify_wheel(whl, w.sp)
        assert "start-up hook" in e.value.message
        write(w.sp, hook, "old\n")                            # an upgrade of a package that already ships it is fine
        s2.verify_wheel(whl, w.sp)


def test_python_flag_only_inside_the_install_and_bridge_pin_only_the_started_file(world, tmp_path):
    w = world
    with pytest.raises(hf.Refused):
        s2.resolve_python(w.inst, tmp_path / "elsewhere" / "python.exe")
    assert s2.resolve_python(w.inst, w.root / "python" / "python.exe") == (w.root / "python" / "python.exe").resolve()
    for bad in ("bridge/..", "bridge/other.exe"):
        with pytest.raises(hf.Refused):
            s2.read_pin(json.dumps({"path": bad, "sha256": "a" * 64}))


def test_installed_core_starts_the_bridge_the_hotfix_replaces(tmp_path, monkeypatch):
    import sys
    from jarves import whatsapp_bridge as wab
    root = tmp_path / "JARVES"
    fake = root / "python" / "Lib" / "site-packages" / "jarves" / "whatsapp_bridge.py"
    exe = root / ("bridge/whatsapp_bridge.exe" if sys.platform == "win32" else "bridge/whatsapp_bridge")
    write(root, exe.relative_to(root).as_posix(), b"x")
    monkeypatch.delenv(wab.BRIDGE_ENV, raising=False)
    monkeypatch.setattr(wab, "__file__", str(fake))
    assert wab.bridge_command(tmp_path / "home") == [str(exe.resolve())]
    assert s2.install_root_of(fake.parent) / s2.read_pin(json.dumps({"path": "bridge/" + exe.name, "sha256": "a" * 64}))["path"] == exe
