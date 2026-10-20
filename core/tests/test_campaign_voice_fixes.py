"""SW-V3 campaign PASS C (voice) fixes (#17): spoken-approval hint, curator facts hidden in privacy mode."""
from __future__ import annotations

import asyncio
import json

from jarves import logs
from jarves.skills import SkillRegistry
from jarves.voice import live as live_mod
from jarves.voice.live import LiveVoice


class _Commentary:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def append(self, content, delegation_id=None, event_id=None):
        self.sent.append(content)


class _Conn:
    def __init__(self) -> None:
        self.session = type("S", (), {})()
        self.session.commentary = _Commentary()


def _note_for(voice: bool, monkeypatch) -> str:
    monkeypatch.setattr(live_mod, "CONFIRM_NOTE_AFTER_S", 0.01)
    lv = LiveVoice(SkillRegistry(), lambda: "backend")
    lv.running, lv.conn = True, _Conn()

    async def run():
        await lv.confirm_pending("לנקות 3 פריטים?", voice=voice)
        await asyncio.sleep(0.2)
    asyncio.run(run())
    return lv.conn.session.commentary.sent[0]


def test_voice_answerable_approval_tells_the_user_to_say_yes_or_no(monkeypatch):
    note = _note_for(True, monkeypatch)
    assert note == live_mod.CONFIRM_NOTE_VOICE and "כן או לא" in note


def test_screen_only_approval_keeps_the_screen_hint(monkeypatch):
    assert _note_for(False, monkeypatch) == live_mod.CONFIRM_NOTE


def test_curator_facts_are_not_logged_in_privacy_mode(tmp_path):
    from jarves.curator import Curator
    from jarves.memory import MemoryStore
    path = logs.setup(tmp_path / "logs", "DEBUG", 1_000_000, 2, False, privacy=True)
    mem = MemoryStore(tmp_path / "memory.db")
    try:
        cur = Curator(mem, None, enabled=lambda k: True)
        cur.observe("user", "קוראים לי דניאל ואני גר ברחובות")
        cur.observe("assistant", "נעים מאוד")
        saved = asyncio.run(cur.run("shutdown"))
        assert saved, "the rules should learn the name"
    finally:
        mem.close()
    raw = path.read_text(encoding="utf-8")
    assert "דניאל" not in raw and "רחובות" not in raw
    done = [json.loads(l) for l in raw.splitlines() if '"CURATOR_DONE"' in l]
    assert all("facts" not in e for e in done)
