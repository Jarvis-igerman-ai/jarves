"""Issue #16 review findings (music P1/P2, voice risks) and the volume/mute UX from the real session of 19 Oct 2026.
Offline: fake players, mock Pear transports, fake GPT-Live connection; no sound, no network."""
from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest
from test_voice_faults import FakeAudio, FakeClient, Rec, delta, loud, make_live

from jarves.skills import SkillRegistry
from jarves.skills.music import (FakePlayer, MusicSkill, NowPlaying, PearDesktopPlayer, Track, VolumeArgs,
                                 artists_match, slider_from_state, track_is_relevant)
from jarves.voice import live as live_mod


def _reg(player, **kw):
    r = SkillRegistry()
    sk = MusicSkill(player, **kw)
    r.register(sk)
    return r, sk


def _pear(tmp_path, handler) -> PearDesktopPlayer:
    idf = tmp_path / "id"
    idf.write_text("jarves-test", encoding="utf-8")
    (tmp_path / "id.paired").write_text("x", encoding="utf-8")
    return PearDesktopPlayer("http://pear.test", idf, 2.0, transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------- P2: title + artist, per-candidate relevance
@pytest.mark.parametrize("played_title,played_artist,searched,same", [
    ("Hello", "Adele", Track("v", "Hello", "Song • Lionel Richie • Can't Slow Down"), False),     # same title, other artist
    ("Hello", "Adele", Track("v", "Hello (Official Video)", "Video • Adele • 3.2B views"), True),
    ("Get Lucky", "Daft Punk, Pharrell Williams & Nile Rodgers", Track("v", "Get Lucky", "Song • Daft Punk"), True),
    ("Get Lucky", "DaftPunkVEVO", Track("v", "Get Lucky", "Video • Daft Punk"), True),               # channel name
    ("תתארו לכם", "שלמה ארצי", Track("v", "Tetaru Lachem", "Shlomo Artzi"), False),                  # other title
    ("Tetaru Lachem", "שלמה ארצי", Track("v", "Tetaru Lachem", "Shlomo Artzi"), True),               # scripts differ: unknown
    ("Bohemian Rhapsody", "Queen", Track("v", "Bohemian Rhapsody"), True),                          # no artist to compare
])
def test_same_track_needs_the_artist_too_when_both_name_one(played_title, played_artist, searched, same):
    np = NowPlaying(played_title, played_artist, "other-id", False)
    assert MusicSkill._same_track(np, searched) is same


def test_artist_match_is_unknown_not_false_without_data():
    assert artists_match("", Track("v", "X", "Song • Y")) is None
    assert artists_match("Y", Track("v", "X", "")) is None


def test_relevance_is_per_candidate_not_anywhere_on_the_page():
    assert not track_is_relevant(Track("v", "リオが踊る【MMD】", "Video • Sound • 2.7K views"), "Beatles")
    assert track_is_relevant(Track("v", "Let It Be", "Song • The Beatles"), "Beatles")
    assert track_is_relevant(Track("v", "Yesterday", "Song • The Beatles"), "play the beatles music")
    assert track_is_relevant(Track("v", "שיר הרגוע", "שיר • אמן"), "משהו רגוע") or \
        track_is_relevant(Track("v", "משהו קטן וטוב", ""), "משהו רגוע")
    assert track_is_relevant(Track("v", "הביטלס - אוסף", ""), "ביטלס")                # Hebrew prefix tolerated
    assert not track_is_relevant(Track("v", "With You", "Song • Someone"), "it")       # whole words only
    assert track_is_relevant(Track("v", "Anything", ""), "שיר")                         # filler-only query


def _yt(top_title: str, top_sub: str, *rows: tuple[str, str]) -> dict:
    """A top card (playable) + list rows, as YouTube Music answers a search."""
    return {"contents": [
        {"musicCardShelfRenderer": {"title": {"runs": [{"text": top_title, "navigationEndpoint": {
            "watchEndpoint": {"videoId": "card"}}}]}, "subtitle": {"runs": [{"text": top_sub}]}}},
        *({"musicResponsiveListItemRenderer": {"playlistItemData": {"videoId": f"v{i}"}, "flexColumns": [
            {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [{"text": t}]}}},
            {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [{"text": s}]}}}]}}
          for i, (t, s) in enumerate(rows))]}


