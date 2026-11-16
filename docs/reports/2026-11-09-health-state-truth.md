# Health/status wording audit: configured vs installed vs available vs live-verified (JAR-137)

Scope: `core/src/jarves/features.py` and dashboard JS. Paths are relative to `core/src/jarves/`. Line numbers are on main at b9fe38d5.

## Fixed in this PR
1. `features.py` `state_word` (~1011): an enabled feature whose first probe had not finished (`check == "checking"`) was reported "unavailable", a claim nobody verified. It is now "checking" (Hebrew "בבדיקה") in the CLI, the model tool and the switch result. Test: `test_enabled_but_never_checked_is_checking_not_unavailable`.
2. `dashboard_static/browser.js:158`: the Browser page showed a red "unavailable" pill while the feature was still "checking". It now uses `f.health`.

## Found, not fixed (needs an owner decision or is larger than Tier N small)
3. `features.py:264-270` `_skill_installed`: `skills()` returning `None` (unknown, e.g. dashboard) passes as "installed". The Google and similar features then read "on" without a live check.
4. `features.py:231-243` `_probe_mic` / `_probe_speaker`: no `audio`/`mic` component report returns True. Wake word and voice features can read "on" with no evidence of a device. These are live-device claims: UNVERIFIED.
5. `features.py:89` `_always`: features registered without a probe are always "available". `memory_curator` and `self_diagnosis_ask` are examples; "available" there means only "configured".
6. `features.py:748`: `health = "ok"` is derived from a result that may be `stale` (older than PROBE_TTL_S). The `check` field is correct, but `m4.js:58` shows "active" without it.
7. `dashboard_static/app.js:81` (`core.state === "ready"`), `home.js:86` ("OK" for a connection state) and `mobile_admin.js:22` ("ready" for a tailnet): not traced to their producers. Candidates for a follow-up.

Nothing here was verified on a real device.
