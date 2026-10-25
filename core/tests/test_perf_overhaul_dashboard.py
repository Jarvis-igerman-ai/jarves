"""Perf overhaul 4.5 (#159): `jarvis dashboard` listens and prints its URL before the ~40 page modules are imported
(they load beside the browser start; an API call made meanwhile waits for them), and the always-running polls of
the dashboard pause while its tab is hidden."""
import http.client
import re
import statistics
import threading
import time
from pathlib import Path

from jarves import dashboard
from jarves.config import Config

STATIC = Path(dashboard.__file__).with_name("dashboard_static")


def _get(d, path):
    c = http.client.HTTPConnection("127.0.0.1", d.port, timeout=30)
    c.request("GET", path, headers={"Host": f"127.0.0.1:{d.port}",
                                    "Cookie": f"{dashboard.COOKIE}={d.url.split('t=')[1]}"})
    r = c.getresponse()
    r.read()
    return r.status


def test_deferred_plugins_serve_shell_at_once_and_api_after(tmp_path, monkeypatch):
    real = dashboard.load_plugins
    gate = threading.Event()

    def slow():
        gate.wait(10)                            # stands in for 2-4 s of page-module imports on the owner's PC
        return real()

    monkeypatch.setattr(dashboard, "load_plugins", slow)
    (tmp_path / "workspace").mkdir()
    t = time.perf_counter()
    d = dashboard.Dashboard(tmp_path, Config(), defer_plugins=True).start()
    try:
        assert time.perf_counter() - t < 5
        assert not d.plugins_ready.is_set()
        assert _get(d, "/") == 200                   # the page shell does not wait for the page modules
        res = {}
        th = threading.Thread(target=lambda: res.update(s=_get(d, "/api/overview")))
        th.start()
        time.sleep(0.3)
        assert "s" not in res                        # the API call waits ...
        gate.set()
        th.join(30)
        assert res["s"] == 200 and d.plugins_ready.is_set()   # ... and answers once the routes are there
    finally:
        gate.set()
        d.stop()


def test_failed_plugin_load_does_not_block_requests(tmp_path, monkeypatch):
    def broken():
        raise RuntimeError("boom")
    monkeypatch.setattr(dashboard, "load_plugins", broken)
    (tmp_path / "workspace").mkdir()
    d = dashboard.Dashboard(tmp_path, Config(), defer_plugins=True).start()
    try:
        assert d.plugins_ready.wait(10)
        assert _get(d, "/api/overview") == 200
    finally:
        d.stop()


def test_default_still_loads_plugins_before_returning(tmp_path):
    (tmp_path / "workspace").mkdir()
    d = dashboard.Dashboard(tmp_path, Config())
    try:
        assert d.plugins_ready.is_set() and d.plugins
    finally:
        d.ctx.close()


def test_deferred_init_is_faster(tmp_path, monkeypatch):
    """Median of 3: the constructor no longer pays for the page modules (simulated 0.4 s import time)."""
    real = dashboard.load_plugins

    def slow():
        time.sleep(0.4)
        return real()
    monkeypatch.setattr(dashboard, "load_plugins", slow)
    (tmp_path / "workspace").mkdir()

    def init(defer):
        t = time.perf_counter()
        d = dashboard.Dashboard(tmp_path, Config(), defer_plugins=defer)
        dt = time.perf_counter() - t
        d.plugins_ready.wait(10)
        d.ctx.close()
        d.httpd.server_close()
        return dt
    eager = statistics.median(init(False) for _ in range(3))
    lazy = statistics.median(init(True) for _ in range(3))
    assert lazy < eager - 0.3, (lazy, eager)


def test_global_polls_pause_in_hidden_tabs():
    for name, fn in (("approvals.js", "refresh"), ("app.js", "refreshOverview"), ("home.js", "renderTopbar"),
                     ("guides.js", "refreshBadge"), ("usage.js", "refreshStrip")):
        js = (STATIC / name).read_text(encoding="utf-8")
        assert not re.search(rf"setInterval\({fn},", js), name
        assert re.search(rf"setInterval\(\(\) => \{{ if \(!document\.hidden\) {fn}\(\); \}}", js), name
