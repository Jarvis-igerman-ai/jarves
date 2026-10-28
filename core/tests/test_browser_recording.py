"""Opt-in pilot recording (V7 B5): pause on password/card/code fields, resume when clear, caps, retention and delete. Fake page first;
the real browser test is in test_browser_driver.py."""
from __future__ import annotations

import json

import pytest

from jarves import browser_recording as br


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


class FakeScreencast:
    def __init__(self, page) -> None:
        self.page = page

    def start(self, **kw) -> None:
        assert not self.page.recording, "a second stretch started while one was open"
        self.page.recording, self.page.path = True, kw["path"]
        open(kw["path"], "wb").write(b"\x1aE\xdf\xa3webm")

    def stop(self) -> None:
        if self.page.stop_raises:
            raise RuntimeError("page crashed")
        self.page.recording = False


class FakePage:
    def __init__(self) -> None:
        self.sensitive, self.recording, self.path, self.eval_raises, self.stop_raises = False, False, "", False, False
        self.screencast = FakeScreencast(self)

    def evaluate(self, js):
        if self.eval_raises:
            raise RuntimeError("navigating")
        return self.sensitive


@pytest.fixture()
def rec(tmp_path):
    clock = Clock()
    return br.Recorder(tmp_path, clock=clock), FakePage(), clock


def test_nothing_is_filmed_until_the_first_tick_and_the_format_is_labelled_honestly(rec):
    r, page, _ = rec
    assert r.view()["state"] == "waiting" and not page.recording
    assert "WebM" in r.view()["format"] and "no audio" in r.view()["format"]
    assert (r.dir / "manifest.json").exists()


def test_it_records_pauses_on_a_sensitive_field_and_resumes_in_a_new_file(rec):
    r, page, clock = rec
    r.tick(page, True)
    assert page.recording and r.state == "recording"
    page.sensitive = True
    clock.t += 5
    r.tick(page, True)
    assert not page.recording and r.state == "paused" and r.pauses == 1                 # the closed file ends BEFORE the field is filmed
    clock.t += 3
    r.tick(page, True)
    assert not page.recording                                                           # still sensitive: still paused
    page.sensitive = False
    r.tick(page, True)
    assert page.recording and [s["file"] for s in r.segments] == ["part-001.webm", "part-002.webm"]
    assert r.segments[0]["ended"] is not None and r.segments[0]["bytes"] > 0


def test_a_failed_check_counts_as_sensitive_so_nothing_is_filmed(rec):
    r, page, _ = rec
    page.eval_raises = True
    r.tick(page, True)
    assert not page.recording and r.segments == []
    page.eval_raises = False
    r.tick(page, True)
    assert page.recording
    page.eval_raises = True
    r.tick(page, True)
    assert not page.recording


def test_an_inactive_session_is_not_filmed(rec):
    r, page, _ = rec
    r.tick(page, False)
    assert not page.recording
    r.tick(page, True)
    r.tick(page, False)                                                                  # paused by the owner
    assert not page.recording and r.pauses == 1


def test_a_page_that_dies_while_stopping_still_closes_the_stretch(rec):
    r, page, _ = rec
    r.tick(page, True)
    page.stop_raises = True
    page.sensitive = True
    r.tick(page, True)
    assert r.state == "paused" and r.segments[0]["ended"] is not None


def test_the_segment_and_total_caps(rec):
    r, page, clock = rec
    r.tick(page, True)
    clock.t += br.MAX_SEGMENT_S + 1
    r.tick(page, True)
    assert not page.recording and r.state == "paused"
    r.tick(page, True)                                                                   # a fresh stretch starts after the segment cap
    assert page.recording
    clock.t += br.MAX_RECORDING_S
    r.tick(page, True)
    assert r.state == "finished" and not page.recording
    r.tick(page, True)
    assert not page.recording                                                            # finished stays finished


def test_finish_closes_the_open_stretch_and_the_manifest_matches_the_files(rec):
    r, page, clock = rec
    r.tick(page, True)
    clock.t += 4
    v = r.finish(page)
    assert v["state"] == "finished" and not page.recording and v["recorded_s"] == 4.0
    m = json.loads((r.dir / "manifest.json").read_text(encoding="utf-8"))
    assert m["segments"][0]["file"] == "part-001.webm" and (r.dir / "part-001.webm").exists()
    assert "url" not in json.dumps(m)                                                    # no page address or text in the manifest


def test_a_start_that_fails_turns_the_recording_off_without_breaking_the_pilot(rec):
    r, page, _ = rec
    page.screencast.start = lambda **kw: (_ for _ in ()).throw(RuntimeError("no codec"))
    r.tick(page, True)
    assert r.state == "finished" and r.segments == []


def test_list_delete_and_retention(tmp_path):
    clock = Clock()
    old = br.Recorder(tmp_path, clock=clock)
    old.finish(FakePage())
    clock.t += 8 * 86400
    new = br.Recorder(tmp_path, clock=clock)
    page = FakePage()
    new.tick(page, True)
    new.finish(page)
    ids = [m["id"] for m in br.list_recordings(tmp_path)]
    assert ids == [new.id, old.id] and br.list_recordings(tmp_path)[0]["bytes"] > 0
    assert br.purge(tmp_path, clock=clock) == 1                                          # the 8-day-old one goes
    assert [m["id"] for m in br.list_recordings(tmp_path)] == [new.id]
    assert br.delete(tmp_path, new.id) is True and br.list_recordings(tmp_path) == []
    assert br.delete(tmp_path, new.id) is False


def test_delete_refuses_anything_that_is_not_a_recording_id(tmp_path):
    (tmp_path / "browser").mkdir()
    (tmp_path / "browser" / "keep.txt").write_text("x")
    for bad in ("..", "../..", "rec-../x", "rec-zzzzzzzzzzzz", "", "rec-0123456789abc", "/etc"):
        assert br.delete(tmp_path, bad) is False
    assert (tmp_path / "browser" / "keep.txt").exists()


def test_the_total_size_cap_deletes_the_oldest_first_but_never_the_running_one(tmp_path):
    clock = Clock()
    ids = []
    for _ in range(3):
        r = br.Recorder(tmp_path, clock=clock)
        (r.dir / "part-001.webm").write_bytes(b"x" * 600_000)
        r.finish(FakePage())
        ids.append(r.id)
        clock.t += 60
    assert br.purge(tmp_path, clock=clock, max_mb=1, keep=ids[0]) == 1
    left = {m["id"] for m in br.list_recordings(tmp_path)}
    assert ids[0] in left and ids[2] in left and ids[1] not in left


def test_the_switch_reports_a_missing_ffmpeg_instead_of_failing_later(monkeypatch, tmp_path):
    import sys
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    assert br.ffmpeg_ready() is False
    (tmp_path / "ffmpeg-1013").mkdir()
    assert br.ffmpeg_ready() is False                                   # an empty folder is not an install
    (tmp_path / "ffmpeg-1013" / "ffmpeg-win64.exe").write_bytes(b"x")
    assert br.ffmpeg_ready() is True
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "0")
    assert br.ffmpeg_ready() is True


def test_the_sensitive_check_and_mask_cover_the_obvious_fields():
    js = br.SENSITIVE_JS
    for needle in ("password", "one-time-code", "cc-", "cvv", "otp", "iban", "shadowRoot", "iframe", "\\\\bpin\\\\b"):
        assert needle in js
    assert "type=password" in br.MASK_CSS and "one-time-code" in br.MASK_CSS and "transparent" in br.MASK_CSS