async def test_unrelated_top_result_is_skipped_even_when_the_page_mentions_the_query(tmp_path):
    """Review finding: the old page-level check accepted ANY query word anywhere on the page."""
    answers = {
        # top result unrelated; the query word only appears in a non-playable artist text further down
        "Radiohead": _yt("Random Viral Clip", "Video • SomeUser • 2.7K views", ("Artist", "Radiohead • 9M subscribers")),
        "Creep": _yt("Random Viral Clip", "Video • SomeUser", ("Creep", "Song • Radiohead • Pablo Honey")),
    }

    def h(req):
        if req.url.path.startswith("/auth/"):
            return httpx.Response(200, json={"accessToken": "t"})
        return httpx.Response(200, json=answers[json.loads(req.content)["query"]])
    p = _pear(tmp_path, h)
    got = await p.search("Radiohead")
    assert [t.title for t in got] == ["Artist"]          # only the row that itself names Radiohead
    got = await p.search("Creep")
    assert [t.video_id for t in got] == ["v0"]           # the unrelated top card is not a candidate
    await p.close()


def test_rows_with_the_id_only_in_the_watch_endpoint_are_playable():
    """Real Pear 19 Oct 2026, "ABBA Dancing Queen": no row had playlistItemData, so the answer parsed to nothing
    and JARVES said "not found"."""
    from jarves.skills.music import parse_search

    def row(vid, title, sub, where):
        r = {"flexColumns": [{"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [{"text": title}]}}},
                             {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [{"text": sub}]}}}]}
        if where == "nav":
            r["navigationEndpoint"] = {"watchEndpoint": {"videoId": vid}}
        elif where == "overlay":
            r["overlay"] = {"musicItemThumbnailOverlayRenderer": {"content": {"musicPlayButtonRenderer": {
                "playNavigationEndpoint": {"watchEndpoint": {"videoId": vid}}}}}}
        else:
            r["navigationEndpoint"] = {"browseEndpoint": {"browseId": "album"}}
        return {"musicResponsiveListItemRenderer": r}
    resp = {"contents": [row("A1", "Dancing Queen", "שיר • ABBA • 3:51", "nav"),
                         row("A2", "Dancing Queen (Live)", "סרטון • ABBA", "overlay"),
                         row(None, "Gold", "אלבום • ABBA • 1992", "album")]}
    assert [(t.video_id, t.title) for t in parse_search(resp)] == [("A1", "Dancing Queen"), ("A2", "Dancing Queen (Live)")]


def test_songs_inside_an_artist_card_inherit_the_artist_name():
    """Real Pear 19 Oct 2026, "נועה קירל": the Hebrew artist card lists songs whose rows say only "שיר • 3:24"."""
    from jarves.skills.music import parse_search
    resp = {"musicCardShelfRenderer": {
        "title": {"runs": [{"text": "נועה קירל", "navigationEndpoint": {"browseEndpoint": {}}}]},
        "subtitle": {"runs": [{"text": "אומן/ית"}]},
        "contents": [{"musicResponsiveListItemRenderer": {"playlistItemData": {"videoId": "T1"}, "flexColumns": [
            {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [{"text": "טרילילי טרללה"}]}}},
            {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [{"text": "שיר • 3:24"}]}}}]}}]}}
    t = parse_search(resp)
    assert [x.video_id for x in t] == ["T1"] and track_is_relevant(t[0], "נועה קירל")
    # the player then names the artist in Latin with a Hebrew "and": cannot compare -> the title decides
    np = NowPlaying("טרילילי טרללה", "Noa Kirel ו-Ilan Peled", "other", False)
    assert artists_match(np.artist, t[0]) is None and MusicSkill._same_track(np, t[0])


def test_hebrew_ui_subtitles_still_compare_latin_artists():
    t = Track("v", "Hello", "שיר • Lionel Richie • 4:09")
    assert artists_match("Adele", t) is False
    assert artists_match("Daft Punk, Pharrell Williams & Nile Rodgers",
                         Track("v", "Get Lucky", "שיר • Daft Punk‏, Pharrell Williams ו-Nile Rodgers • 4:09")) is True


