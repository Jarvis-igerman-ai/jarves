"""Conversation quality after the user's 19 Oct live session (docs/reviews/CONVERSATION_LOG_REVIEW_19OCT.md):
no bare "סגור" acknowledgements, the Hebrew-written name, a short beat before the greeting, warmer emotion cues."""
import asyncio
import re
import time
from pathlib import Path

from jarves.app import Core
from jarves.config import Config, VoiceConfig
from jarves.emotion import detect_emotion
from jarves.skills import SkillRegistry
from jarves.voice import live as live_mod
from jarves.voice.live import LiveVoice, front_instructions

SRC = Path(live_mod.__file__).resolve().parents[1]
# a one-word ack the model said after EVERY action ("בטח. סוגר.", "סגור.") and the user could not understand
BARE_ACK = re.compile(r"(?<![א-ת])(?:סגור|סוגר|בוצע)(?![א-ת])")


def _hebrew_literals(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    groups = re.findall(r'"""(.*?)"""|"([^"\n]*)"|\'([^\'\n]*)\'', text, re.S)
    return [s for g in groups for s in g if s and re.search(r"[א-ת]", s)]


def test_no_bare_ack_word_in_instructions_or_canned_phrases():
    spoken = [front_instructions(), live_mod.PROGRESS_NOTE, live_mod.PROGRESS_NOTE_AGAIN, live_mod.CONFIRM_NOTE]
    lv = LiveVoice(SkillRegistry(), lambda: "backend", greeting="שלום!")
    spoken.append(lv.session_config()["input"][0]["content"][0]["text"])
    for text in spoken:
        assert not BARE_ACK.search(text), text
    # every Hebrew string the Core can say or hand to the model (skills results, notes, brain prompts)
    for rel in ("voice/live.py", "app.py", "brain.py", "chatgpt_brain.py", "skills/music.py", "skills/builtin.py"):
        for lit in _hebrew_literals(SRC / rel):
            assert not BARE_ACK.search(lit), f"{rel}: {lit[:80]}"


def test_actions_are_confirmed_with_the_real_result():
    text = front_instructions()
    assert "real result" in text and "never your own estimate" in text
    assert "הגברתי לשלושים אחוז" in text                     # an example that carries the actual value
    assert "bare acknowledgement" in text


def test_spoken_name_is_hebrew_never_latin():
    text = front_instructions("JARVES", "ג'ארוויס")
    assert text.startswith("You are ג'ארוויס") and 'ALWAYS say "ג\'ארוויס"' in text
    lv = LiveVoice(SkillRegistry(), lambda: "backend", name="JARVES", greeting="שלום! אני ג'ארוויס.")
    greet = lv.session_config()["input"][0]["content"][0]["text"]
    assert "ג'ארוויס" in greet and "JARVES" not in greet


def test_hello_text_uses_configurable_spoken_name(home):
    cfg = Config()
    assert cfg.assistant_name_spoken == "ג'ארוויס" and VoiceConfig().greeting_delay_s == 0.5
    core = Core(cfg, home, console_logs=False, use_real_audio=False)
    assert core.hello_text(question=True) == "שלום! אני ג'ארוויס. איך אפשר לעזור?"
    cfg.assistant_name_spoken = "ג'רי"
    assert "ג'רי" in core.hello_text() and not re.search(r"[A-Za-z]", core.hello_text())


async def test_greeting_is_presented_after_the_configured_beat(home):
    cfg = Config()
    cfg.voice.greeting_delay_s = 0.2
    core = Core(cfg, home, console_logs=False, use_real_audio=False)
    presented = []

    class Live:
        def present(self):
            presented.append(time.monotonic())

    core.live = live = Live()
    t0 = time.monotonic()
    await core._present_after_delay(live)
    assert presented and presented[0] - t0 >= 0.19
    core.live = None                                         # a session replaced meanwhile is not presented
    await core._present_after_delay(live)
    assert len(presented) == 1


def test_warmer_tone_words_reach_the_face():
    assert detect_emotion("וואו, מזל טוב! שמח בשבילך", "jarves") == "love"
    assert detect_emotion("אוי לא, זה לא נעים", "jarves") == "sad"
    assert detect_emotion("אני כאן בשבילך", "jarves") == "sad"
    assert detect_emotion("אוי לא", "user") is None             # role-specific: the user's "oh no" stays neutral
