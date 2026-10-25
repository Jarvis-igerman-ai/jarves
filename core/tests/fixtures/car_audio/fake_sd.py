"""A deterministic stand-in for the `sounddevice` module with a car-audio flavour.

Devices: laptop/phone built-ins, a car kit over HFP/SCO (two-way, narrowband), a car A2DP sink (output only, stereo)
and a USB headset. Events: plug / unplug, route switch, Bluetooth drop, audio-focus loss/gain. Time is explicit:
tests call `pump()` to deliver one mic chunk; nothing runs on a thread, nothing touches a real device, no audio is
ever recorded to disk. `install(monkeypatch)` puts it where `import sounddevice` finds it.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass


class PortAudioError(Exception):
    """What sounddevice raises for a vanished or invalid device (name-compatible)."""


BUILTIN_MIC, BUILTIN_SPEAKER = "Built-in Microphone", "Built-in Speaker"
CAR_HFP, A2DP, USB_HEADSET = "Car Kit (HFP)", "Car Stereo (A2DP)", "USB Headset"


@dataclass
class FakeDevice:
    name: str
    max_in: int = 0
    max_out: int = 0
    kind: str = "builtin"           # builtin | hfp | a2dp | usb
    present: bool = True


def default_devices() -> list[FakeDevice]:
    return [FakeDevice(BUILTIN_MIC, max_in=1, kind="builtin"), FakeDevice(BUILTIN_SPEAKER, max_out=2, kind="builtin"),
            FakeDevice(CAR_HFP, max_in=1, max_out=1, kind="hfp", present=False),
            FakeDevice(A2DP, max_out=2, kind="a2dp", present=False),
            FakeDevice(USB_HEADSET, max_in=1, max_out=2, kind="usb", present=False)]


class _Stream:
    def __init__(self, owner: "FakeSoundDevice", device: FakeDevice) -> None:
        self.owner, self.device, self.closed, self.started = owner, device, False, False

    @property
    def active(self) -> bool:
        return self.started and not self.closed and self.device.present

    def __enter__(self):
        self.started = True
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def abort(self) -> None:
        self.started = False

    def close(self) -> None:
        if not self.closed:
            self.closed, self.started = True, False
            self.owner._closed(self)


class _InputStream(_Stream):
    def __init__(self, owner, device, callback, blocksize: int) -> None:
        super().__init__(owner, device)
        self.callback, self.blocksize = callback, blocksize


class _OutputStream(_Stream):
    def write(self, data) -> None:
        if self.closed or not self.device.present:
            raise PortAudioError(f"Unanticipated host error: {self.device.name} is gone")
        self.owner.played[self.device.name] = self.owner.played.get(self.device.name, 0) + len(bytes(data))


class FakeSoundDevice:
    """sounddevice-compatible subset used by jarves.voice.audio: Raw{Input,Output}Stream, query_devices."""

    PortAudioError = PortAudioError

    def __init__(self, tone: bytes | None = None) -> None:
        self.devices = default_devices()
        self.default_in, self.default_out = BUILTIN_MIC, BUILTIN_SPEAKER
        self.focus = True                                   # audio focus held (speech); False = another app has it
        self.events: list[tuple[str, str]] = []             # ("unplug", name), ("switch", name) ...
        self.mic_opens = 0                                  # how many times a mic stream was opened (fail-safe tests)
        self.speaker_opens = 0
        self.played: dict[str, int] = {}                    # bytes written per output device
        self._tone = tone
        self._inputs: list[_InputStream] = []
        self._outputs: list[_OutputStream] = []

    # ---- sounddevice API ------------------------------------------------------------------------
    def _find(self, device, want_in: bool) -> FakeDevice:
        if device is None:
            device = self.default_in if want_in else self.default_out
        for i, d in enumerate(self.devices):
            if device in (i, d.name):
                if not d.present:
                    raise PortAudioError(f"Error querying device {device!r}: {d.name} is not connected")
                if (d.max_in if want_in else d.max_out) < 1:
                    raise PortAudioError(f"Invalid {'input' if want_in else 'output'} device: {d.name}")
                return d
        raise PortAudioError(f"Error querying device {device!r}")

    def query_devices(self, device=None, kind=None):
        if device is None and kind is None:
            return [self._info(d) for d in self.devices if d.present]
        return self._info(self._find(device, kind != "output"))

    @staticmethod
    def _info(d: FakeDevice) -> dict:
        return {"name": d.name, "max_input_channels": d.max_in, "max_output_channels": d.max_out}

    def RawInputStream(self, samplerate=16000, channels=1, dtype="int16", blocksize=1600, device=None,
                       callback=None, **kw):
        d = self._find(device, True)
        s = _InputStream(self, d, callback, blocksize)
        self._inputs.append(s)
        self.mic_opens += 1
        self.events.append(("mic_open", d.name))
        return s

    def RawOutputStream(self, samplerate=24000, channels=1, dtype="int16", device=None, **kw):
        d = self._find(device, False)
        s = _OutputStream(self, d)
        self._outputs.append(s)
        self.speaker_opens += 1
        self.events.append(("speaker_open", d.name))
        return s

    def _closed(self, s) -> None:
        self.events.append(("mic_close" if isinstance(s, _InputStream) else "speaker_close", s.device.name))

    # ---- test-side events -------------------------------------------------------------------------
    def dev(self, name: str) -> FakeDevice:
        return next(d for d in self.devices if d.name == name)

    def plug(self, name: str) -> None:
        self.dev(name).present = True
        self.events.append(("plug", name))

    def unplug(self, name: str) -> None:
        """The device disappears: open streams on it die; a default pointing at it falls back to the built-ins."""
        self.dev(name).present = False
        self.events.append(("unplug", name))
        if self.default_in == name:
            self.default_in = BUILTIN_MIC
        if self.default_out == name:
            self.default_out = BUILTIN_SPEAKER

    def switch_route(self, name: str) -> None:
        """Make `name` the default for the directions it supports. Streams opened earlier stay on their device."""
        d = self.dev(name)
        if not d.present:
            raise PortAudioError(f"{name} is not connected")
        if d.max_in:
            self.default_in = name
        if d.max_out:
            self.default_out = name
        self.events.append(("switch", name))

    def connect_car(self) -> None:
        """Phone joins the car: HFP (two-way) + A2DP; the call route becomes the car kit."""
        self.plug(CAR_HFP)
        self.plug(A2DP)
        self.switch_route(CAR_HFP)

    def drop_bluetooth(self) -> None:
        """Car Bluetooth link drops (car off, out of range): HFP and A2DP vanish at once."""
        self.unplug(CAR_HFP)
        self.unplug(A2DP)

    def focus_loss(self) -> None:
        """Another app (a phone call, navigation prompt) takes audio focus: the mic delivers nothing until gain."""
        self.focus = False
        self.events.append(("focus_loss", ""))

    def focus_gain(self) -> None:
        self.focus = True
        self.events.append(("focus_gain", ""))

    # ---- time -------------------------------------------------------------------------------------
    def pump(self, n: int = 1, level: float = 0.1) -> int:
        """Deliver `n` mic chunks to every live input stream. Returns the number of chunks delivered."""
        delivered = 0
        for _ in range(n):
            for s in list(self._inputs):
                if s.active and self.focus and s.callback is not None:
                    s.callback(self._chunk(s.blocksize, level), s.blocksize, None, None)
                    delivered += 1
        return delivered

    def _chunk(self, frames: int, level: float) -> bytes:
        if self._tone is not None:
            return (self._tone * (frames * 2 // len(self._tone) + 1))[:frames * 2]
        import numpy as np
        t = np.arange(frames)
        return (np.sin(t * 2 * np.pi * 220 / 16000) * level * 32767).astype("<i2").tobytes()

    @property
    def open_mics(self) -> int:
        return sum(1 for s in self._inputs if not s.closed)

    # ---- plumbing ---------------------------------------------------------------------------------
    def install(self, monkeypatch) -> "FakeSoundDevice":
        """Make `import sounddevice` return this fake for the test (pytest monkeypatch fixture)."""
        monkeypatch.setitem(sys.modules, "sounddevice", self)
        return self