async def test_one_slow_status_answer_does_not_fail_the_confirm():
    class Hiccup(FakePlayer):
        n = 0

        async def now_playing(self):
            self.n += 1
            if self.n == 1:
                from jarves.skills.music import MusicError
                raise MusicError("MUSIC_TIMEOUT", "slow")
            return await super().now_playing()
    r, _ = _reg(Hiccup(), verify_tries=4)
    assert (await r.call("music_play", {"query": "Song One"})).ok


async def test_a_result_the_player_skips_fails_fast_and_the_next_one_is_tried():
    """Real Pear 19 Oct 2026: an unplayable "Get Lucky" made Pear jump to "Love Again"; JARVES polled 8 s per try
    and ran out of time. A third song playing on two polls = this result failed."""
    class Skips(FakePlayer):
        async def play_track(self, t):
            self.calls.append(f"play:{t.video_id}")
            self.np = NowPlaying("Love Again", "Dua Lipa", "LOVE", False) if t.video_id == "bad" else \
                NowPlaying(t.title, t.subtitle, t.video_id, False)
    fp = Skips([Track("bad", "Get Lucky (Radio Edit)", "Daft Punk"), Track("good", "Get Lucky", "Daft Punk")])
    fp.np = NowPlaying("Old", "X", "OLD", False)
    r, _ = _reg(fp, verify_tries=16)
    t = time.monotonic()
    res = await r.call("music_play", {"query": "get lucky"})
    assert res.ok and res.data["now_playing"]["video_id"] == "good" and time.monotonic() - t < 3.0


# ---------------------------------------------------------------- P1: honest playback wording
async def test_play_result_says_the_player_reports_it_not_that_it_is_audible():
    r, _ = _reg(FakePlayer(), verify_tries=2)
    res = await r.call("music_play", {"query": "Song One"})
    assert res.ok and res.data["playback"] == "player_reports_playing"
    assert "cannot hear" in res.data["note"]


async def test_stalled_player_that_ignores_pause_gets_an_actionable_message():
    class Stalled(FakePlayer):
        async def pause(self):
            self.calls.append("pause")               # a stuck video ignores pause (QA 19 Oct)
    fp = Stalled()
    r, _ = _reg(fp, verify_tries=3)
    await r.call("music_play", {"query": "Song One"})
    res = await r.call("music_pause")
    assert not res.ok and res.code == "MUSIC_NOT_CONFIRMED" and "Pear Desktop" in res.message


# ---------------------------------------------------------------- P2: one end-to-end deadline
class SlowPlayer(FakePlayer):
    def __init__(self, play_delay=0.0, search_delay=0.0, starts=True):
        super().__init__()
        self.play_delay, self.search_delay, self.starts = play_delay, search_delay, starts
        self.catalog = [Track(f"v{i}", f"Song {i}", "Band") for i in range(4)]

    async def search(self, q):
        await asyncio.sleep(self.search_delay)
        return list(self.catalog)

    async def play_track(self, t):
        self.calls.append(f"play:{t.video_id}")
        await asyncio.sleep(self.play_delay)
        if self.starts:
            await super().play_track(t)


async def test_stuck_play_returns_its_own_truthful_result_before_the_tool_timeout():
    fp = SlowPlayer(play_delay=60)
    r, sk = _reg(fp, verify_tries=16, play_budget_s=2.0)
    t = time.monotonic()
    res = await r.call("music_play", {"query": "song"})
    took = time.monotonic() - t
    assert took < 3.0 and res.code == "MUSIC_NOT_CONFIRMED" and res.code != "TOOL_TIMEOUT"
    assert "בזמן" in res.message and res.data["tried"] == 1


async def test_candidates_never_starting_stay_inside_the_deadline():
    fp = SlowPlayer(starts=False)                    # every candidate polls the full verify window
    r, sk = _reg(fp, verify_tries=16, play_budget_s=6.0)
    t = time.monotonic()
    res = await r.call("music_play", {"query": "song"})
    took = time.monotonic() - t
    assert not res.ok and res.code == "MUSIC_NOT_CONFIRMED" and took < 6.8
    assert 1 <= res.data["tried"] <= 2                # no third candidate started with < MIN_CANDIDATE_S left


async def test_slow_search_is_an_honest_timeout():
    r, _ = _reg(SlowPlayer(search_delay=60), play_budget_s=5.0)
    t = time.monotonic()
    res = await r.call("music_play", {"query": "song"})
    assert res.code == "MUSIC_TIMEOUT" and time.monotonic() - t < 2.5


