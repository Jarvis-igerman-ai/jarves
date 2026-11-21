"""Hermetic tests for core/tools/post_install_demos.py: fake scenario runner and capture, frozen clock."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import post_install_demos as pid  # noqa: E402

NOW = 1_800_000_000.0


def tickets(*extra):
    rows = [{"ticket": t, "merged": True, "user_facing": True, "argv": ["status"], "page": "/",
             "caption": f"{t} works"} for t in pid.REQUIRED]
    return rows + list(extra)


def fake_capture(page, img, vid):
    img.write_bytes(b"png")
    vid.write_bytes(b"webm")


def test_records_demo_for_every_required_ticket_and_metrics(tmp_path):
    ran = []
    res = pid.run(tickets({"ticket": "JAR-1", "merged": True, "user_facing": False, "argv": ["status"]},
                          {"ticket": "JAR-2", "merged": False, "user_facing": True, "argv": ["status"]}),
                  tmp_path, run_scenario=lambda argv: ran.append(argv) or (0, "ok"),
                  capture=fake_capture, clock=lambda: NOW)
    assert sorted(res) == sorted(pid.REQUIRED)
    rec = json.loads((tmp_path / "JAR-188" / "demo.json").read_text(encoding="utf-8"))
    assert rec["result"] == "PASS" and rec["caption"] == "JAR-188 works"
    assert (tmp_path / "JAR-188" / rec["image"]).read_bytes() == b"png"
    assert (tmp_path / "JAR-188" / rec["video"]).read_bytes() == b"webm"
    m = pid.metrics(tmp_path, now=NOW + 60)
    assert m == {"demos_24h": 6, "with_image": 6, "with_video": 6, "scenario_pass_pct": 100.0, "missing": []}
    # second run: every ticket already has a demo -> nothing runs again
    assert pid.run(tickets(), tmp_path, run_scenario=lambda a: (1, ""), capture=fake_capture, clock=lambda: NOW) == []


def test_failures_are_recorded_and_pass_pct(tmp_path):
    rows = tickets()
    rows[0]["argv"] = ["doctor"]
    pid.run(rows, tmp_path, run_scenario=lambda argv: (0 if argv == ["status"] else 2, "boom"),
            capture=fake_capture, clock=lambda: NOW)
    assert json.loads((tmp_path / "JAR-188" / "demo.json").read_text(encoding="utf-8"))["result"] == "FAIL"
    m = pid.metrics(tmp_path, now=NOW)
    assert m["scenario_pass_pct"] == round(5 / 6 * 100, 1) and m["missing"] == ["JAR-188"]   # FAIL is not a demo


class PlaywrightLikeError(Exception):
    pass


@pytest.mark.parametrize("exc", [OSError("no browser"), ImportError("no browser"), PlaywrightLikeError("no browser")])
def test_capture_error_is_fail_and_retried_next_pass(tmp_path, exc):
    def broken(page, img, vid):
        img.write_bytes(b"partial")
        raise exc
    rows = tickets()[:2]
    assert pid.run(rows, tmp_path, run_scenario=lambda a: (0, ""), capture=broken,
                   clock=lambda: NOW) == ["JAR-188", "JAR-189"]   # the error did not abort the remaining tickets
    rec = json.loads((tmp_path / "JAR-188" / "demo.json").read_text(encoding="utf-8"))
    assert rec["result"] == "FAIL" and rec["video"] is None and "no browser" in rec["error"]
    assert (tmp_path / "JAR-189" / "demo.json").is_file()   # the error did not abort the remaining tickets
    assert pid.metrics(tmp_path, now=NOW)["with_video"] == 0
    assert pid.run(rows, tmp_path, run_scenario=lambda a: (0, ""), capture=fake_capture,
                   clock=lambda: NOW) == ["JAR-188", "JAR-189"]   # FAIL tickets are retried
    assert pid.metrics(tmp_path, now=NOW)["scenario_pass_pct"] == 100.0


def test_unknown_when_no_demos_and_old_demos_not_counted(tmp_path):
    m = pid.metrics(tmp_path, now=NOW)
    assert m["scenario_pass_pct"] is None and m["demos_24h"] == 0 and m["missing"] == list(pid.REQUIRED)
    pid.run(tickets()[:1], tmp_path, run_scenario=lambda a: (0, ""), capture=fake_capture, clock=lambda: NOW)
    assert pid.metrics(tmp_path, now=NOW + 2 * 86400)["demos_24h"] == 0
    (tmp_path / "JAR-189").mkdir()
    (tmp_path / "JAR-189" / "demo.json").write_text("{bad", encoding="utf-8")   # corrupt record is ignored
    assert pid.metrics(tmp_path, now=NOW)["demos_24h"] == 1


@pytest.mark.parametrize("argv", [["update", "install"], ["doctor", "--fix"], ["features", "enable", "x"], ["--x"],
                                  [], "status", [1]])
def test_scenario_argv_must_be_allowlisted(tmp_path, argv):
    row = dict(tickets()[0], argv=argv)
    pid.run([row], tmp_path, run_scenario=lambda a: pytest.fail("must not run"), capture=fake_capture,
            clock=lambda: NOW)
    rec = json.loads((tmp_path / "JAR-188" / "demo.json").read_text(encoding="utf-8"))
    assert rec["result"] == "FAIL" and "DEMO_SCENARIO_REFUSED" in rec["error"]


@pytest.mark.parametrize("page", ["//evil.example/x", "http://evil.example/", "@evil.example", "x", "/x\n",
                                  "/\\evil.example"])
def test_page_must_be_a_local_path(tmp_path, page):
    row = dict(tickets()[0], page=page)
    pid.run([row], tmp_path, run_scenario=lambda a: (0, ""), capture=lambda *a: pytest.fail("must not capture"),
            clock=lambda: NOW)
    assert "DEMO_PAGE_REFUSED" in json.loads((tmp_path / "JAR-188" / "demo.json").read_text(encoding="utf-8"))["error"]


@pytest.mark.parametrize("url,base", [("http://127.0.0.1:8765", "http://127.0.0.1:8765"),
                                      ("http://localhost:08765/", "http://localhost:8765"),
                                      ("https://[::1]:8765", "https://[::1]:8765"), ("http://localhost", "http://localhost")])
def test_loopback_url_allowed_and_rebuilt(url, base):
    assert pid.check_url(url) == base


@pytest.mark.parametrize("url", ["http://example.com", "http://127.0.0.1.evil.com", "file:///etc/passwd",
                                 "http://10.0.0.5:8765", "ftp://localhost",
                                 "http://evil.example\\@localhost:8765", "http://user@localhost:8765",
                                 "http://user:pw@127.0.0.1:8765", "http://localhost:8765/x", "http://localhost:8765?a=1",
                                 "http://localhost:8765#f", "http://localhost:8765\n", " http://localhost:8765",
                                 "http://localhost:0", "http://localhost:70000", "HTTP://evil.example"])
def test_non_loopback_url_refused(tmp_path, url, capsys):
    with pytest.raises(ValueError, match="DEMO_URL_REFUSED"):
        pid.check_url(url)
    t = tmp_path / "t.json"
    t.write_text("[]", encoding="utf-8")
    assert pid.main(["run", "--tickets", str(t), "--demos", str(tmp_path / "d"), "--dashboard-url", url]) == 2
    assert "DEMO_URL_REFUSED" in capsys.readouterr().err


def test_exit_code_follows_pass_state_not_24h_window(tmp_path, monkeypatch):
    pid.run(tickets(), tmp_path, run_scenario=lambda a: (0, ""), capture=fake_capture, clock=lambda: NOW)
    monkeypatch.setattr(pid.time, "time", lambda: NOW + 3 * 86400)
    assert pid.main(["metrics", "--demos", str(tmp_path)]) == 0


def test_load_tickets_rejects_malformed(tmp_path):
    p = tmp_path / "t.json"
    p.write_text('{"ticket": "x"}', encoding="utf-8")
    with pytest.raises(ValueError, match="DEMO_TICKETS_INVALID"):
        pid.load_tickets(p)
    p.write_text('[1, {"ticket": "JAR-9"}, {"ticket": "JAR-8\\n"}]', encoding="utf-8")
    assert pid.load_tickets(p) == [{"ticket": "JAR-9"}]
    p.write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError, match="DEMO_TICKETS_INVALID"):
        pid.load_tickets(p)



def test_deleted_media_makes_ticket_pending_again(tmp_path):
    pid.run(tickets()[:1], tmp_path, run_scenario=lambda a: (0, ""), capture=fake_capture, clock=lambda: NOW)
    assert pid.has_demo(tmp_path, "JAR-188")
    (tmp_path / "JAR-188" / "demo.webm").unlink()
    assert not pid.has_demo(tmp_path, "JAR-188") and "JAR-188" in pid.metrics(tmp_path, now=NOW)["missing"]
    assert pid.run(tickets()[:1], tmp_path, run_scenario=lambda a: (0, ""), capture=fake_capture,
                   clock=lambda: NOW) == ["JAR-188"]


def test_output_tail_redacts_home_and_user(tmp_path, monkeypatch):
    monkeypatch.setattr(pid.Path, "home", classmethod(lambda cls: Path("/home/alice")))
    monkeypatch.setenv("USER", "alice")
    monkeypatch.delenv("USERNAME", raising=False)
    pid.run(tickets()[:1], tmp_path, run_scenario=lambda a: (0, "log at /home/alice/x.log by alice; malice ok"),
            capture=fake_capture, clock=lambda: NOW)
    tail = json.loads((tmp_path / "JAR-188" / "demo.json").read_text(encoding="utf-8"))["output_tail"]
    assert tail == "log at ~/x.log by <user>; malice ok"


def test_error_field_redacts_home_and_user(tmp_path, monkeypatch):
    monkeypatch.setattr(pid.Path, "home", classmethod(lambda cls: Path(r"C:\Users\alice")))
    monkeypatch.setenv("USERNAME", "alice")

    def broken(page, img, vid):
        raise OSError(r"Executable doesn't exist at c:\users\alice\ms-playwright and C:/Users/alice/x; alice")
    pid.run(tickets()[:1], tmp_path, run_scenario=lambda a: (0, ""), capture=broken, clock=lambda: NOW)
    err = json.loads((tmp_path / "JAR-188" / "demo.json").read_text(encoding="utf-8"))["error"]
    assert "alice" not in err and r"at ~\ms-playwright and ~/x; <user>" in err


@pytest.mark.parametrize("url,ok", [("http://127.0.0.1:8765/api/x?a=1", True), ("http://127.0.0.1:8765", True),
                                    ("http://127.0.0.1:8766/", False), ("https://127.0.0.1:8765/", False),
                                    ("http://localhost:8765/", False), ("http://evil.example/", False),
                                    ("http://u@127.0.0.1:8765/", False), ("data:text/html,x", False),
                                    ("http://127.0.0.1:99999/", False)])
def test_browser_requests_pinned_to_checked_origin(url, ok):
    assert pid.same_origin(url, pid.check_url("http://127.0.0.1:8765")) is ok
    assert pid.same_origin("http://localhost/x", pid.check_url("http://localhost")) is True


def test_jarvis_resolved_absolute_or_scenario_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(pid.shutil, "which", lambda exe: str(Path("jarvis.exe")))   # Windows current-folder hit
    assert pid.resolve_jarvis("jarvis") is None
    monkeypatch.setattr(pid.shutil, "which", lambda exe: str(tmp_path / "jarvis.cmd"))
    assert pid.resolve_jarvis("jarvis") == str(tmp_path / "jarvis.cmd")
    pid.run(tickets()[:1], tmp_path, run_scenario=pid.subprocess_runner(None), capture=fake_capture, clock=lambda: NOW)
    assert "DEMO_JARVIS_NOT_FOUND" in json.loads((tmp_path / "JAR-188" / "demo.json").read_text(encoding="utf-8"))["error"]


def test_required_list_comes_from_flag(tmp_path):
    assert pid.main(["metrics", "--demos", str(tmp_path), "--required", ""]) == 0
    assert pid.main(["metrics", "--demos", str(tmp_path), "--required", "JAR-5"]) == 1
