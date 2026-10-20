"""Voice campaign follow-up fixes (#17, branch v3-voice-fixes): CV-15 acknowledged-but-never-delegated watchdog,
CV-06 unasked memory claims, CV-05 language matching, CV-09 stale progress after a barge-in, CV-14 one WhatsApp
confirmation, CV-08 no web search for arithmetic, CV-16 name-prefix pronunciation rule."""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from jarves import brain as brain_mod
from jarves.skills import SkillRegistry
from jarves.voice import live as live_mod
from jarves.voice.live import LiveVoice, front_instructions, text_language, user_asked_action, waiting_only


class Sent:
    def __init__(self) -> None:
        self.items: list[tuple] = []

    def appender(self, kind):
        async def append(content, delegation_id=None, event_id=None):
            self.items.append((kind, content, event_id))
        return SimpleNamespace(append=append)

    def kinds(self, kind):
        return [x for x in self.items if x[0] == kind]


def make_live(**kw):
    lv = LiveVoice(SkillRegistry(), lambda: "backend", **kw)
    sent = Sent()

    async def item_create(item, event_id=None):
        sent.items.append(("item", item, event_id))

    async def response_create(event_id=None):
        sent.items.append(("response", None, event_id))

    lv.conn = SimpleNamespace(
        session=SimpleNamespace(thinking=sent.appender("thinking"), commentary=sent.appender("commentary"),
                                instructions=sent.appender("instructions")),
        response=SimpleNamespace(create=response_create, item=SimpleNamespace(create=item_create)))
    lv.running = lv._active = True
    lv._backend_busy = False
    return lv, sent


async def _cleanup(lv):
    lv.running = False
    for t in lv._tasks:
        t.cancel()
    await asyncio.gather(*lv._tasks, return_exceptions=True)


async def _user(lv, text):
    await lv.handle_event({"type": "session.input_transcript.delta", "delta": text})


async def _jarves(lv, text):
    await lv.handle_event({"type": "session.output_transcript.delta", "delta": text})
    lv._flush_transcripts()


# ------------------------------------------------------------------ helpers
def test_waiting_only_tells_lead_ins_from_answers():
    assert waiting_only("רגע...") and waiting_only("רגע, אני בודק") and waiting_only("שנייה, מחשב.")
    assert waiting_only("רגע, אני בודק מה מזג האוויר בתל אביב")
    assert not waiting_only("רשמתי שהצבע האהוב עליך הוא ירוק.")
    assert not waiting_only("רגע. מחקתי את הצבע האהוב עליך.")
    assert not waiting_only("אחלה") and not waiting_only("")


def test_user_asked_action_and_language():
    assert user_asked_action("תמחק מהזיכרון את הצבע האהוב עליי")
    assert user_asked_action("תזכור שהחניה שלי שלושים ושבע") and user_asked_action("please remember my code")
    assert not user_asked_action("דרך אגב, לאחותי קוראים נועה והיא גרה בחיפה")
    assert text_language("What is the capital city of Australia?") == "en"
    assert text_language("ומה עיר הבירה של קנדה?") == "he"
    assert text_language("ok") == ""


# ------------------------------------------------------------------ CV-15
async def test_cv15_undelegated_wait_is_handed_to_the_backend_and_its_result_said(monkeypatch):
    monkeypatch.setattr(live_mod, "REDELEGATE_AFTER_S", 0.05)
    monkeypatch.setattr(live_mod, "OWN_DELIVERY_WAIT_S", 0.01)
    monkeypatch.setattr(live_mod, "ANNOUNCE_USER_QUIET_S", 0.0)
    lv, sent = make_live()
    try:
        await _user(lv, "תמחק מהזיכרון את הצבע האהוב עליי")
        await _jarves(lv, "רגע...")
        await asyncio.sleep(0.4)
        items = sent.kinds("item")
        assert len(items) == 1 and "תמחק מהזיכרון את הצבע האהוב עליי" in items[0][1]["content"][0]["text"]
        assert sent.kinds("response") and sent.kinds("response")[0][2].startswith("jarves_redelegate")
        assert lv._stats["undelegated_turns"] == 1 and lv._stats["redelegations"] == 1
        # the Core-started response runs (delegation id None) and completes with an answer nobody said yet
        await lv.handle_event({"type": "response.event", "delegation_id": None,
                               "event": {"type": "response.created", "response": {"id": "r1"}}})
        await lv.handle_event({"type": "response.event", "delegation_id": None,
                               "event": {"type": "response.output_text.done", "text": "מחקתי את הצבע האהוב עליך."}})
        await lv.handle_event({"type": "response.event", "delegation_id": None,
                               "event": {"type": "response.completed", "response": {"id": "r1"}}})
        await asyncio.sleep(2.0)
        notes = [c for k, c, _ in sent.items if k == "commentary"]
        assert len(notes) == 1 and "מחקתי את הצבע" in notes[0] and "finished the user's last request" in notes[0]
    finally:
        await _cleanup(lv)


