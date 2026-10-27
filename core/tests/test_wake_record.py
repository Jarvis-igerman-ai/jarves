"""`jarvis wake record` (voice/wake_record.py): hermetic, fake sounddevice, fake level control, frozen clock."""
from __future__ import annotations

import numpy as np

from jarves.voice import wake_prod, wake_record as wr

RATE = wr.RATE


class FakeSD:
    """sd.rec returns scripted audio: `script[device]` is a callable (seconds) -> float32 audio in [-1, 1]."""

    def __init__(self, devices, script, default=0):
        self.devices, self.script, self.default = devices, script, default
        self.calls = []

    def query_devices(self, device=None, kind=None):
        if kind == "input":
            return self.devices[self.default]
        return self.devices

    def query_hostapis(self, i):
        return {"name": ["MME", "WASAPI"][i]}

    def rec(self, n, samplerate, channels, dtype, device):
        self.calls.append(device)
        fn = self.script[device]
        if isinstance(fn, Exception):
            raise fn
        x = fn(n / samplerate)
        return (np.clip(x, -1, 1) * 32767).astype(np.int16).reshape(-1, 1)

    def wait(self):
        pass


def dev(name, api=0, ch=1):
    return {"name": name, "max_input_channels": ch, "hostapi": api}


def quiet(sec, level=0.0005, seed=1):
    return np.random.default_rng(seed).normal(0, level, int(sec * RATE)).astype(np.float32)


def speech(sec, amp=0.05, seed=1):
    t = np.arange(int(sec * RATE)) / RATE
    x = quiet(sec, seed=seed)
    mid = (t > 0.6) & (t < 1.6)
    x[mid] += amp * np.sin(2 * np.pi * 220 * t[mid]) * np.random.default_rng(2).uniform(0.7, 1.0, mid.sum()) * (1 + seed * 1e-4)
    return x.astype(np.float32)


def saturated(sec):
    return np.sign(np.sin(np.arange(int(sec * RATE)) / 20.0)).astype(np.float32) * 0.99


class Level:
    def __init__(self, v=0.8):
        self.v, self.sets = v, []

    def available(self):
        return True

    def get(self):
        return self.v

    def set(self, v):
        self.v = round(v, 3)
        self.sets.append(self.v)
        return self.v


def deps(sd, **kw):
    out, sleeps = [], []
    d = wr.Deps(sd=sd, ask=lambda p: out.append("ASK " + p) or "", say=out.append, sleep=sleeps.append,
                clock=lambda: 1_700_000_000.0, default_mic=lambda: "Microphone Array", **kw)
    d.out, d.sleeps = out, sleeps
    return d


DEVICES = [dev("Microsoft Sound Mapper - Input"), dev("Microphone Array (Realtek)"), dev("2K HD Camera Microphone"),
           dev("Speakers", ch=0), dev("Microphone Array (Realtek) wasapi", api=1)]


def test_thresholds_come_from_the_sample_checker():
    assert wr.HARD_MIN == wake_prod.MIN_RMS_DB - 1.0 == -43.0
    assert wr.MIN_SNR >= wake_prod.MIN_SNR_DB and wr.CLIP_FRACTION >= wake_prod.CLIP_FRACTION
    assert wr.CLIP_FRACTION == 0.003 and wr.MIN_SNR == 20.0


def test_input_devices_mme_only_no_mapper():
    sd = FakeSD(DEVICES, {})
    assert [n for _, n in wr.input_devices(sd)] == ["Microphone Array (Realtek)", "2K HD Camera Microphone"]


def test_probe_labels_and_recommendation():
    sd = FakeSD(DEVICES, {1: lambda s: quiet(s), 2: saturated})
    rows = wr.probe_all(sd)
    assert [r.verdict for r in rows] == ["good", "saturated"]
    assert wr.recommend(rows) == 1
    assert "SATURATED" in wr.describe(rows[1]) and "webcam" in wr.describe(rows[1])
    assert wr.classify_probe(-45, 0.1) == "noisy" and wr.classify_probe(-60, 0.1) == "good"
    assert wr.classify_probe(-60, 0.99) == "saturated"


