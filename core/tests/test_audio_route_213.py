"""V6 #213: audio route / device-change handling, driven by the simulated car-audio fixtures (#214).

The conversation must (a) behave exactly as before when nothing changes (same assertions on the old FakeAudio and on
the real StreamingAudio over a fake sounddevice), (b) end gracefully, truthfully and without a crash when the
microphone or speaker disappears, and (c) never open the microphone again by itself.
"""
import asyncio

import pytest
from fixtures.car_audio import A2DP, CAR_HFP, USB_HEADSET, FakeSoundDevice, run_scenario
from test_voice_faults import FakeAudio, FakeClient, Rec, loud, make_live, settle

from jarves.voice.audio import AudioUnavailable, StreamingAudio

STALL = 0.3


@pytest.fixture
def fake(monkeypatch):
    return FakeSoundDevice().install(monkeypatch)


async def pumper(fake, every=0.01):
    """Stands in for the PortAudio callback thread: one mic chunk every `every` seconds while the device lives."""
    while True:
        fake.pump(1)
        await asyncio.sleep(every)


def make_audio(kind, fake=None):
    return FakeAudio() if kind == "fake" else StreamingAudio(mic_stall_s=STALL)


# ---------------------------------------------------------------- (a) nothing changes: identical on both audio paths
@pytest.mark.parametrize("kind", ["fake", "streaming"])
async def test_normal_conversation_is_identical_without_a_device_change(kind, fake):
    rec, client, audio = Rec(), FakeClient(), make_audio(kind)
    lv = make_live(rec, client, audio)
    task = asyncio.create_task(pumper(fake))
    await lv.start()
    await asyncio.sleep(STALL * 3)                  # longer than the stall window: a healthy mic never trips it
    assert lv.running and rec.ends == [] and rec.health[-1][0] == "ok"
    assert client.conn.mic_frames > 5
    await lv.stop("user")
    task.cancel()
    assert rec.ends == ["user"] and rec.states[-1] == "idle" and audio.closed == 1 if kind == "fake" else rec.ends == ["user"]
    assert lv.conn is None and lv._tasks == []
    if kind == "streaming":
        assert fake.open_mics == 0 and fake.mic_opens == 1


@pytest.mark.parametrize("kind", ["fake", "streaming"])
async def test_speech_still_plays_without_a_device_change(kind, fake):
    rec, client, audio = Rec(), FakeClient(), make_audio(kind)
    lv = make_live(rec, client, audio)
    task = asyncio.create_task(pumper(fake))
    await lv.start()
    audio.enqueue(loud(4800))
    await asyncio.sleep(0.25)
    await lv.stop("user")
    task.cancel()
    assert rec.ends == ["user"]
    if kind == "streaming":
        assert fake.played["Built-in Speaker"] > 0


# ---------------------------------------------------------------- (b) the device goes away
async def start_in_car(fake):
    fake.connect_car()
    rec, client, audio = Rec(), FakeClient(), StreamingAudio(mic_stall_s=STALL)
    lv = make_live(rec, client, audio)
    task = asyncio.create_task(pumper(fake))
    await lv.start()
    await asyncio.sleep(0.1)
    assert client.conn.mic_frames > 0 and fake.events.count(("mic_open", CAR_HFP)) == 1
    return rec, client, audio, lv, task


def assert_graceful(rec, lv, audio, fake):
    assert rec.ends == ["audio_route_lost"]
    assert rec.states[-1] == "idle"
    assert lv.running is False and lv.conn is None and lv._tasks == []
    status, detail, action = rec.health[-1]
    assert status == "degraded"
    assert "stopped" in detail                                 # says what happened
    assert "push-to-talk" in action and "wake word" in action   # says what to do
    assert audio._open is False and fake.open_mics == 0         # everything closed


@pytest.mark.parametrize("scenario_tail", ["bluetooth_drop", "unplug_hfp_only", "focus_loss"])
async def test_mic_route_lost_mid_conversation_ends_gracefully(scenario_tail, fake):
    rec, client, audio, lv, task = await start_in_car(fake)
    {"bluetooth_drop": fake.drop_bluetooth, "unplug_hfp_only": lambda: fake.unplug(CAR_HFP),
     "focus_loss": fake.focus_loss}[scenario_tail]()
    await settle(lv, STALL + 2)
    task.cancel()
    assert_graceful(rec, lv, audio, fake)


