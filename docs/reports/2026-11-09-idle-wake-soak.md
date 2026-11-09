# Idle wake cost and memory growth (JAR-96)

Scope: `WakeListener` (core/src/jarves/voice/wakeword.py) fed 90,000 fake 80 ms silent frames (2 h of audio), no audio device, network or real clock. Detector is a null scorer, so this measures the listener's own cost, not a model's.

| Measure | Result |
|---|---|
| Frames processed | 90,000 (odd chunk sizes exercise reframing) |
| Traced heap growth after 2,000-frame warm-up | < 64 KiB asserted (test passes) |
| Reframing buffer | stays under 2 frames |
| Listener overhead per frame (`avg_cpu_ms`) | 0.0004 ms (about 4.4 us wall incl. asyncio) |
| Wall time for 2 h of audio | 0.39 s |

Finding: no leak in `WakeListener`; nothing fixed, so no product change. Note: the test double `FakeDetector.frame_sizes` grows by one int per frame (test-only, never used in product), so the soak uses a stateless detector.

UNVERIFIED: real model cost (openWakeWord / HeadWakeDetector) and Raspberry Pi 5 numbers; needs the real runtime and hardware.

Reproduce: `PYTHONPATH=core/src python -m pytest core/tests/test_wake_idle_soak.py -q`
