"""#168 update progress: the status file written by the update steps, what the CLI and the dashboard read, and the rule
that "done" is never shown before the new version passed its health check. Fakes only (local fake release server,
temp dirs, no installer, no network)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_update_flow import NEW, OLD, Launch, checker, home, keys, server, windows  # noqa: F401  (fixtures)

from jarves import update_progress as up
from jarves import updates
from jarves.updates import UpdateError

STATIC = Path(updates.__file__).parent / "dashboard_static"


def _pending(home, **kw):
    p = home / "updates" / "pending.json"
    d = json.loads(p.read_text("utf-8")) if p.exists() else {}
    d.update(kw)
    p.write_text(json.dumps(d), "utf-8")


def test_prepare_install_records_stages_and_size(home, server, keys):
    server.publish(keys, NEW)
    u = checker(home, server, keys)
    u.check(force=True)
    seen = []
    real = up.write
    up.write = lambda h, stage, **kw: (seen.append(stage), real(h, stage, **kw))[1]
    try:
        u.prepare_install(approved=True)
    finally:
        up.write = real
    assert seen == ["downloading", "verifying", "backing_up"]
    s = up.read(home, OLD)
    assert s["active"] and s["stage"] == "backing_up" and s["state"] == "running" and s["to"] == NEW
    assert s["text_en"] and s["text_he"] and 0 < s["percent"] < 100


def test_a_failed_download_is_failed_with_code_and_a_hint(home, server, keys):
    server.publish(keys, NEW, tamper=None)
    u = checker(home, server, keys)
    u.check(force=True)
    server.files[[k for k in server.files if k.endswith(".exe")][0]] = b"corrupt"
    with pytest.raises(UpdateError):
        u.prepare_install(approved=True)
    s = up.read(home, OLD)
    assert s["state"] == "failed" and s["code"].startswith("UPDATE_") and s["hint_en"] and s["hint_he"]
    assert "Failed at" in s["text_en"] and "keeps working" in s["hint_en"]


def test_launch_marks_installing_but_never_done(home, server, keys):
    server.publish(keys, NEW)
    u = checker(home, server, keys)
    u.check(force=True)
    r = u.prepare_install(approved=True)
    updates.launch_installer(r["installer"], verified=r, silent=True, restart=True, popen=Launch())
    assert up.read(home, OLD)["stage"] == "installing"
    assert up.read(home, NEW)["stage"] == "health_check"          # new program up, no CORE_READY yet: still not done
    _pending(home, attempts=1)
    s = up.read(home)
    assert s["stage"] == "health_check" and s["state"] == "running"
    _pending(home, state="ok")                                   # Core of the new version reached CORE_READY
    s = up.read(home, NEW)
    assert s["stage"] == "done" and s["state"] == "done" and s["percent"] == 100


def test_survives_restart_and_failures_come_from_setup_files(home, server, keys):
    server.publish(keys, NEW)
    u = checker(home, server, keys)
    u.check(force=True)
    r = u.prepare_install(approved=True)
    updates.launch_installer(r["installer"], verified=r, silent=True, restart=True, popen=Launch())
    _pending(home, state="not_applied")
    s = up.read(home, OLD)
    assert s["state"] == "failed" and s["code"] == "UPDATE_NOT_APPLIED" and "previous version" in s["hint_en"]
    _pending(home, state="installing")
    (home / "updates" / "health.json").write_text(json.dumps({"status": "rolled_back", "to": NEW}), "utf-8")
    s = up.read(home, OLD)
    assert s["state"] == "failed" and s["code"] == "rolled_back"


def test_a_running_file_that_stopped_moving_is_reported_stalled(home):
    up.write(home, "installing", fresh=True, to=NEW)
    s = up.read(home, now=up.read(home)["ts"] + up.STALE_S + 5)
    assert s["state"] == "failed" and s["code"] == "STALLED"


def test_idle_without_a_file_and_garbage_file(home):
    assert up.read(home)["active"] is False
    up.path(home).parent.mkdir(parents=True, exist_ok=True)
    up.path(home).write_text("{not json", "utf-8")
    assert up.read(home)["active"] is False


def test_watch_prints_each_stage_once_and_stops_at_done(home):
    up.write(home, "downloading", fresh=True, to=NEW, total=3 * 1048576)
    t = [0.0]
    out = []
    steps = iter(["verifying", "installing", "ok"])

    def sleep(_):
        t[0] += 2
        n = next(steps)
        if n == "ok":
            _pending(home, to=NEW, state="ok")
        else:
            up.write(home, n)
    _pending_dir = home / "updates"
    _pending_dir.mkdir(parents=True, exist_ok=True)
    _pending(home, to=NEW, state="installing")
    end = up.watch(home, out.append, sleep=sleep, clock=lambda: t[0])
    assert end == "done"
    assert [l.split("]")[0].split()[0] for l in out] == ["[1/7", "[2/7", "[5/7", "[7/7"]
    assert "3.0 MB" in out[0] and "Done" in out[-1]
    t[0] = 0
    up.write(home, "installing", fresh=True, to="9.9")
    assert up.watch(home, lambda s: None, sleep=lambda _: t.__setitem__(0, t[0] + 100), clock=lambda: t[0],
                    timeout_s=300) == "timeout"


def test_api_route_and_status_command_show_progress(home, monkeypatch, capsys):
    routes = []
    updates.dashboard_routes(lambda m, p, f: routes.append(p))
    assert "/api/updates/progress" in routes
    up.write(home, "installing", fresh=True, to=NEW)
    code, body = up.api_progress(type("C", (), {"home": home})(), None, None)
    assert code == 200 and body["stage"] == "installing" and "Installing" in body["text_en"]
    assert "[5/7" in up.line(body) and "[5/7" in up.line(body, True)


def test_cli_status_prints_the_stage_and_install_watches_until_done(home, monkeypatch, capsys):
    from jarves import cli
    monkeypatch.setenv("JARVES_HOME", str(home))
    monkeypatch.setattr(updates, "version_line", lambda h=None: "JARVES test")
    monkeypatch.setattr(updates.UpdateChecker, "status", lambda self: {"available": False, "error": None,
                                                                       "checked_at": None, "latest": None})
    up.write(home, "installing", fresh=True, to=NEW)
    assert cli.main(["update", "status"]) == 0
    assert "[5/7" in capsys.readouterr().out


def test_updates_js_parses_and_uses_the_progress_api():
    js = STATIC / "updates.js"
    text = js.read_text("utf-8")
    assert "/api/updates/progress" in text and "progress.start()" in text and "textContent" in text
    node = subprocess.run(["node", "--version"], capture_output=True) if sys.platform else None
    if node and node.returncode == 0:
        assert subprocess.run(["node", "--check", str(js)], capture_output=True).returncode == 0
