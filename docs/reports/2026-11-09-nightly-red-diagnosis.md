# Nightly red 3-8 Nov 2026: diagnosis (JAR-51 / T16, #480)

Outcome: one root cause for every failure; the fix lives in `.github/workflows/nightly.yml` (owner Q5: not edited here). No test-only fix is correct.

## Root cause
`nightly.yml` installs Playwright browsers **without ffmpeg**:
- line 40 (Full Core suite): Linux `playwright install --with-deps chromium`; Windows only "using the preinstalled Edge".
- line 171 (dashboard canary): `playwright install --with-deps chromium`.
`jarves-checks.yml:413` (PR CI, green) installs `chromium ffmpeg`. So code that needs ffmpeg passes on PRs and fails nightly.

## Evidence (run 42464525694, 8 Nov; same two tests failed in 42356214321 and 42192945203)
| Job | Failing test | Why |
|---|---|---|
| Flake detector (3x, Linux), all 3 passes | `test_browser_driver.py::test_a_real_recording_pauses_on_a_password_field_and_resumes_in_a_new_file` | `brec.ffmpeg_ready()` false and `CI=true` -> deliberate `pytest.fail("Playwright's ffmpeg is missing in CI ...")`. Deterministic, not a flake. |
| same | `test_dashboard_a11y.py::test_every_page_is_clean_in_a_real_browser` | 3 problems: the English hint `video needs Playwright's small ffmpeg: python -m playwright install ffmpeg` (`browser_recording.py:287`) is shown on the features/addons pages in `he` as `untranslated`. It only appears because ffmpeg is missing. |
| Full Core suite (windows-2025) | the same a11y test (fails on retry too, 2 failed / 115 passed / 5 skipped); `NO-SKIPS RULE` also reports 5 skipped browser-driver tests | Windows has no ffmpeg either. Main suite itself: 7350 passed in 3144 s. |

## Windows "closed database / closed file" noise
These are `PytestUnhandledThreadExceptionWarning` lines (e.g. `orchestrator/service.py:_codex_approval` -> `db.add_approval` -> `Cannot operate on a closed database`; `ValueError: I/O operation on closed file`): background threads outliving test teardown. The run reports 7350 passed, so they are warnings, not failures. They are a separate hygiene item (threads not joined at fixture teardown), not the cause of red. The 3144 s duration is the test count; it is not explained by this noise (UNVERIFIED: no profiling done).

## Exact blocker / proposed fix (Tier R path, owner to apply)
In `.github/workflows/nightly.yml`:
- line 40: `uv run playwright install --with-deps chromium ffmpeg` on Linux; on Windows add `uv run playwright install ffmpeg`.
- line 171: `... install --with-deps chromium ffmpeg`.
Expected: both tests pass; a11y untranslated hint disappears. UNVERIFIED until a nightly runs (not run locally; no workflow dispatch by this agent).

Optional follow-up (product, separate task): translate the `ffmpeg_ready` hint into Hebrew so a11y does not depend on ffmpeg presence.