async def test_cv15_handover_arrives_as_a_delegation_and_gpt_live_says_it(monkeypatch):
    """Live probe 20 Oct: the Core's response.create comes back as session.delegation.created and GPT-Live speaks
    the result itself: no 'did not go through' note, no second hand-over of the result."""
    monkeypatch.setattr(live_mod, "REDELEGATE_AFTER_S", 0.05)
    monkeypatch.setattr(live_mod, "OWN_DELIVERY_WAIT_S", 0.05)
    lv, sent = make_live()
    try:
        await _user(lv, "תמחק מהזיכרון את הצבע האהוב עליי")
        await _jarves(lv, "רגע...")
        await asyncio.sleep(0.3)
        n0 = lv._delegations
        await lv.handle_event({"type": "session.delegation.created", "delegation": {"id": "d9", "target": "responses"}})
        assert lv._own_delegation == "d9" and lv._own_pending is None and lv._delegations == n0
        for ev in ({"type": "response.created", "response": {"id": "r9"}},
                   {"type": "response.output_text.done", "text": "מחקתי את הצבע האהוב עליך."},
                   {"type": "response.completed", "response": {"id": "r9"}}):
            await lv.handle_event({"type": "response.event", "delegation_id": "d9", "event": ev})
        await _jarves(lv, "מחקתי את הצבע האהוב עליך.")
        await asyncio.sleep(1.0)
        assert not [x for x in sent.items if x[0] == "commentary"]
    finally:
        await _cleanup(lv)


async def test_cv15_no_handover_when_the_model_delegates_after_its_lead_in(monkeypatch):
    monkeypatch.setattr(live_mod, "REDELEGATE_AFTER_S", 0.3)
    lv, sent = make_live()
    try:
        await _user(lv, "תזכור שהצבע האהוב עליי ירוק")
        await _jarves(lv, "רגע")
        await lv.handle_event({"type": "session.delegation.created", "delegation": {"id": "d1", "target": "x"}})
        await asyncio.sleep(0.6)
        assert not sent.kinds("item") and not sent.kinds("response")
    finally:
        await _cleanup(lv)


async def test_cv15_failed_handover_is_reported_honestly(monkeypatch):
    monkeypatch.setattr(live_mod, "REDELEGATE_AFTER_S", 0.05)
    monkeypatch.setattr(live_mod, "ANNOUNCE_USER_QUIET_S", 0.0)
    lv, sent = make_live()
    try:
        await _user(lv, "תמחק את רשימת הקניות")
        await _jarves(lv, "רגע, מוחק.")
        await asyncio.sleep(0.3)
        await lv.handle_event({"type": "error", "error": {"code": "x", "message": "no",
                                                          "client_event_id": "jarves_redelegate_1"}})
        await asyncio.sleep(1.8)
        notes = [c for k, c, _ in sent.items if k == "commentary"]
        assert notes == [live_mod.REDELEGATE_FAIL_NOTE]
    finally:
        await _cleanup(lv)


def test_cv15_persona_says_wait_word_is_not_the_action():
    text = front_instructions()
    assert 'Saying\n"רגע" is NOT doing it' in text and "never instead of delegating" in text


# ------------------------------------------------------------------ CV-06
async def test_cv06_unasked_memory_claim_is_taken_back_not_saved(monkeypatch):
    monkeypatch.setattr(live_mod, "ANNOUNCE_USER_QUIET_S", 0.0)
    lv, sent = make_live()
    try:
        await _user(lv, "דרך אגב, לאחותי קוראים נועה והיא גרה בחיפה")
        await _jarves(lv, "אחלה, רשמתי שנועה גרה בחיפה.")
        await asyncio.sleep(2.0)
        notes = [c for k, c, _ in sent.items if k == "commentary"]
        assert len(notes) == 1 and "did not ask you to remember" in notes[0]
        assert not sent.kinds("item")                       # nothing handed to the backend: nothing saved
    finally:
        await _cleanup(lv)