def test_probe_median_ignores_one_click():
    def clicky(sec):
        x = quiet(sec)
        x[:200] = 1.0                                # one key click inside block 1
        return x
    assert wr.probe_device(FakeSD(DEVICES, {1: clicky}), 1, "x").verdict == "good"


def test_busy_device_reported():
    sd = FakeSD(DEVICES, {1: OSError("Device unavailable: busy"), 2: OSError("boom")})
    assert [r.verdict for r in wr.probe_all(sd)] == ["busy", "error"]


def test_webcam_hint_and_diagnosis():
    assert wr.is_webcam("2K HD Camera Microphone") and not wr.is_webcam("Microphone Array (Realtek)")
    assert "webcam" in wr.mic_diagnosis("USB Webcam", None)
    assert "saturated" in wr.mic_diagnosis("Mic", -18.0)
    assert "too hot" in wr.mic_diagnosis("Mic", -60.0, clipping=True)
    assert "silent" in wr.mic_diagnosis("Mic", -80.0)
    assert wr.mic_diagnosis("Microphone Array", -58.0) == ""


def test_analyse_and_verdicts():
    noise = float(np.median(wr.frames_db(quiet(3.0))))
    a = (speech(2.6) * 32767).astype(np.int16).reshape(-1, 1)
    assert wr.verdict(*wr.analyse(a, noise))[0] == "ok"
    loud = (saturated(2.6) * 32767).astype(np.int16).reshape(-1, 1)
    assert wr.verdict(*wr.analyse(loud, noise))[0] == "loud"
    soft = (speech(2.6, 0.0005) * 32767).astype(np.int16).reshape(-1, 1)
    assert wr.verdict(*wr.analyse(soft, noise))[0] == "quiet"
    assert wr.verdict(0.0, -30.0, 10.0)[0] == "noisy"
    click = (quiet(2.6) * 32767).astype(np.int16).reshape(-1, 1)
    click[100] = 32767
    assert wr.analyse(click, noise)[0] < wr.CLIP_FRACTION           # a single click is not clipping


def test_autoleveler_two_identical_verdicts_25_percent_steps():
    lv, out, sleeps = Level(0.8), [], []
    al = wr.AutoLeveler(lv, out.append, sleeps.append)
    assert not al.on_verdict("loud") and lv.sets == []              # first bad verdict: nothing changes
    assert al.on_verdict("loud") and lv.sets == [0.6]               # second identical: 25 % lower
    assert sleeps == [2.5] and "0.80 -> 0.60" in out[-1]            # old and new printed, settle 2.5 s
    assert not al.on_verdict("loud") and not al.on_verdict("quiet") and lv.sets == [0.6]   # a different verdict resets
    assert al.on_verdict("quiet") and lv.sets == [0.6, 0.75]
    al.on_verdict("ok")
    assert not al.on_verdict("quiet") and len(lv.sets) == 2         # an ok in between resets the count


def test_autoleveler_disabled_or_no_control():
    lv = Level()
    al = wr.AutoLeveler(lv, print, lambda s: None, enabled=False)
    assert not al.on_verdict("loud") and not al.on_verdict("loud") and lv.sets == []

    class NoCtl(Level):
        def get(self):
            return None
    al = wr.AutoLeveler(NoCtl(), print, lambda s: None)
    al.on_verdict("loud")
    assert not al.on_verdict("loud") and not al.enabled


def test_windows_level_unavailable_off_windows(monkeypatch):
    monkeypatch.setattr(wr.sys, "platform", "linux")
    c = wr.WindowsInputLevel()
    assert c.get() is None and c.set(0.5) is None and not c.available()


def test_move_old_never_deletes(tmp_path):
    s, o = tmp_path / "samples", tmp_path / "old"
    s.mkdir()
    for n in ("a.wav", "b.wav"):
        (s / n).write_bytes(b"x")
    (s / "keep.txt").write_text("y")
    assert wr.move_old(s, o, lambda: 5.0) == 2
    assert sorted(p.name for p in o.iterdir()) == ["5_a.wav", "5_b.wav"] and (s / "keep.txt").exists()
    (s / "a.wav").write_bytes(b"z")
    assert wr.move_old(s, o, lambda: 5.0) == 1 and len(list(o.iterdir())) == 3


