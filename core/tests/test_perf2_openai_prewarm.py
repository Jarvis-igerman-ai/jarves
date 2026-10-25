"""#159 perf2: `import openai` (8-12 s) starts at the very beginning of Core start in cloud-voice mode, in a DAEMON
thread (a running import must never delay process exit / `jarvis stop`). Offline: fakes only."""
import importlib
import threading
import time

from test_runtime import make_core


def _core(home, cfg, mode, monkeypatch, key=True, real_audio=True):
    cfg.voice.mode = mode
    cfg.ai.provider = "openai"
    core = make_core(home, cfg)
    core.use_real_audio = real_audio
    monkeypatch.setattr("jarves.app.openai_key_present", lambda: key)
    return core


def _watch(monkeypatch):
    seen = []
    done = threading.Event()
    real = importlib.import_module

    def fake(name, *a, **k):
        if name == "openai":
            seen.append(threading.current_thread())
            time.sleep(0.3)
            done.set()
            return None
        return real(name, *a, **k)
    monkeypatch.setattr("importlib.import_module", fake)
    return seen, done


def test_live_mode_starts_openai_import_in_a_daemon_thread(home, cfg, monkeypatch):
    core = _core(home, cfg, "live", monkeypatch)
    seen, done = _watch(monkeypatch)
    t0 = time.monotonic()
    core._start_early_voice_import()
    assert time.monotonic() - t0 < 0.2                 # returns at once, does not wait for the import
    assert done.wait(5) and seen and seen[0].daemon and seen[0] is not threading.main_thread()


def test_no_early_import_when_voice_is_off_or_no_key_or_no_audio(home, cfg, monkeypatch):
    for mode, key, audio in (("off", True, True), ("push_to_talk", True, True), ("live", False, True), ("live", True, False)):
        core = _core(home, cfg, mode, monkeypatch, key, audio)
        seen, done = _watch(monkeypatch)
        core._start_early_voice_import()
        assert not done.wait(0.5) and not seen, (mode, key, audio)


def test_prewarm_thread_is_a_daemon(home, cfg):
    started = []
    t = type(make_core(home, cfg))._spawn_daemon(lambda: started.append(1), "x")
    t.join(2)
    assert t.daemon and started == [1]