async def test_cv06_asked_but_undone_claim_is_handed_to_the_backend(monkeypatch):
    monkeypatch.setattr(live_mod, "ANNOUNCE_USER_QUIET_S", 0.0)
    lv, sent = make_live()
    try:
        await _user(lv, "תרשום לך בבקשה שהקוד של השער הוא אחת שתיים שלוש")
        await _jarves(lv, "רשמתי שהקוד של השער הוא אחת שתיים שלוש.")
        await asyncio.sleep(1.0)
        assert len(sent.kinds("item")) == 1 and sent.kinds("response")
    finally:
        await _cleanup(lv)


def test_cv06_persona_forbids_unasked_memory_claims():
    assert "only TELLS you something" in front_instructions()


# ------------------------------------------------------------------ CV-05
async def test_cv05_language_note_on_change_only():
    lv, sent = make_live()
    try:
        await _user(lv, "What is the capital")
        await _user(lv, " city of Australia?")
        await _jarves(lv, "Canberra.")
        await _user(lv, "ומה עיר הבירה של קנדה?")
        await _jarves(lv, "אוטווה.")
        await _user(lv, "ומה הבירה של יפן?")
        notes = [(c, e) for k, c, e in sent.items if k == "instructions"]
        assert len(notes) == 2
        assert "English" in notes[0][0] and "Hebrew" in notes[1][0] and notes[0][1].startswith("jarves_lang")
    finally:
        await _cleanup(lv)


def test_cv05_persona_answers_in_the_language_of_the_latest_sentence():
    text = front_instructions()
    assert "LANGUAGE OF THE USER'S LATEST SENTENCE" in text and "[language]" in text


# ------------------------------------------------------------------ CV-09
async def test_cv09_superseded_lookup_gets_no_progress_note_and_no_retry(monkeypatch):
    lv, sent = make_live(progress_after_s=0.05, backend_deadline_s=0.4)
    retried = []

    async def retry(q):
        retried.append(q)
        return "ארבע"
    lv.retry_lookup = retry
    try:
        await _user(lv, "ספר לי סיפור ארוך על ירושלים")
        await lv.handle_event({"type": "session.delegation.created", "delegation": {"id": "d1", "target": "x"}})
        lv._deleg_turn["d1"] = (lv._user_turn_n, time.monotonic() - 5)   # created 5 s ago
        await _jarves(lv, "אוקיי.")
        await lv.handle_event({"type": "response.event", "delegation_id": "d1",
                               "event": {"type": "response.created", "response": {"id": "r1"}}})
        await _user(lv, "כמה זה שתיים ועוד שתיים")                     # the user moved on (barge-in)
        lv._last_speech_t = time.monotonic() + 1                          # JARVES answered the new turn
        lv._backend_t0 = time.monotonic() - 2
        await asyncio.sleep(1.5)
        assert not [x for x in sent.items if x[0] == "commentary"]
        assert not retried and lv._stats.get("backend_stalls") == 1
    finally:
        await _cleanup(lv)


async def test_cv09_barge_in_over_server_stopped_speech_is_counted():
    lv, _ = make_live(half_duplex=False)
    try:
        lv._voice_onset, lv._onset_over_speech = time.monotonic(), True
        await _user(lv, "רגע, עצור")
        assert lv._stats["barge_ins"] == 1
        await _user(lv, " עוד")                                            # same utterance: not counted twice
        assert lv._stats["barge_ins"] == 1
    finally:
        await _cleanup(lv)


# ------------------------------------------------------------------ CV-14 / CV-08 / CV-16
async def test_cv14_whatsapp_draft_goes_straight_to_the_single_approval(tmp_path):
    from jarves.whatsapp import WhatsAppSkill
    sk = WhatsAppSkill(tmp_path, opener=lambda u: True)
    tools = {t.name: t for t in sk.tools()}
    res = await tools["whatsapp_draft"].handler(tools["whatsapp_draft"].args(text="אני מאחר", name="דני",
                                                                       phone="054-1234567"))
    assert res.ok and "?" not in res.message                       # no question of its own
    assert "call whatsapp_open" in res.data["rule"] and "ONE yes/no" in res.data["rule"]
    assert "do not ask yourself" in tools["whatsapp_open"].description


def test_cv08_backend_never_searches_for_arithmetic():
    assert "Never web search for arithmetic" in brain_mod.INSTRUCTIONS


def test_cv16_name_prefix_rule():
    text = front_instructions()
    assert "לְאביטל" in text and 'never "לביטל"' in text and "לְצחי" in text
