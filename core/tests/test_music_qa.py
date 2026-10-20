"""Regressions from the pre-use music QA on real Pear Desktop 3.12 (19 Oct 2026, tools/suite_music.py)."""
from __future__ import annotations

import asyncio
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


# ---------------------------------------------------------------- queue growth (19 Oct campaign finding)
async def test_trim_history_removes_only_played_items_and_is_bounded(tmp_path):
    import httpx
    import json as _json
    from jarves.skills.music import PearDesktopPlayer

    def item(v, sel=False):
        return {"playlistPanelVideoRenderer": {"videoId": v, "selected": sel}}
    state = {"q": [item(f"H{i}") for i in range(30)] + [item("NOW", True)] + [item("N1"), item("N2")], "deleted": []}

    def handler(req: httpx.Request):
        if req.url.path.startswith("/auth/"):
            return httpx.Response(200, json={"accessToken": "t"})
        if req.url.path == "/api/v1/queue" and req.method == "GET":
            return httpx.Response(200, json={"items": state["q"]})
        if req.url.path == "/api/v1/queue/0" and req.method == "DELETE":
            state["deleted"].append(state["q"].pop(0)["playlistPanelVideoRenderer"]["videoId"])
            return httpx.Response(204)
        return httpx.Response(204)
    p = PearDesktopPlayer("http://127.0.0.1:26538", tmp_path / "id", transport=httpx.MockTransport(handler))
    await p.pair()
    n = await p.trim_history(keep_before=3, max_deletes=400)
    ids = [x["playlistPanelVideoRenderer"]["videoId"] for x in state["q"]]
    assert n == 27 and ids == ["H27", "H28", "H29", "NOW", "N1", "N2"]       # history trimmed to 3; now + upcoming kept
    assert await p.trim_history(keep_before=3) == 0                          # nothing more to trim
    state["q"][:0] = [item(f"X{i}") for i in range(500)]
    assert await p.trim_history(keep_before=3, max_deletes=40) == 40         # bounded per call
    await p.close()


async def test_music_skill_trims_in_the_background_after_a_successful_play():
    from jarves.skills.music import FakePlayer, MusicSkill, Track
    p = FakePlayer()
    t = Track("V1", "שיר", "אמן")
    p.catalog = [t]
    trimmed = []

    async def trim_history():
        trimmed.append(1)
        return 5
    p.trim_history = trim_history
    s = MusicSkill(p, verify_tries=2)
    play = next(x for x in s.tools() if x.name == "music_play")
    res = await play.handler(type("A", (), {"query": "שיר"})())
    assert res.ok
    await asyncio.sleep(0.1)
    assert trimmed == [1]                                                  # first play trims; it never fails the play


async def test_trim_never_overlaps_a_play_and_gives_way(tmp_path):
    """Real-Pear finding: trimming while a play jumped to a queue index shifted positions and the play failed."""
    import httpx
    from jarves.skills.music import PearDesktopPlayer, Track

    def item(v, sel=False):
        return {"playlistPanelVideoRenderer": {"videoId": v, "selected": sel}}
    events = []
    state = {"q": [item(f"H{i}") for i in range(40)] + [item("NOW", True)]}

    def handler(req: httpx.Request):
        if req.url.path.startswith("/auth/"):
            return httpx.Response(200, json={"accessToken": "t"})
        if req.url.path == "/api/v1/queue" and req.method == "GET":
            return httpx.Response(200, json={"items": state["q"]})
        if req.url.path == "/api/v1/queue/0" and req.method == "DELETE":
            events.append("delete")
            state["q"].pop(0)
            return httpx.Response(204)
        if req.url.path == "/api/v1/queue" and req.method == "POST":
            state["q"].append(item("NEW"))
            events.append("insert")
            return httpx.Response(204)
        if req.url.path == "/api/v1/queue" and req.method == "PATCH":
            events.append("patch")
            return httpx.Response(204)
        return httpx.Response(204)
    p = PearDesktopPlayer("http://127.0.0.1:26538", tmp_path / "id", transport=httpx.MockTransport(handler))
    await p.pair()
    trim = asyncio.create_task(p.trim_history(keep_before=3, max_deletes=30))
    await asyncio.sleep(0.4)                                       # the trim is mid-way
    await p.play_track(Track("NEW", "x", "y"), wait_s=1.0)         # a play arrives: the trim must give way, not interleave
    n = await trim
    assert 0 < n < 30                                              # it stopped early for the play
    first_play = events.index("insert")
    assert "delete" not in events[first_play:first_play + 3] or events.index("patch") > first_play
    last_delete_before = max(i for i, e in enumerate(events) if e == "delete" and i < first_play)
    assert all(e != "delete" for e in events[first_play:events.index("patch") + 1])     # no delete between insert and jump
    await p.close()


