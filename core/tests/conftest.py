import asyncio
import json
import os
import socket

if os.name != "nt":
    os.environ.setdefault("PYSTRAY_BACKEND", "dummy")  # headless Linux CI: no X server / appindicator needed

import numpy as np
import pytest
from websockets.asyncio.client import connect

from jarves.config import Config
from jarves.skills.music import FakePlayer
from jarves.voice.audio import SimAudio


# ---------------------------------------------------------------- no skipped tests, ever (owner rule, issue #17)
def pytest_configure(config):
    config.addinivalue_line("markers", "windows_only: runs only on Windows (deselected, not skipped, elsewhere)")
    config.addinivalue_line("markers", "linux_only: runs only on Linux (deselected, not skipped, elsewhere)")


# ---------------------------------------------------------------- CI sharding (no plugin, no reruns of the whole suite)
# The real-browser files run as their own CI job (own retry); the rest is split into N shards by file, balanced by
# test count, the same on every shard because each shard collects the same tests. Deselected, never skipped.
BROWSER_FILES = ("test_browser.py", "test_canary.py", "test_dashboard_a11y.py")


def pytest_addoption(parser):
    parser.addoption("--shard", default="1/1", help="I/N: run only the I-th of N balanced shards of the files")
    parser.addoption("--shard-files", default="", help="comma list of test file names: run exactly these files "
                     "(the CI plan from core/tools/ci_plan.py, balanced by measured durations); overrides --shard")
    parser.addoption("--durations-file", default="", help="JSON {file: seconds}: run the slowest files first")
    parser.addoption("--browser", default="include", choices=["include", "exclude", "only"],
                     help="real-browser test files (CI runs them as their own job): include, exclude or only")


def shard_files(counts: dict, n: int) -> list:
    """Greedy longest-first split of {file: test count} into n shards (deterministic)."""
    shards = [[] for _ in range(n)]
    load = [0] * n
    for name, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        i = load.index(min(load))
        shards[i].append(name)
        load[i] += c
    return shards


def pytest_collection_modifyitems(config, items):
    import sys
    mode = config.getoption("--browser")
    if mode != "include":
        gone = [i for i in items if (os.path.basename(str(i.fspath)) in BROWSER_FILES) == (mode == "exclude")]
        if gone:
            config.hook.pytest_deselected(items=gone)
            items[:] = [i for i in items if i not in gone]
    idx, n = (int(x) for x in config.getoption("--shard").split("/"))
    listed = [x.strip() for x in config.getoption("--shard-files").split(",") if x.strip()]
    names = set(listed)
    if names:
        out = [i for i in items if os.path.basename(str(i.fspath)) not in names]
        if out:
            config.hook.pytest_deselected(items=out)
            items[:] = [i for i in items if os.path.basename(str(i.fspath)) in names]
        pos = {name: k for k, name in enumerate(listed)}      # the plan lists the slowest file first: run it first
        items.sort(key=lambda i: pos[os.path.basename(str(i.fspath))])
    elif n > 1:
        counts = {}
        for i in items:
            counts[str(i.fspath)] = counts.get(str(i.fspath), 0) + 1
        mine = set(shard_files(counts, n)[idx - 1])
        out = [i for i in items if str(i.fspath) not in mine]
        if out:
            config.hook.pytest_deselected(items=out)
            items[:] = [i for i in items if str(i.fspath) in mine]
    dfile = config.getoption("--durations-file")
    if dfile and os.path.isfile(dfile) and not names:         # slowest files first: the long pole starts at once, the tail is short
        try:
            with open(dfile, encoding="utf-8") as fh:
                dur = json.load(fh)
        except ValueError:
            dur = {}
        order = {}
        for i in items:
            order.setdefault(str(i.fspath), len(order))
        items.sort(key=lambda i: (-float(dur.get(os.path.basename(str(i.fspath)), 0)), order[str(i.fspath)]))
    keep, drop = [], []
    for item in items:
        wrong = (item.get_closest_marker("windows_only") and sys.platform != "win32") or \
                (item.get_closest_marker("linux_only") and not sys.platform.startswith("linux"))
        (drop if wrong else keep).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep


def pytest_sessionfinish(session, exitstatus):
    """A skipped (or xfailed) test fails the run: every test must really run and pass."""
    tr = session.config.pluginmanager.get_plugin("terminalreporter")
    if tr is None:
        return
    bad = [r for key in ("skipped", "xfailed") for r in tr.stats.get(key, [])]
    if bad:
        tr.write_line(f"NO-SKIPS RULE: {len(bad)} test(s) skipped/xfailed: " + ", ".join(r.nodeid for r in bad[:10]),
                      red=True)
        session.exitstatus = 1


# CI runners (windows-2025) stall for seconds at a time: every wait for an event is event-driven and generous, and
# fails fast only on a real failure (the awaited thing raising), never on a short clock.
WAIT_S = 45.0


async def until(pred, timeout=WAIT_S, interval=0.02):
    """Poll `pred` (sync or async) until it is truthy; AssertionError("condition not reached") after `timeout`."""
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    while True:
        v = pred()
        if asyncio.iscoroutine(v):
            v = await v
        if v:
            return v
        if loop.time() - t0 > timeout:
            raise AssertionError("condition not reached")
        await asyncio.sleep(interval)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class FakeSTT:
    def __init__(self, text="מה השעה"):
        self.text = text
        self.loaded_name = "fake"

    async def transcribe(self, audio):
        return self.text


