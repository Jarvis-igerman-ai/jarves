# Evidence pack B: installer / update / phone issues (T18, JAR-38)

Docs only. Base: origin/main `0b5fd6c3` (2026-11-09). Issues were NOT closed; all seven are still OPEN on GitHub.
Closed issue != merged code != tested user scenario != shipped artifact. Installed-product and phone rows are UNVERIFIED (owner-only).

| Issue | Fix PR (merge commit) | Key fix commits | Automated test | Installed product / phone |
|---|---|---|---|---|
| #466 bridge exe path after install | #930 (01a7cd55); code fix in #465 | ba13d7f7 stale-exe report/restart; e6cd8f37 start-order pin | `core/tests/test_hotfix_slice2.py` | UNVERIFIED (needs a real install) |
| #343 no Core restart after silent install | #383 (ba9cc899) | 335d5aa3, a4b332a3 | `core/tests/test_installer_clean.py` | UNVERIFIED |
| #261 recovery for dead program after schema bump | #655 (c5eb3804), Tier R | df3553f3, 97bd902d, 41ce6528 | `core/tests/test_installer_state.py` | UNVERIFIED |
| #166 restart only what was running | #269 (547ee2ae), #951 (3057e924) | 53c94f19, e799ddad, ba330859 | `test_runmarks.py`, `test_update_was_running.py`, `test_installer_clean.py`, `test_installer_state.py` | UNVERIFIED |
| #357 phone pairing too hard | #360 (60478dc9) | 7d8f25ba, 685a6834 | `core/tests/test_dashboard_phone_switch.py` | UNVERIFIED (real phone) |
| #510 phone PWA not installable | #641 (85b4754b) | 9776c875 | `core/tests/test_mobile_pwa.py` | UNVERIFIED (real phone) |
| #509 phone UI looks bad | #532 (82056308), #648 (2e15a04c) | fdb375d6, cf64ea2d, 489fe02f | `core/tests/test_mobile_phone_e2e.py` | UNVERIFIED (real phone) |

## Test run on main 0b5fd6c3
Command: `PYTHONPATH=core/src python -m pytest -q core/tests/test_hotfix_slice2.py test_installer_clean.py test_installer_state.py test_runmarks.py test_update_was_running.py test_dashboard_phone_switch.py test_mobile_pwa.py test_mobile_phone_e2e.py`

Result: 165 passed, 2 failed (256 s). The 2 failures are `test_qr_payload_decodes_and_pairing_is_single_use` and `test_qr_encoder_output_decodes_for_realistic_urls`. Both raise `ModuleNotFoundError: cv2` because OpenCV is not installed in this local Python. That is an environment gap, not a product defect. The QR checks are UNVERIFIED locally and need a run in CI or an environment with `opencv`.

## Open items
- Issues remain open pending owner installed-product / phone verification. Nothing here is a closure claim.
