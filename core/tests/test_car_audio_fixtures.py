"""The simulated car-audio fixtures themselves (V6 #214): no hardware, deterministic, drive the real StreamingAudio."""
import asyncio

import pytest
from fixtures.car_audio import (A2DP, BUILTIN_MIC, BUILTIN_SPEAKER, CAR_HFP, SCENARIOS, FakeSoundDevice,
                                PortAudioError, run_scenario)

from jarves.voice.audio import StreamingAudio


@pytest.fixture
def fake(monkeypatch):
    return FakeSoundDevice().install(monkeypatch)


def test_defaults_are_builtins_and_car_devices_start_absent(fake):
    assert fake.default_in == BUILTIN_MIC and fake.default_out == BUILTIN_SPEAKER
    assert [d["name"] for d in fake.query_devices()] == [BUILTIN_MIC, BUILTIN_SPEAKER]
    with pytest.raises(PortAudioError):
        fake.query_devices(CAR_HFP, "input")


def test_connect_car_routes_both_directions_to_hfp_and_a2dp_has_no_mic(fake):
    fake.connect_car()
    assert fake.default_in == fake.default_out == CAR_HFP
    with pytest.raises(PortAudioError):
        fake.query_devices(A2DP, "input")          # A2DP is one-way stereo


def test_unplug_falls_back_to_builtins_and_kills_open_streams(fake):
    fake.connect_car()
    got = []
    s = fake.RawInputStream(device=None, callback=lambda *a: got.append(a[0]))
    with s:
        assert fake.pump(2) == 2 and len(got) == 2
        fake.drop_bluetooth()
        assert fake.pump(2) == 0 and not s.active
    assert fake.default_in == BUILTIN_MIC and fake.default_out == BUILTIN_SPEAKER


def test_output_write_to_vanished_device_raises(fake):
    fake.connect_car()
    out = fake.RawOutputStream(device=None)
    out.write(b"\0\0" * 10)
    assert fake.played[CAR_HFP] == 20
    fake.drop_bluetooth()
    with pytest.raises(PortAudioError):
        out.write(b"\0\0")


def test_focus_loss_silences_the_mic_until_gain(fake):
    got = []
    with fake.RawInputStream(callback=lambda *a: got.append(1)):
        fake.focus_loss()
        assert fake.pump(3) == 0
        fake.focus_gain()
        assert fake.pump(1) == 1


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_every_scenario_runs_without_hardware(fake, name):
    run_scenario(fake, name)
    assert fake.mic_opens == 0 and fake.speaker_opens == 0      # events never open a stream by themselves


async def test_streaming_audio_reads_the_fake_mic_and_plays_to_the_fake_speaker(fake):
    audio = StreamingAudio()
    chunk = 4800                                    # 100 ms of 24 kHz PCM16

    async def read(n):
        out = []
        async for c in audio.mic_chunks(24000, chunk):
            out.append(c)
            if len(out) == n:
                audio.close()
        return out

    task = asyncio.create_task(read(3))
    await asyncio.sleep(0.01)
    assert fake.open_mics == 1
    fake.pump(3)
    chunks = await asyncio.wait_for(task, 2)
    assert len(chunks) == 3 and all(len(c) == chunk for c in chunks)
    assert fake.open_mics == 0                      # closing the session closed the mic

    spk = StreamingAudio()
    player = asyncio.create_task(spk.run_player(24000))
    spk.enqueue(b"\0\0" * 4800)
    await asyncio.sleep(0.2)
    spk.close()
    await asyncio.wait_for(player, 2)
    assert fake.played[BUILTIN_SPEAKER] == 9600