class FakeTTS:
    def __init__(self):
        self.spoken = []

    def check(self):
        return True, "cloud", "fake"

    async def synth(self, text):
        self.spoken.append(text)
        return np.zeros(1600, np.float32), 16000


@pytest.fixture(autouse=True)
def _update_keys_are_hermetic(monkeypatch):
    """The shipped public key must not change what update tests expect; tests pass their own keys explicitly."""
    from jarves import updates
    monkeypatch.setattr(updates, "UPDATE_PUBLIC_KEYS", ())


@pytest.fixture(autouse=True)
def _runtime_privacy_does_not_leak(monkeypatch):
    """logs keeps the dashboard's runtime privacy switch in module globals; one test turning it on must not make the
    next test in the same process (pytest-xdist worker) see privacy mode."""
    from jarves import logs
    for name, value in (("_runtime", None), ("_flag", None), ("_flag_mtime", None), ("_flag_checked", 0.0)):
        monkeypatch.setattr(logs, name, value)


@pytest.fixture(autouse=True)
def _pear_probe_is_hermetic(monkeypatch):
    """The music feature probe pings Pear's real port; tests must not depend on whether Pear runs on this PC."""
    from jarves import features as feat
    monkeypatch.setattr(feat, "_tcp_open", lambda url, timeout=0.3: True)


@pytest.fixture(autouse=True)
def _no_real_autostart(monkeypatch):
    """Never touch the real HKCU Run key / systemd user units from a test (runtime/autostart.py)."""
    from jarves.runtime import autostart as au

    class Reg(dict):
        def set(self, name, value):
            self[name] = value

        def delete(self, name):
            return self.pop(name, None) is not None
    monkeypatch.setattr(au, "adapter", lambda: au.WindowsAutostart(Reg(), ["pythonw.exe", "-m", "jarves", "run",
                                                                           "--background"]))


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVES_HOME", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return tmp_path


@pytest.fixture
def cfg():
    c = Config()
    c.link.port = free_port()
    c.voice.greeting = False      # the greeting has its own test
    c.voice.greeting_delay_s = 0  # the 0.5 s beat before the hello has its own test (test_conversation_quality)
    return c


class Face:
    """Minimal stand-in for the Godot face client (same protocol as godot/core_link.gd)."""

    def __init__(self, ws):
        self.ws = ws
        self.msgs: list[dict] = []

    async def send(self, t, payload=None):
        await self.ws.send(json.dumps({"type": t, "payload": payload or {}}))

    async def wait(self, t, pred=lambda p: True, timeout=WAIT_S):
        async def go():
            while True:
                m = json.loads(await self.ws.recv())
                self.msgs.append(m)
                if m["type"] == t and pred(m["payload"]):
                    return m["payload"]
        return await asyncio.wait_for(go(), timeout)

    def states(self):
        return [m["payload"]["state"] for m in self.msgs if m["type"] == "face.set_state"]


async def drain_tools(lv):
    """Live Skill calls run in their own task (they never block the event loop): wait for them."""
    await asyncio.gather(*[t for t in list(lv._tasks) if "tool-" in t.get_name()], return_exceptions=True)


async def open_face(core, token=None):
    ws = await connect(f"ws://127.0.0.1:{core.cfg.link.port}")
    await ws.send(json.dumps({"type": "hello", "payload": {"token": token or core.link.token, "client": "godot",
                                                           "protocol": 1}}))
    f = Face(ws)
    await f.wait("hello.ok")
    return f


@pytest.fixture
async def core(home, cfg):
    from jarves.app import Core
    c = Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(), console_logs=False,
             use_real_audio=False)
    await c.start()
    yield c
    if c.health.core != "stopped":
        await c.stop()


@pytest.fixture(autouse=True)
def _helper_not_elevated(monkeypatch):
    """CI runners (Windows) run as administrator, where the helper correctly refuses everything. Tests assume a normal
    user; the elevation tests set their own monkeypatch to True after this fixture."""
    try:
        from jarves.skills import helper as _hp
    except Exception:
        return
    monkeypatch.setattr(_hp, "running_elevated", lambda: False, raising=False)


@pytest.fixture(autouse=True)
def _no_real_browser(monkeypatch):
    """Tests must NEVER open a real browser/window on the developer's desktop (a connect-wizard test once opened
    t.me/BotFather every time it ran). Every launch is recorded in `opened_urls` and refused."""
    import os
    import webbrowser
    opened: list[str] = []

    def _fake(url, *a, **k):
        opened.append(str(url))
        return True

    for name in ("open", "open_new", "open_new_tab"):
        monkeypatch.setattr(webbrowser, name, _fake, raising=False)
    if hasattr(os, "startfile"):
        monkeypatch.setattr(os, "startfile", lambda path, *a, **k: opened.append(str(path)), raising=False)
    yield opened


@pytest.fixture(autouse=True)
def _close_memory_stores(monkeypatch):
    """Every MemoryStore a test opens is closed at teardown: an open memory.db handle kept the file locked on Windows
    (tmp-dir cleanup failed, rename failed) when a test built a store inline and never closed it."""
    from jarves.memory import MemoryStore
    opened: list = []
    orig = MemoryStore.__init__

    def tracking(self, *a, **kw):
        orig(self, *a, **kw)
        opened.append(self)
    monkeypatch.setattr(MemoryStore, "__init__", tracking)
    yield
    for m in opened:
        try:
            m.close()
        except Exception:
            pass
