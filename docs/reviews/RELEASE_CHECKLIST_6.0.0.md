# Release checklist: JARVES 6.0.0 (stable), DRAFT

Draft prepared 28 Oct 2026 from the 5.0.0 checklist. Docs only: V6 code is NOT authorized yet (`docs/NOW.md`, issue #224), nothing is versioned, tagged or published. Read by `core/tools/release_gate.py` when the tag `v6.0.0` is pushed: every row must be `done`, `n/a` or `accepted`. Steps: [RELEASING](../RELEASING.md) "Stable release, step by step". Triage: [BUG_TABLE_V60_2026-10-25](BUG_TABLE_V60_2026-10-25.md). Security: [V6_SECURITY_REVIEW_2026-10-25](V6_SECURITY_REVIEW_2026-10-25.md). Safe default: 6.0.0 ships as beta with the car rows NOT-RUN; the stable tag waits for the owner's signed sheet and the parked-car session (G11).

| # | Item | Status | Evidence / what is left |
|---|---|---|---|
| 1 | `jarves-checks` green on the release commit | n/a (checked by the gate at tag time) | A green push run on `main` for the tagged commit. |
| 2 | Latest nightly run green | n/a (checked by the gate at tag time) | Newest nightly on `main` green and at most 2 days old. |
| 3 | The 5.0 stable gate has passed first | open | Pipeline is 4.5, 5.0, 6.0. 5.0 stays beta until the owner's phone sheet (#193). |
| 4 | Bug table: no open P0-P2 | open | `BUG_TABLE_V60_2026-10-25.md`; re-triage on the release day. `jarvis release status --report --version 6.0.0` is the generated evidence. |
| 5 | Open GitHub issues triaged | open | All V6 issues classified with owner and plan; P3 reasons listed. |
| 6 | `jarvis selftest`, `doctor` and `speedtest` findings triaged on the 6.0 beta | open | Run through `jarvis install-latest --channel beta`, then triage every finding. |
| 7 | V6 security review items closed | open | Hands-free voice (#219): microphone only while the session runs and the page is on screen; no location without consent; no change to approval or drive-mode rules. |
| 8 | V6 features have guide (EN+HE), CLI parity entry and an honest capability row | n/a (checked by CI) | None may be `verified_on_device` before the car rows are run. |
| 9 | Version bump to 6.0.0 and CHANGELOG section `6.0.0` | open | Not done in this prep by design. |
| 10 | Installer: upgrade 5.0 to 6.0 and rollback smoke | open | CI upgrade matrix plus the owner-PC smoke (`jarvis install-latest`, then `jarvis update --rollback`). |
| 11 | Release gate dry run on the final commit | open | `core/tools/release_gate.py --tag v6.0.0 --sha <sha>`; or `jarvis release run --version 6.0.0 --dry-run`. |
| 12 | Owner's manual test sign-off (`MANUAL_TEST_6.0.0.md`) | n/a (checked by the gate at tag time) | Generate with `core/tools/manual_test_sheet.py`, then `jarvis release signoff --version 6.0.0` per row and `--finish`. |
| 13 | Parked-car session (G11): hands-free voice, drive screen, stop word | open | Owner only. If absent: car rows stay NOT-RUN and 6.0.0 ships as beta. |
| 14 | Stable tag only after the owner says go | open | Never tag without his chat approval. |
