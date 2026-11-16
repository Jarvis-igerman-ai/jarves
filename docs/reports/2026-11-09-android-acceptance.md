# Android acceptance sheet and guide (JAR-85, GitHub #395, V7 A7)

Date 2026-11-09. Docs only; no Kotlin, no `.github`, nothing from PR #985 (A6 #394) touched.

## Finding
The deliverables already exist on main (commit 1ea105b6): the acceptance sheet with 15 rows and the battery/OEM matrix
(`docs/reviews/ANDROID_COMPANION_ACCEPTANCE.md`) and the EN/HE guide `core/src/jarves/guides/data/android_companion.toml`
(Hebrew is inline in the toml, so `catalog_add.py` has nothing to add). Rows 1-11 are NOT-RUN, 12-15 NOT-BUILT.
Exact-alarm prompt, airplane mode and OEM battery rows are device checks: UNVERIFIED, owner/phone only.

## Parity / features row (not added, on purpose)
The app has no feature switch of its own: its only CLI path is `jarvis features on mobile_access`, which already has
a `cli_parity` entry (`dashboard`) and a features row. `cli_parity` fails on feature ids missing from the catalog, so a
`[parity.features] android_companion` entry would break `test_cli_parity`; a new catalog feature would be product code
and out of this docs-only scope. Proposal: add the row when A3 ships a real on/off switch.

## Checks
`pytest core/tests/test_guides.py core/tests/test_cli_parity.py`: 52 passed.
