# Handoff 2026-10-23: music is quiet when not in use

Problem: `jarvis status` showed Core "degraded" and `last_error` = Pear Desktop not reachable on every start, though the
owner had not asked for music (health loop set `music` = unavailable whenever Pear was closed).

Changes
- `health.py`: new component status `idle` (optional, not running, starts on demand); it never degrades the Core and never sets `last_error`.
- `skills/music.py`: `MusicSkill.health()` returns `idle` while Pear is closed/unpaired and no request failed; only after a
  REQUESTED music action failed (offline / not paired / timeout) it reports `unavailable`/`degraded`; cleared as soon as Pear answers.
  Offline answer now says what to start and the setting (EN action, HE spoken text): start Pear (`jarvis music start`),
  Plugins > API Server, 127.0.0.1:26538, ask again.
- `cli.py`: `jarvis music start` (alias of `open`; says so when Pear already runs, never installs anything); `jarvis doctor` shows
  Pear closed as info, not warn.
- `features.py`: music probe reason now "idle, not an error" (still not available until Pear runs; capabilities status unchanged and honest).
- Guide `guides/data/music.toml` problem text (EN+HE) updated. No dashboard catalog strings added (Hebrew lives in the guide / skill).
- Tests: `tests/test_music_qa.py` (fake player, no real app).

Open: the music probe stays False while Pear is closed (brain may say "not available"); making it True-on-idle would let the
skill try and answer with the actionable message - owner decision. `tests/test_selfcheck.py` 2 failures (bundle:* findings, trend) are unrelated to this change.
