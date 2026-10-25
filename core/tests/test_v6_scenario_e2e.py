"""V6 #222: end-to-end scenario with fakes only: emulated phone + simulated car audio. No device, no paid call.

Story: the phone joins the car (HFP route), the owner speaks a command through the real phone endpoints, the reply is
spoken to the car speaker, the Bluetooth link drops and the phone resends the same command (lost answer), then the PC
resumes the very same conversation. Acceptance: desktop resume, no duplicate command, stop within 1 s."""
from __future__ import annotations

import asyncio
import time

import pytest
from fixtures.car_audio import CAR_HFP, BUILTIN_SPEAKER, FakeSoundDevice, run_scenario

from jarves.voice.audio import AudioUnavailable, StreamingAudio

from test_v5_phone_resume import desktop_resume_last, desktop_say, phone_say, stored_sessions
from test_web_chat import dev_req, pair
from test_web_chat import rig  # noqa: F401  (fixture)


@pytest.fixture
def fake(monkeypatch):
    return FakeSoundDevice().install(monkeypatch)


def test_phone_command_survives_a_car_bluetooth_drop_and_resumes_on_the_desktop_once(rig, fake):
    r = rig(mobile_on=True)
    tok, _ = pair(r.dash)
    run_scenario(fake, "car_connect")
    assert fake.default_in == fake.default_out == CAR_HFP

    first = phone_say(r, tok, "navigate to the office", "scn-msg-0001")
    spoken = asyncio.run(_speak_then_drop(fake, first["text"]))
    assert spoken >= 1 and fake.played[CAR_HFP] > 0          # the reply reached the car speaker before the drop

    # The answer was lost with the link: the phone resends the same id -> same turn, NOT a second command.
    st, again = dev_req(r.dash, "POST", "/api/m/chat/send", token=tok,
                        body={"text": "navigate to the office", "id": "scn-msg-0001"})
    assert st == 200, again
    assert [t for role, t in r.agent.history if role == "user"] == ["navigate to the office"]

    sessions = desktop_resume_last(r)                          # the PC opens the same conversation
    assert sessions[0]["first"] == "navigate to the office" and sessions[0]["turns"] == 1
    desktop_say(r, "and park nearby")
    assert [t for role, t in r.agent.history if role == "user"] == ["navigate to the office", "and park nearby"]
    assert len(stored_sessions(r.home)) == 1 and stored_sessions(r.home)[0][1] == 4


async def _speak_then_drop(fake, text: str) -> int:
    audio = StreamingAudio()
    player = asyncio.create_task(audio.run_player(24000))
    audio.enqueue(b"\0\0" * 2400)
    await asyncio.sleep(0.25)
    audio.enqueue(b"\0\0" * 24000)
    fake.drop_bluetooth()
    with pytest.raises(AudioUnavailable) as e:                 # the lost route is reported, not swallowed
        await asyncio.wait_for(player, 2)
    assert e.value.code == "AUDIO_SPEAKER_UNAVAILABLE"
    audio.close()
    return len(text)


async def test_stop_silences_the_car_speaker_and_closes_the_mic_within_one_second(fake, monkeypatch):
    from fixtures.car_audio import fake_sd
    real_write = fake_sd._OutputStream.write

    def paced_write(self, data):                                # real hardware plays in real time; the fake is instant
        time.sleep(0.1)
        real_write(self, data)

    monkeypatch.setattr(fake_sd._OutputStream, "write", paced_write)
    fake.connect_car()
    audio = StreamingAudio()
    player = asyncio.create_task(audio.run_player(24000))
    audio.enqueue(b"\0\0" * 24000 * 30)                         # 30 s of reply queued
    await asyncio.sleep(0.05)
    t0 = time.monotonic()
    assert audio.flush() > 0                                    # most of the reply was still queued
    audio.flush()                                               # "stop": barge-in discards what is queued
    assert audio.queued_s() == 0
    audio.close()
    await asyncio.wait_for(player, 1)
    assert time.monotonic() - t0 < 1.0
    assert fake.played[CAR_HFP] < 24000 * 2 * 30                # it did not play the whole reply
    assert fake.open_mics == 0 and BUILTIN_SPEAKER not in fake.played
