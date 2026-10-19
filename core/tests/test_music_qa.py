"""Regressions from the pre-use music QA on real Pear Desktop 3.12 (19 Oct 2026, tools/suite_music.py)."""
from __future__ import annotations

import httpx
import pytest

from jarves.skills import SkillRegistry
from jarves.skills.music import (FakePlayer, MusicSkill, NowPlaying, PearDesktopPlayer, Track, base_title,
                                 clean_title, parse_queue)


def _reg(player, **kw) -> SkillRegistry:
    r = SkillRegistry()
    r.register(MusicSkill(player, **kw))
    return r


def _pear(tmp_path, handler) -> PearDesktopPlayer:
    idf = tmp_path / "id"
    idf.write_text("jarves-test", encoding="utf-8")
    (tmp_path / "id.paired").write_text("x", encoding="utf-8")
    return PearDesktopPlayer("http://pear.test", idf, 2.0, transport=httpx.MockTransport(handler))


def _auth_ok(req):
    if req.url.path.startswith("/auth/"):
        return httpx.Response(200, json={"accessToken": "t"})
    return None


@pytest.mark.parametrize("body", [b"{not json", b"<html>", b""])
async def test_unreadable_search_answer_is_an_honest_error_not_a_crash(tmp_path, body):
    def h(req):
        return _auth_ok(req) or (httpx.Response(200, content=body) if body else httpx.Response(204))
    r = await _reg(_pear(tmp_path, h)).call("music_play", {"query": "x"})
    assert not r.ok and r.code in ("MUSIC_PLAYER_ERROR", "MUSIC_NOT_FOUND") and r.code != "TOOL_CRASH"
    assert any("֐" <= c <= "׿" for c in r.message)


@pytest.mark.parametrize("song", [httpx.Response(200, content=b"<html>"),
                                  httpx.Response(200, json={"title": "X", "artist": "Y", "videoId": "v",
                                                            "isPaused": False, "songDuration": "n/a",
                                                            "elapsedSeconds": None}),
                                  httpx.Response(200, json=[1, 2])])
async def test_status_survives_odd_song_answers(tmp_path, song):
    def h(req):
        return _auth_ok(req) or song
    r = await _reg(_pear(tmp_path, h)).call("music_status")
    assert r.code != "TOOL_CRASH"


async def test_unreadable_queue_falls_back_and_still_plays(tmp_path):
    st = {"song": None}

    def h(req):
        if (a := _auth_ok(req)) is not None:
            return a
        p, m = req.url.path, req.method
        if p == "/api/v1/search":
            return httpx.Response(200, json={"musicResponsiveListItemRenderer": {
                "playlistItemData": {"videoId": "v1"}, "flexColumns": [
                    {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [{"text": "Song"}]}}}]}})
        if p == "/api/v1/queue" and m == "GET":
            return httpx.Response(200, content=b"garbage")
        if p == "/api/v1/queue" and m == "PATCH":
            st["song"] = "v1"
        if p == "/api/v1/song":
            return httpx.Response(204) if st["song"] is None else httpx.Response(
                200, json={"title": "Song", "artist": "A", "videoId": "v1", "isPaused": False, "elapsedSeconds": 1})
        return httpx.Response(204)
    p = _pear(tmp_path, h)
    p_track = p.play_track

    async def quick(t):                     # do not wait 3 s for a queue that never parses
        await p_track(t, wait_s=0.1)
    p.play_track = quick
    r = await _reg(p, verify_tries=2).call("music_play", {"query": "song"})
    assert r.ok, r.message
    assert parse_queue([1]) == [] and parse_queue({"items": "x"}) == [] and parse_queue({"items": [1]}) == []


@pytest.mark.parametrize("searched,played,same", [
    ("Bohemian Rhapsody (Official Video)", "Bohemian Rhapsody", True),
    ("Hotel California [Official Music Video]", "Hotel California (2013 Remaster)", True),
    ("Let It Be - Remastered 2009", "Let It Be", True),
    ("‏שני משוגעים", "שני  משוגעים‏", True),
    ("Bohemian Rhapsody", "Another One Bites the Dust", False),
    ("(Official Video)", "Other", False),
])
def test_same_track_counterpart_titles(searched, played, same):
    np = NowPlaying(played, "Queen", "song-id", False)
    assert MusicSkill._same_track(np, Track("video-id", searched)) is same


def test_title_normalisers():
    assert clean_title("‏שיר‏  של‫ אהבה‬") == "שיר של אהבה"
    assert base_title("Song X (Official Video) | Lyrics") == "song x"


