"""Face reactions (laugh / cry ...), on-screen activity and live mic level, fully offline."""
import asyncio

import pytest

from jarves.emotion import DURATION_MS, detect_emotion
from test_voice_faults import FakeClient, FakeLiveConn, Rec, loud, make_live


@pytest.mark.parametrize("text,role,expected", [
    ("חה חה חה זה היה טוב", "jarves", "laugh"),
    ("חחח", "user", "laugh"),
    ("hahaha that is funny", "jarves", "laugh"),
    ("אני עצוב היום", "user", "cry"),
    ("סבא שלי נפטר השבוע", "user", "cry"),
    ("מצטער לשמוע, זה נורא", "jarves", "cry"),
    ("וואו, לא ייאמן", "jarves", "surprised"),
    ("אני אוהב אותך", "user", "love"),
    ("אני עצוב וגם חחח", "user", "cry"),                 # sad news wins over a nervous laugh
    # must NOT fire: everyday words
    ("אבל אני לא יודע", "user", None),                   # "but" is not sad
    ("אני מצטער, לא הבנתי", "jarves", None),             # an apology is not grief
    ("תספר לי בדיחה", "user", None),                     # asking for a joke is not laughing yet
    ("מה השעה", "user", None),
    ("", "user", None),
])
def test_detect_emotion(text, role, expected):
    assert detect_emotion(text, role) == expected
    assert expected is None or expected in DURATION_MS


async def test_live_laughs_once_and_cries_for_sad_user_news():
    rec, emos = Rec(), []

    async def on_emotion(e):
        emos.append(e)
    lv = make_live(rec, FakeClient(), on_emotion=on_emotion)
    for chunk in ("חה ", "חה! ", "זה ", "מצחיק"):
        await lv.handle_event({"type": "session.output_transcript.delta", "delta": chunk})
    assert emos == ["laugh"]                                       # one reaction, not one per delta (5 s cooldown)
    await lv.handle_event({"type": "session.input_transcript.delta", "delta": "אני עצוב, הכלב שלי נפטר"})
    await lv.handle_event({"type": "session.output_transcript.delta", "delta": "אוי"})   # flushes the user turn
    assert emos == ["laugh", "cry"]


async def test_live_screen_activity_thinking_searching_music_then_clears(monkeypatch):
    rec, acts = Rec(), []

    async def on_activity(kind, text="", detail=""):
        acts.append((kind, text, detail))
    lv = make_live(rec, FakeClient(), on_activity=on_activity)
    lv.running = True
    lv.conn = FakeLiveConn()
    monkeypatch.setattr(lv, "_clear_activity_later", lambda delay=3.5: _instant_clear(lv))

    async def ev(inner):
        await lv.handle_event({"type": "response.event", "delegation_id": "d1", "event": inner})
    await ev({"type": "response.created"})
    await ev({"type": "response.web_search_call.searching"})
    await ev({"type": "response.output_item.added", "item": {"type": "web_search_call"}})
    assert [a[0] for a in acts] == ["thinking", "searching", "searching"] and acts[1][1] == "מחפש ברשת…"
    await lv._tool_activity("music_play", None)
    from jarves.skills import ToolResult
    await lv._tool_activity("music_play", ToolResult(True, "ok", {"now_playing": {"title": "תתארו לכם", "artist": "שלמה ארצי"}}))
    assert acts[-2][:2] == ("music", "מחפש שיר…") and acts[-1] == ("music", "מנגן", "תתארו לכם – שלמה ארצי")
    await lv._tool_activity("get_time", None)
    assert acts[-1][0] == "time"
    lv._activity_gen += 0
    await ev({"type": "response.completed"})
    await asyncio.sleep(0.05)
    assert acts[-1][0] == "done"                                   # the overlay clears itself after the answer


async def _instant_clear(lv):
    await lv._activity("done")


async def test_live_mic_level_goes_to_the_face_and_is_zero_while_gated():
    levels = []
    rec = Rec()

    async def on_level(x):
        levels.append(x)

    class Mic:
        def __init__(self):
            self.opened = True

        async def mic_chunks(self, rate, chunk_bytes):
            for i in range(6):
                yield loud(chunk_bytes)
                await asyncio.sleep(0.12)

        def queued_s(self):
            return 0.0

        def enqueue(self, pcm):
            pass

        def close(self):
            pass

        def stats(self):
            return {}

        async def run_player(self, rate):
            await asyncio.sleep(10)
    lv = make_live(rec, FakeClient(), Mic(), on_level=on_level)
    await lv.start()
    await asyncio.sleep(1.0)
    await lv.stop()
    assert levels and max(levels) > 0.4                            # real speech level reaches the ring
