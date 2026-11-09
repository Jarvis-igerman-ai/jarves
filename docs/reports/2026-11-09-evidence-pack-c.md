# Evidence pack C: honest-stage audit of shipped beta features (JAR-32 / T19)

Date 2026-11-09. Base: origin/main 0b5fd6c3. Report only; no product code changed. All issues #60 #167 #365 #331 #336 #140 #71 #77 are still OPEN on GitHub.
Stage = `features.stage_of()` after importing every module (explicit `stage=` or derived from the capabilities table). Tests = existing test files; no real device, phone, ear or live sign-in was exercised.

| Issue | Feature id(s) | Stage | Guide EN+HE | CLI parity | Tests (file, count) |
|---|---|---|---|---|---|
| #60 sign-off wizard | `release_signoff`, `signoff_steps` | beta, beta (explicit) | `release_signoff.toml`, `signoff_steps.toml`: both EN+HE | `release_signoff`->`release` (cli_parity.py); `[parity.features] signoff_steps="release"` | test_release_signoff 33, test_signoff_steps 7 |
| #167 what-changed card | `whats_new_card` | beta | `whats_new_card.toml` EN+HE | `[parity.features] whats_new_card="whatsnew"` | test_whats_new_card 10, +_browser |
| #365 smart chat UI | `smart_format` | beta | `smart_chat.toml` EN+HE | `[parity.features]` + `[parity.tool_groups] ui_blocks` (chat only, terminal prints numbered list) | test_ui_blocks 21 |
| #331 chat UI/UX | `text_chat`, `web_chat` | **stable** (explicit, chat_service.py:39), beta | `text_chat.toml`, `web_chat.toml` EN+HE | `text_chat`->`chat`, `web_chat`->`web` (cli_parity.py) | test_web_chat 34, test_chat, test_chat_render, test_chat_markdown_browser; no test_text_chat file |
| #336 self-diagnosis | `diagnosis_llm_pass`, `self_diagnosis_ask`, `self_diagnosis_ai_wording`, `self_diagnosis_handoff`, `self_check` | beta; handoff off by default, risk high | `diagnosis.toml`, `self_check.toml` EN+HE | all ->`diagnose` (cli_parity.py) | test_self_diagnosis 29, test_self_diagnosis_handoff 18, test_selfcheck (2 FAIL, see below) |
| #140 Any.do | `anydo`, `anydo_update` | beta, beta (risk high) | `anydo.toml` EN+HE | `anydo`/`anydo_update`->`anydo` | test_anydo 17 |
| #71 lazy tool groups | none (internal: `registry.open_lazy`, brain.py:669) | n/a, no features row | **none** | none (internal optimisation) | test_lazy_tools 4, test_cli_lazy |
| #77 mic mute + room appearance | `appearance` (+ runtime guide for `jarvis mic`) | not checked as a beta feature | `appearance.toml`, `runtime.toml` EN+HE | `appearance`->`appearance`; `app`->`mic` | test_appearance 9, test_conversation_controls (3), test_runtime |

## Gaps (honest-stage findings)
1. **#331 stage mismatch risk:** `text_chat` is explicitly `stable` in code, while the sibling `web_chat` is beta and the issue is open as "feel like Claude Code". Nothing in this audit shows owner-device verification of `text_chat`; confirm the basis for `stable` or downgrade (UNVERIFIED).
2. **#71 has no guide, no features row, no parity entry.** Acceptable only if it stays an internal optimisation; "-30%" claim has no test (test_lazy_tools has 4 tests on group opening, not on size). UNVERIFIED claim.
3. **#77:** no single guide/features row covers "mic mute + room appearance" together; coverage is split across `runtime.toml` and `appearance.toml`. Mute/unmute is tested only through CLI/offline-command tests; real microphone behaviour is UNVERIFIED.
4. **#365:** guide key is `smart_chat.toml` but feature id is `smart_format` (name mismatch); button works only in the dashboard on the PC itself, as the guide states. Phone use is not supported.
5. **#336:** `diagnosis.toml` lists `self_diagnosis_handoff`, which has risk high and needs a coding agent; no test of a real coding-agent handoff (mocked in test_self_diagnosis_handoff). `self_check.toml` has no `[parity.*]` block (central map only).
6. Most guides declare parity only in the central `cli_parity.py` map, not in a per-guide `[parity.*]` block (release_signoff, web_chat, text_chat, self_check, diagnosis, anydo, appearance, runtime). Only whats_new_card, signoff_steps, smart_chat, self_awareness have one. Not a defect while test_cli_parity passes.
7. **#140 live sign-in: UNVERIFIED.** test_anydo uses fakes; the guide's own check ("available:anydo") only sees a saved sign-in. No real OAuth against Any.do was run.
8. Hebrew strings: every listed guide has `_he` fields. This audit did not run a Hebrew-quality or catalog_add coverage check beyond test_guides; dashboard Hebrew catalog coverage is not confirmed per feature.
9. #60/#167/#365: tests are hermetic; none has installed-product or owner-device evidence. Closed/merged/green is not verified.

## Test run (targeted, this base)
`PYTHONPATH=core/src python -m pytest -q` over the 16 files above: 726 passed, **2 failed**, both in `tests/test_selfcheck.py`:
- `test_config_sanity_and_feature_gaps` (feature-gap bundle ids `bundle:camera_preview/chimes/tray_icon/wake_word` fail an `all(...)` assertion)
- `test_trend_against_the_previous_run` (`assert (0 - 0) > 0`)
Not diagnosed (report-only task; may depend on this PC's state or on a concurrent run in the shared workspace). Needs a rerun on CI/clean checkout before being treated as a regression.