async def test_speaker_route_lost_mid_conversation_ends_gracefully(fake):
    fake.plug(A2DP)
    fake.switch_route(A2DP)                                     # output on the car stereo, mic stays built-in
    rec, client, audio = Rec(), FakeClient(), StreamingAudio(mic_stall_s=STALL)
    lv = make_live(rec, client, audio)
    task = asyncio.create_task(pumper(fake))
    await lv.start()
    audio.enqueue(loud(4800))
    await asyncio.sleep(0.2)
    assert fake.played[A2DP] > 0
    fake.unplug(A2DP)
    audio.enqueue(loud(4800))
    await settle(lv, 2)
    task.cancel()
    assert rec.ends == ["audio_route_lost"] and rec.health[-1][0] == "degraded"
    assert "speaker" in rec.health[-1][1] and "push-to-talk" in rec.health[-1][2]
    assert lv._tasks == [] and fake.open_mics == 0


@pytest.mark.parametrize("name", ["bluetooth_drop_and_return", "headset_unplug_replug", "incoming_call_focus_loss"])
async def test_replug_or_return_never_reopens_the_mic_by_itself(name, fake):
    fake.plug(USB_HEADSET) if name == "headset_unplug_replug" else None
    fake.connect_car()
    rec, client, audio = Rec(), FakeClient(), StreamingAudio(mic_stall_s=STALL)
    lv = make_live(rec, client, audio)
    task = asyncio.create_task(pumper(fake))
    await lv.start()
    await asyncio.sleep(0.1)
    opens_before = fake.mic_opens
    # the route drops in the middle of the call, then comes back: the scenario's own steps do both
    if name == "bluetooth_drop_and_return":
        fake.drop_bluetooth()
        fake.connect_car()
    elif name == "headset_unplug_replug":
        fake.unplug(CAR_HFP)
        fake.plug(CAR_HFP)
        fake.switch_route(CAR_HFP)
    else:
        fake.focus_loss()
    await settle(lv, STALL + 2)
    fake.focus_gain()
    fake.connect_car()
    await asyncio.sleep(STALL)                                  # time for anything to (wrongly) reopen
    task.cancel()
    assert rec.ends == ["audio_route_lost"]
    assert fake.mic_opens == opens_before and fake.open_mics == 0


async def test_mic_missing_at_open_is_unavailable_and_opens_nothing(fake):
    audio = StreamingAudio(input_device=CAR_HFP, mic_stall_s=STALL)         # the car kit is not connected
    with pytest.raises(AudioUnavailable) as e:
        async for _ in audio.mic_chunks(24000, 4800):
            pass
    assert e.value.code == "AUDIO_MIC_UNAVAILABLE" and fake.mic_opens == 0


async def test_stall_detection_can_be_switched_off(fake):
    audio = StreamingAudio(mic_stall_s=0)
    got = []

    async def read():
        async for c in audio.mic_chunks(24000, 4800):
            got.append(c)

    t = asyncio.create_task(read())
    await asyncio.sleep(STALL * 2)                              # nothing delivered, no exception
    assert not t.done()
    fake.pump(1)
    await asyncio.sleep(0.05)
    audio.close()
    await asyncio.wait_for(t, 1)
    assert len(got) == 1


async def test_stall_detection_is_off_by_default_so_the_wake_listener_keeps_its_own_guard(fake):
    # the wake listener builds StreamingAudio() and wraps it in runtime.stall_guard (configurable, 0 = off, it
    # re-opens the mic): the conversation-only 5 s window must not pre-empt it or override the owner's 0
    audio = StreamingAudio()
    assert audio.mic_stall_s == 0
    t = asyncio.create_task(audio.mic_chunks(24000, 4800).__anext__())
    await asyncio.sleep(STALL * 3)
    assert not t.done()
    audio.close()
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(t, 1)


async def test_slow_first_frame_gets_double_grace(fake):
    # Bluetooth HFP/SCO set-up can delay the first frame: 1.5x the window before any frame is not a loss
    audio = StreamingAudio(mic_stall_s=STALL)
    t = asyncio.create_task(audio.mic_chunks(24000, 4800).__anext__())
    await asyncio.sleep(STALL * 1.5)
    assert not t.done()
    fake.pump(1)
    assert len(await asyncio.wait_for(t, 1)) == 4800
    audio.close()


async def test_a_level_callback_failure_is_not_reported_as_a_lost_speaker(fake):
    async def broken(level):
        raise ValueError("ui link down")

    audio = StreamingAudio(on_output_level=broken, mic_stall_s=STALL)
    audio.enqueue(loud(4800))
    with pytest.raises(ValueError):
        await asyncio.wait_for(audio.run_player(24000), 1)
    assert audio.stats()["route_losses"] == 0


def test_scenarios_leave_no_stream_open(fake):
    for name in ("car_connect", "bluetooth_drop_mid_conversation"):
        run_scenario(fake, name)
    assert fake.open_mics == 0