async def test_next_resume_pause_with_nothing_playing_do_not_claim_an_action():
    fp = FakePlayer()
    reg = _reg(fp)
    for tool in ("music_next", "music_resume"):
        r = await reg.call(tool)
        assert not r.ok and r.code == "MUSIC_NOTHING_PLAYING" and "שום דבר לא מתנגן" in r.message
    for tool in ("music_pause", "music_stop"):
        r = await reg.call(tool)
        assert r.ok and "שום דבר לא מתנגן" in r.message
    assert "next" not in fp.calls and "resume" not in fp.calls


async def test_next_names_the_new_song_and_resume_is_confirmed():
    fp = FakePlayer()
    reg = _reg(fp, verify_tries=2)
    assert (await reg.call("music_play", {"query": "Song One"})).ok
    r = await reg.call("music_next")
    assert r.ok and "Song Two" in r.message and r.data["now_playing"]["video_id"] == "v2"
    await reg.call("music_pause")
    r = await reg.call("music_resume")
    assert r.ok and "Song Two" in r.message


def _yt(query_echo: str, *titles: str) -> dict:
    """Shape of a real YouTube Music answer: a top card + generic hits; the query only echoed in endpoints."""
    return {"contents": [
        {"musicCardShelfRenderer": {"title": {"runs": [{"text": titles[0], "navigationEndpoint": {
            "watchEndpoint": {"videoId": "card"}}}]}, "subtitle": {"runs": [{"text": "סרטון • Sound • 2.7K צפיות"}]}}},
        {"chipCloudChipRenderer": {"navigationEndpoint": {"searchEndpoint": {"query": query_echo}}}},
        *({"musicResponsiveListItemRenderer": {"playlistItemData": {"videoId": f"v{i}"}, "flexColumns": [
            {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [{"text": t}]}}}]}} for i, t in enumerate(titles[1:]))]}


async def test_gibberish_query_is_not_found_instead_of_playing_a_random_video(tmp_path):
    """QA 19 Oct on real Pear: 'asdkjhqwe zzxxcc' played a random user video and JARVES said 'מנגן: ...'."""
    answers = {"asdkjhqwe zzxxcc": _yt("asdkjhqwe zzxxcc", "リオが踊る【MMD】", "Believer", "Stressed Out"),
               "משהו רגוע": _yt("משהו רגוע", "משהו קטן וטוב", "תתארו לכם"),
               "Beatles": _yt("Beatles", "הביטלס", "Let It Be (The Beatles)")}

    def h(req):
        if (a := _auth_ok(req)) is not None:
            return a
        import json as _j
        return httpx.Response(200, json=answers[_j.loads(req.content)["query"]])
    p = _pear(tmp_path, h)
    assert await p.search("asdkjhqwe zzxxcc") == []
    assert [t.title for t in await p.search("משהו רגוע")][:1] == ["משהו קטן וטוב"]
    assert await p.search("Beatles")
    r = await _reg(p).call("music_play", {"query": "asdkjhqwe zzxxcc"})
    assert not r.ok and r.code == "MUSIC_NOT_FOUND"


async def test_dropped_next_press_is_retried_once():
    """Real Pear 3.12: a 'next' right after a track starts was sometimes ignored (S07/S10, 19 Oct)."""
    class DropFirst(FakePlayer):
        dropped = False

        async def next(self):
            if not self.dropped:
                self.dropped = True
                self.calls.append("next-dropped")
                return
            await super().next()
    fp = DropFirst()
    reg = _reg(fp)
    await reg.call("music_play", {"query": "Song One"})
    r = await reg.call("music_next")
    assert r.ok and "Song Two" in r.message and fp.calls.count("next") == 1 and "next-dropped" in fp.calls


class _QueuePear:
    """Pear queue with back-to-back copies, as found on the real queue on 19 Oct ([A, A, A, B])."""

    def __init__(self, queue, cur):
        self.q, self.cur, self.seen = list(queue), cur, []

    def __call__(self, req):
        import json as _j
        if (a := _auth_ok(req)) is not None:
            return a
        p, m = req.url.path, req.method
        self.seen.append((m, p))
        body = _j.loads(req.content) if req.content else {}
        if p == "/api/v1/queue" and m == "GET":
            return httpx.Response(200, json={"items": [{"playlistPanelVideoRenderer": {"videoId": v, "selected": i == self.cur}}
                                                       for i, v in enumerate(self.q)]})
        if p == "/api/v1/queue" and m == "PATCH":
            self.cur = body["index"]
        if p == "/api/v1/queue" and m == "POST":
            self.q.insert(self.cur + 1, body["videoId"])
        if p == "/api/v1/next":
            self.cur += 1
        if p == "/api/v1/song":
            return httpx.Response(200, json={"title": self.q[self.cur], "artist": "x", "videoId": self.q[self.cur], "elapsedSeconds": 1,
                                             "isPaused": False})
        return httpx.Response(204)