# ---------------------------------------------------------------- campaign findings, 19 Oct (real Pear)
class ScriptedPlayer:
    """Player whose behaviour per video id is scripted: which candidates actually start, whether pause takes effect."""

    def __init__(self, playable, pause_ignored=0):
        from jarves.skills.music import NowPlaying, Track
        self.NowPlaying, self.Track = NowPlaying, Track
        self.playable, self.pause_ignored, self.pause_calls = set(playable), pause_ignored, 0
        self.np = NowPlaying("Old", "X", "OLD", False, 5, 200)
        self.requested = []

    async def search(self, q):
        return [self.Track(v, f"Song {v}", "Artist") for v in ("A", "B", "C", "D")]

    async def play_track(self, t):
        self.requested.append(t.video_id)
        if t.video_id in self.playable:
            self.np = self.NowPlaying(t.title, "Artist", t.video_id, False, 1, 200)
        else:                                    # unavailable video: the player stays on something else
            self.np = self.NowPlaying("Other", "Y", "OTHER", False, 1, 200)

    async def pause(self):
        self.pause_calls += 1
        if self.pause_calls > self.pause_ignored:
            self.np.paused = True

    async def resume(self):
        self.np.paused = False

    async def next(self):
        pass

    async def set_volume(self, v):
        pass

    async def now_playing(self):
        return self.np


def _tool(skill, name):
    return next(t for t in skill.tools() if t.name == name)


async def test_play_tries_the_next_candidate_when_the_first_cannot_play():
    from jarves.skills.music import MusicSkill
    p = ScriptedPlayer(playable={"B"})
    res = await _tool(MusicSkill(p, verify_tries=2), "music_play").handler(type("A", (), {"query": "x"})())
    assert res.ok and "Song B" in res.message and p.requested == ["A", "B"]


async def test_play_reports_honestly_when_no_candidate_starts():
    from jarves.skills.music import MusicSkill
    p = ScriptedPlayer(playable=set())
    res = await _tool(MusicSkill(p, verify_tries=2), "music_play").handler(type("A", (), {"query": "x"})())
    assert not res.ok and res.code == "MUSIC_NOT_CONFIRMED" and res.data["tried"] == 3 and p.requested == ["A", "B", "C"]


async def test_pause_is_verified_pressed_again_and_never_claimed_falsely():
    from jarves.skills.music import MusicSkill
    ignored_once = ScriptedPlayer(playable={"A"}, pause_ignored=1)         # first press ignored (track still loading)
    res = await _tool(MusicSkill(ignored_once, verify_tries=8), "music_pause").handler(type("A", (), {})())
    assert res.ok and ignored_once.pause_calls == 2 and ignored_once.np.paused
    never = ScriptedPlayer(playable={"A"}, pause_ignored=99)
    res = await _tool(MusicSkill(never, verify_tries=3), "music_pause").handler(type("A", (), {})())
    assert not res.ok and res.code == "MUSIC_NOT_CONFIRMED"                # it must NOT say "paused"