class Session:
    """Mic 1: silence for the 2.5 s probe and the 3 s room step, then speech clips (scripted `kinds` first)."""

    def __init__(self, kinds=None):
        self.kinds, self.i = list(kinds or []), 0

    def __call__(self, sec):
        if abs(sec - 3.0) < 1e-6 or abs(sec - 2.5) < 1e-6:
            return quiet(sec)
        k = self.kinds[self.i] if self.i < len(self.kinds) else "ok"
        self.i += 1
        return {"ok": lambda s: speech(s, seed=self.i + 10), "loud": saturated,
                "quiet": lambda s: speech(s, 0.0003, self.i + 10)}[k](sec)


def test_full_session_records_20_clips(tmp_path):
    (tmp_path / "wake" / "samples").mkdir(parents=True)
    (tmp_path / "wake" / "samples" / "old.wav").write_bytes(b"x")
    d = deps(FakeSD(DEVICES, {1: Session(), 2: saturated}), level=Level(0.8))
    r = wr.run(d, home=tmp_path)
    assert r.code == 0 and r.saved == 20 and r.moved == 1 and r.device == "Microphone Array (Realtek)"
    assert len(list((tmp_path / "wake" / "samples").glob("*.wav"))) == 20
    assert (tmp_path / "wake" / "samples_old").exists() and not (tmp_path / "wake" / "samples" / "old.wav").exists()
    assert any("jarvis wake train" in x and "--samples" in x for x in d.out)
    assert 1.5 in d.sleeps and max(d.sleeps) <= 2.5
    assert wake_prod.sample_summary(wake_prod.check_samples(tmp_path / "wake" / "samples"))["usable"] == 20


def test_auto_level_after_two_loud_practice_clips(tmp_path):
    lv = Level(0.8)
    d = deps(FakeSD(DEVICES, {1: Session(["loud", "loud"]), 2: saturated}), level=lv)
    r = wr.run(d, home=tmp_path, clips=3)
    assert r.code == 0 and lv.sets == [0.6]
    assert any("0.80 -> 0.60" in x for x in d.out) and 2.5 in d.sleeps


def test_no_auto_level_flag_only_advises(tmp_path):
    lv = Level(0.8)
    d = deps(FakeSD(DEVICES, {1: Session(["loud", "loud"]), 2: saturated}), level=lv)
    r = wr.run(d, home=tmp_path, clips=3, auto_level=False)
    assert lv.sets == [] and r.code == 0 and any("ADJUST" in x for x in d.out)


def test_not_default_mic_never_touches_level(tmp_path):
    lv = Level(0.8)
    d = deps(FakeSD(DEVICES, {1: Session(["loud", "loud"]), 2: saturated}), level=lv)
    d.default_mic = lambda: "Some Other Mic"
    wr.run(d, home=tmp_path, clips=3)
    assert lv.sets == []


def test_core_running_offers_stop(tmp_path):
    d = deps(FakeSD(DEVICES, {1: Session(), 2: saturated}))
    d.core_running = lambda: True
    d.ask = lambda p: "n"
    r = wr.run(d, home=tmp_path)
    assert r.code == 3 and any("jarvis stop" in x for x in d.out) and d.sd.calls == []
    stopped = []
    d2 = deps(FakeSD(DEVICES, {1: Session(), 2: saturated}), stop_core=lambda: stopped.append(1) or True)
    d2.core_running = lambda: True
    d2.ask = lambda p: "y" if "Stop JARVES" in p else ""
    assert wr.run(d2, home=tmp_path, clips=3).code == 0 and stopped


def test_all_busy_says_stop_core(tmp_path):
    d = deps(FakeSD(DEVICES, {1: OSError("busy"), 2: OSError("busy")}))
    assert wr.run(d, home=tmp_path).code == 2 and any("jarvis stop" in x for x in d.out)


def test_no_usable_mic(tmp_path):
    d = deps(FakeSD(DEVICES, {1: saturated, 2: saturated}))
    assert wr.run(d, home=tmp_path).code == 2 and not (tmp_path / "wake" / "samples").exists()