async def test_next_skips_back_to_back_copies_of_the_current_song(tmp_path):
    """V04 (real GPT-Live + Pear, 19 Oct): 'השיר הבא' stayed on the same song because the next queue items were
    copies of it."""
    qp = _QueuePear(["A", "A", "A", "B"], 0)
    r = await _reg(_pear(tmp_path, qp)).call("music_next")
    assert r.ok and r.data["now_playing"]["video_id"] == "B" and ("POST", "/api/v1/next") not in qp.seen


async def test_play_same_song_again_does_not_add_another_copy(tmp_path):
    qp = _QueuePear(["A", "B"], 0)
    p = _pear(tmp_path, qp)
    await p.play_track(Track("A", "A"))            # already current: just resume, no queue read, no insert
    assert qp.q == ["A", "B"] and qp.cur == 0 and ("POST", "/api/v1/queue") not in qp.seen
    assert ("GET", "/api/v1/queue") not in qp.seen
    await p.play_track(Track("C", "C"), wait_s=0.5)
    assert qp.q == ["A", "C", "B"] and qp.cur == 1


def test_relevance_check_is_fast_on_a_big_answer():
    """The first version joined the text at every recursion level: 8 s per real 250 KB search answer, which made
    every confirm ~10 s and timed out the soak. Must stay linear."""
    import time
    from jarves.skills.music import search_is_relevant
    big = {"contents": [{"shelf": {"runs": [{"text": f"title {i}"}, {"text": "artist"}],
                                   "nested": [{"x": {"text": "deep"}}] * 5}} for i in range(6000)]}
    t = time.perf_counter()
    assert search_is_relevant(big, "title 5999") and not search_is_relevant(big, "qqqzzz")
    assert time.perf_counter() - t < 1.0


async def test_play_reselects_when_the_queue_shifts_under_the_jump(tmp_path):
    """Final run 19 Oct, ~100-item real queue: every play kept the PREVIOUS request playing, because the index
    read after the insert pointed one item early by the time the jump landed."""
    class Shifting(_QueuePear):
        shifted = False

        def __call__(self, req):
            import json as _j
            if req.url.path == "/api/v1/queue" and req.method == "PATCH" and not self.shifted and req.content:
                self.shifted = True                  # an item appears at the front just before the jump lands
                self.q.insert(0, "Z")
                self.cur = _j.loads(req.content)["index"]
                self.seen.append(("PATCH", "/api/v1/queue"))
                return httpx.Response(204)
            return super().__call__(req)
    qp = Shifting(["A", "B", "C"], 1)
    p = _pear(tmp_path, qp)
    await p.play_track(Track("N", "N"), wait_s=0.5)
    assert qp.q[qp.cur] == "N" and qp.seen.count(("PATCH", "/api/v1/queue")) == 2


async def test_lagging_selected_flag_does_not_cause_a_second_jump(tmp_path):
    """Real Pear: right after a jump GET /queue may still show the old item selected. Jumping again to the same
    index froze playback (elapsed stuck at 0, pause ignored)."""
    class Lagging(_QueuePear):
        lag = 1

        def __call__(self, req):
            if req.url.path == "/api/v1/queue" and req.method == "GET" and self.lag and self.seen.count(("PATCH", "/api/v1/queue")):
                self.lag -= 1
                real, self.cur = self.cur, self.cur - 1         # report the previous item as selected once
                try:
                    return super().__call__(req)
                finally:
                    self.cur = real
            return super().__call__(req)
    qp = Lagging(["A", "B"], 0)
    p = _pear(tmp_path, qp)
    await p.play_track(Track("N", "N"), wait_s=0.5)
    assert qp.q[qp.cur] == "N" and qp.seen.count(("PATCH", "/api/v1/queue")) == 1


async def test_confirm_does_not_require_elapsed_time():
    """Pear's elapsedSeconds stays 0 while really playing (refreshed only on events): a playing, matching song
    with elapsed 0 must be confirmed (requiring progress gave 0/10 confirms on 19 Oct)."""
    class Zero(FakePlayer):
        async def play_track(self, track):
            self.np = NowPlaying(track.title, track.subtitle, track.video_id, False, 0, 200)
    assert (await _reg(Zero(), verify_tries=2).call("music_play", {"query": "Song One"})).ok


async def test_next_that_does_not_change_the_song_is_not_claimed():
    class Stuck(FakePlayer):
        async def next(self):
            self.calls.append("next")
    fp = Stuck()
    reg = _reg(fp, verify_tries=2)
    await reg.call("music_play", {"query": "Song One"})
    r = await reg.call("music_next")
    assert not r.ok and r.code == "MUSIC_NOT_CONFIRMED"
