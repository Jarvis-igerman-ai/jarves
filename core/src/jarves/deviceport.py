"""DevicePort: the single boundary between Core behaviour and I/O (docs/DEVICE_CONTRACT.md).

Outbound: face.set_state (Godot display adapter), speaker play/stop/volume, mic capture.
Inbound (from the Godot simulator now, GPIO/USB adapters later): button.down/up, mic.mute_changed,
presence.changed, light.changed, network.changed.
"""
from __future__ import annotations

import numpy as np

from . import logs
from .health import Health
from .link import GodotLink
from .voice.audio import AudioDevice, AudioUnavailable

FACE_STATES = ("idle", "listening", "thinking", "speaking", "happy", "error")
INBOUND = ("button.down", "button.up", "mic.mute_changed", "presence.changed", "light.changed", "network.changed")


class DevicePort:
    def __init__(self, audio: AudioDevice | None, link: GodotLink, health: Health) -> None:
        self.audio, self.link, self.health = audio, link, health
        self.face_state = "idle"
        self.mic_muted = False         # SOFTWARE mute; not a hardware privacy guarantee
        self.speaker_volume = 0.8

    def check_audio(self) -> bool:
        if self.audio is None:
            self.health.set("audio", "disabled", "voice disabled in config")
            return False
        ok, detail = self.audio.check()
        if ok:
            self.health.set("audio", "ok", detail)
        else:
            self.health.set("audio", "unavailable", detail, "connect a mic/speaker; text input still works")
        return ok

    async def face_set_state(self, state: str) -> None:
        if state not in FACE_STATES:
            raise ValueError(f"unknown face state {state}")
        self.face_state = state
        await self.link.send_face("face.set_state", {"state": state})

    def set_mic_muted(self, muted: bool) -> None:
        self.mic_muted = muted
        if muted and self.audio is not None:
            self.audio.stop_capture()
        self.health.set("mic", "disabled" if muted else "ok",
                        "software mute ON (not a hardware privacy switch)" if muted else "software mute off")
        logs.info("device", "MIC_MUTE_CHANGED", "software mic mute changed", muted=muted)

    def mic_start(self) -> None:
        if self.mic_muted:
            raise AudioUnavailable("MIC_MUTED", "the microphone is muted", "unmute to talk")
        if self.audio is None:
            raise AudioUnavailable("AUDIO_DISABLED", "voice is disabled", "type instead, or enable voice in config")
        self.audio.start_capture()

    def mic_stop(self) -> np.ndarray:
        return self.audio.stop_capture() if self.audio is not None else np.zeros(0, np.float32)

    async def speaker_play(self, samples: np.ndarray, rate: int) -> bool:
        if self.audio is None:
            return False
        return await self.audio.play(samples * self.speaker_volume, rate)

    def speaker_stop(self) -> None:
        if self.audio is not None:
            self.audio.stop_playback()

    async def speaker_play_stream(self, chunks) -> bool:
        if self.audio is None:
            return False

        async def scaled():
            async for samples, rate in chunks:
                yield samples * self.speaker_volume, rate

        if hasattr(self.audio, "play_stream"):
            return await self.audio.play_stream(scaled())
        async for samples, rate in scaled():
            if not await self.audio.play(samples, rate):
                return False
        return True

    def speaker_set_volume(self, v: float) -> None:
        self.speaker_volume = max(0.0, min(1.0, float(v)))