def test_play_budget_always_ends_before_the_registry_timeout():
    _, sk = _reg(FakePlayer(), play_budget_s=60)
    play = next(t for t in sk.tools() if t.name == "music_play")
    assert sk.play_budget_s <= play.timeout_s - 2


# ---------------------------------------------------------------- P2: queue trim opt-out and shutdown
async def test_trim_can_be_switched_off():
    fp = FakePlayer()
    calls = []

    async def trim_history():
        calls.append(1)
        return 1
    fp.trim_history = trim_history
    r, sk = _reg(fp, verify_tries=2, trim_queue=False)
    assert (await r.call("music_play", {"query": "Song One"})).ok
    await asyncio.sleep(0.05)
    assert calls == [] and sk._trim_task is None


async def test_close_cancels_a_running_trim_then_closes_the_player():
    fp = FakePlayer()
    state = {"cancelled": False, "closed": False}

    async def trim_history():
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise

    async def close():
        state["closed"] = True
    fp.trim_history, fp.close = trim_history, close
    r, sk = _reg(fp, verify_tries=2)
    assert (await r.call("music_play", {"query": "Song One"})).ok
    await asyncio.sleep(0.05)
    assert sk._trim_task is not None and not sk._trim_task.done()
    await asyncio.wait_for(sk.close(), 2)
    assert state == {"cancelled": True, "closed": True}


def test_config_has_trim_and_step_switches():
    from jarves.config import MusicConfig
    c = MusicConfig()
    assert c.trim_queue is True and c.volume_step == 10
    assert MusicConfig(trim_queue=False).trim_queue is False


# ---------------------------------------------------------------- volume UX (real session 19 Oct 2026)
@pytest.mark.parametrize("state,slider", [(0, 0), (1, 5), (2, 10), (20, 50), (40, 70), (55, 80), (74, 90), (100, 100),
                                          (30, 61)])
def test_slider_from_pear_state_uses_the_measured_curve(state, slider):
    assert slider_from_state(state) == slider


async def test_louder_is_a_step_not_a_jump_to_the_cap():
    fp = FakePlayer()
    fp.volume = 40
    r, _ = _reg(fp, max_volume=70)
    res = await r.call("music_volume", {"change": "up"})
    assert res.ok and fp.volume == 50 and res.data["previous"] == 40 and res.data["volume"] == 50
    assert "מ-40 ל-50" in res.message
    res = await r.call("music_volume", {"change": "down", "step": 20})
    assert fp.volume == 30 and res.data["volume"] == 30


async def test_louder_at_the_cap_does_not_change_and_says_why():
    fp = FakePlayer()
    fp.volume = 70
    r, _ = _reg(fp, max_volume=70)
    res = await r.call("music_volume", {"change": "up"})
    assert res.ok and fp.volume == 70 and res.data["changed"] is False and res.data["at_limit"] is True
    assert "70" in res.message and "תקרה" in res.message
    fp.volume = 65
    res = await r.call("music_volume", {"change": "up"})
    assert fp.volume == 70 and res.data["capped"] is True


async def test_volume_args_need_exactly_one_of_percent_or_change():
    r, _ = _reg(FakePlayer())
    assert (await r.call("music_volume", {})).code == "TOOL_BAD_ARGS"
    assert (await r.call("music_volume", {"percent": 30, "change": "up"})).code == "TOOL_BAD_ARGS"
    assert (await r.call("music_volume", {"change": "sideways"})).code == "TOOL_BAD_ARGS"
    assert VolumeArgs(percent=90).percent == 90                      # the old absolute form still works


async def test_relative_change_without_a_readable_volume_is_honest():
    class NoRead(FakePlayer):
        get_volume = None
    r, _ = _reg(NoRead())
    res = await r.call("music_volume", {"change": "up"})
    assert not res.ok and res.code == "MUSIC_VOLUME_UNKNOWN"


async def test_status_reports_the_real_volume_and_mute():
    fp = FakePlayer()
    r, _ = _reg(fp, verify_tries=2)
    await r.call("music_play", {"query": "Song One"})
    fp.volume = 60
    res = await r.call("music_status")
    assert res.data["volume"] == 60 and res.data["muted"] is False and "60 מתוך 100" in res.message


async def test_mute_is_not_pause_and_unmute_brings_the_sound_back():
    """Real session: "תעשה מיוט" called music_pause and the song stopped."""
    fp = FakePlayer()
    r, _ = _reg(fp, verify_tries=2)
    await r.call("music_play", {"query": "Song One"})
    res = await r.call("music_mute")
    assert res.ok and fp.muted and not fp.np.paused and res.data["muted"] is True    # still playing, silently
    assert "pause" not in fp.calls
    assert (await r.call("music_mute")).data["changed"] is False                     # already muted: no toggle back
    res = await r.call("music_unmute")
    assert res.ok and not fp.muted and res.data["volume"] == 50
    assert fp.calls.count("toggle_mute") == 2


async def test_mute_not_reflected_by_the_player_is_not_claimed():
    class Ignores(FakePlayer):
        async def toggle_mute(self):
            self.calls.append("toggle_mute")
    r, sk = _reg(Ignores())
    res = await r.call("music_mute")
    assert not res.ok and res.code == "MUSIC_NOT_CONFIRMED"


def test_mute_tools_are_described_apart_from_pause_and_allowed_by_policy():
    from jarves.skills import DEFAULT_ALLOWED_ACTIONS
    _, sk = _reg(FakePlayer())
    d = {t.name: t.description for t in sk.tools()}
    assert "WITHOUT pausing" in d["music_mute"] and "music_mute" in d["music_pause"]
    assert "change='up'" in d["music_volume"] and "volume" in d["music_status"]
    assert {"music_mute", "music_unmute"} <= DEFAULT_ALLOWED_ACTIONS


async def test_pear_volume_and_mute_endpoints(tmp_path):
    st = {"state": 40, "muted": False, "posted": []}

    def h(req):
        if req.url.path.startswith("/auth/"):
            return httpx.Response(200, json={"accessToken": "t"})
        if req.url.path == "/api/v1/volume" and req.method == "GET":
            return httpx.Response(200, json={"state": st["state"], "isMuted": st["muted"]})
        if req.url.path == "/api/v1/volume" and req.method == "POST":
            v = json.loads(req.content)["volume"]
            st["posted"].append(v)
            st["state"], st["muted"] = {50: 20, 60: 29, 70: 40, 80: 55}.get(v, 0), v == 0
            return httpx.Response(204)
        if req.url.path == "/api/v1/toggle-mute":
            st["muted"] = not st["muted"]
            return httpx.Response(204)
        return httpx.Response(204)
    p = _pear(tmp_path, h)
    assert await p.get_volume() == (70, False)          # state 40 is slider 70, not "40 %"
    r, _ = _reg(p, max_volume=80)
    res = await r.call("music_volume", {"change": "down"})
    assert st["posted"] == [60] and res.data == {**res.data, "previous": 70, "volume": 60}
    res = await r.call("music_mute")
    assert res.ok and st["muted"]
    res = await r.call("music_unmute")
    assert res.ok and not st["muted"] and res.data["volume"] == 60
    await p.close()


# ---------------------------------------------------------------- voice risk: long first utterance, late transcript
class _Clock:
    """Speeds up live.py's clock only (asyncio timers stay real): `speed` simulated seconds per real second."""

    def __init__(self, speed: float) -> None:
        self.t0, self.speed = time.monotonic(), speed

    def monotonic(self) -> float:
        return self.t0 + (time.monotonic() - self.t0) * self.speed

    def __getattr__(self, name):
        return getattr(time, name)


class _TalkingAudio(FakeAudio):
    """A mic on which the user talks (loud chunks) while `talking` is set."""

    def __init__(self):
        super().__init__()
        self.talking = True

    async def mic_chunks(self, rate, chunk_bytes):
        while self._open:
            await asyncio.sleep(0.01)
            yield loud(chunk_bytes) if self.talking else bytes(chunk_bytes)


SPEED = 10.0      # watchdog ticks every real second = 10 simulated seconds


@pytest.mark.parametrize("talk_s", [20.0, 35.0])
async def test_long_first_utterance_with_late_transcript_is_not_cut_as_a_false_wake(monkeypatch, talk_s):
    """Issue #16 hypothesis: the 12 s no-speech cutoff watched transcripts only. A user talking 20-35 s right
    after the wake word, whose transcript arrives only at the end, must not be cut off."""
    clock = _Clock(SPEED)
    monkeypatch.setattr(live_mod, "time", clock)
    rec, audio = Rec(), _TalkingAudio()
    lv = make_live(rec, FakeClient(), audio, no_speech_timeout_s=12, idle_disconnect_s=600)
    await lv.start()
    await asyncio.sleep(talk_s / SPEED)
    assert rec.ends == [], f"cut off after {talk_s} s of speech"
    await lv.handle_event({"type": "session.input_transcript.delta", "delta": "סיפור ארוך"})   # transcript at last
    audio.talking = False
    await asyncio.sleep(20 / SPEED)
    assert rec.ends == []                                        # a real conversation now: no false-wake close
    await lv.stop()


async def test_room_noise_still_cannot_hold_a_false_wake_session_open(monkeypatch):
    clock = _Clock(SPEED)
    monkeypatch.setattr(live_mod, "time", clock)
    rec, audio = Rec(), _TalkingAudio()                          # loud forever, never transcribed
    lv = make_live(rec, FakeClient(), audio, no_speech_timeout_s=12, idle_disconnect_s=600)
    await lv.start()
    await asyncio.sleep(35 / SPEED)
    assert rec.ends == []                                        # within the bounded grace (12 + 30 s)
    for _ in range(40):
        if rec.ends:
            break
        await asyncio.sleep(0.1)
    assert rec.ends == ["no_speech"]                             # closed by ~50 simulated s, not minutes


async def test_no_speech_cutoff_unchanged_when_the_mic_is_quiet():
    rec, audio = Rec(), FakeAudio()                              # digital silence
    lv = make_live(rec, FakeClient(), audio, no_speech_timeout_s=0.6, idle_disconnect_s=60)
    await lv.start()
    await asyncio.sleep(2.0)
    assert rec.ends == ["no_speech"]


# ---------------------------------------------------------------- voice risk: dropped audio vs caption/face
def _text(s):
    return {"type": "session.output_transcript.delta", "delta": s}


async def test_caption_of_a_dropped_early_answer_is_never_shown():
    rec, audio = Rec(), FakeAudio()
    said = []

    async def on_transcript(role, text):
        said.append((role, text))
    lv = make_live(rec, FakeClient(), audio, on_transcript=on_transcript)
    lv._last_user_voice = time.monotonic()                       # the user is mid-sentence
    await lv.handle_event(_text("כן, אז "))                      # early answer: text first...
    await lv.handle_event(delta(loud(4800 * 3)))                 # ...then its audio, dropped
    await lv.handle_event(_text("מה שרצית"))                     # the rest of that answer's text
    assert audio.queued_s() == 0 and "speaking" not in rec.states
    assert lv._out_text == "" and [r for r, _ in lv._flush_transcripts()] == []
    assert lv.stats()["dropped_caption_chars"] > 0
    # recovery: the user finished; the real answer is heard AND captioned
    lv._last_user_voice -= 5
    lv._drop_until = 0.0
    await lv.handle_event(_text("התשובה המלאה"))
    await lv.handle_event(delta(loud(4800 * 3)))
    assert audio.queued_s() > 0 and "speaking" in rec.states
    assert lv._flush_transcripts() == [("assistant", "התשובה המלאה")]
    assert said == []                                            # nothing unheard went to the screen meanwhile


async def test_held_caption_is_released_when_its_audio_really_plays():
    rec, audio = Rec(), FakeAudio()
    lv = make_live(rec, FakeClient(), audio)
    lv._last_user_voice = time.monotonic()                       # text arrives while the user just spoke
    await lv.handle_event(_text("שלום לך"))
    assert lv._held_text == "שלום לך" and lv._out_text == ""
    lv._last_user_voice -= 5                                     # user done: the audio is a normal answer
    await lv.handle_event(delta(loud(4800 * 3)))
    assert lv._out_text == "שלום לך" and lv._held_text == "" and audio.queued_s() > 0


async def test_normal_answers_are_captioned_immediately():
    rec, audio = Rec(), FakeAudio()
    lv = make_live(rec, FakeClient(), audio)
    await lv.handle_event(_text("מה נשמע"))
    assert lv._out_text == "מה נשמע" and lv._held_text == ""
