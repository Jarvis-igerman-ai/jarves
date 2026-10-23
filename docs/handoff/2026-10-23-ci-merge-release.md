# CI, merge and release friction — 23 Oct 2026 / #89, PR #90 and its follow-up (branch `claude/exciting-goodall-kllmnr`)

- Measured (Actions API, 231 jarves-checks runs, 73 PRs): PR open->merged median 9.1 h (p90 20.6 h); 43% of completed PR
  runs failed (median 24 min to fail); since the 22 Oct CI work a green PR run takes 10.3-14.1 min, critical path always
  one shard about 2x its siblings. Details and the causes list: #89.
- Fixed in PR #90: (1) shard timing refresh from the PR proof on reused `main` pushes (`main`'s cache was never written, so
  every PR planned from the 22 Oct seed with 30 of 174 files unmeasured); (2) `release_gate` reads bug tables by header
  (41 phantom open P0-P2 rows -> 6, 3 of them stale and synced in `SWV4_CAMPAIGN_BUG_TABLE.md`); (3) `release_status.py`
  looks the installer up by the content tree (it always said MISSING) and says UNKNOWN instead of "none"/"off";
  (4) prepush `perf` also runs `test_tool_descriptions.py`; (5) `docs/GITHUB_FLOW.md` corrected: the repo is public, so
  branch protection and native auto-merge are free; `main` is unprotected today.
- Flakes (40 failed-job logs read, 20-23 Oct): most failures were real PR bugs; older flakes were already fixed on main
  (a11y updates-page network text, perf-gate cold first call, guides env leak, workspace undo, r01 kill_on_close). Fixed
  in #90: the 22 Oct nightly failure was a product bug in `browser.py` (a refused navigation aborted with
  `blockedbyclient` committed Chromium's empty error page after the click returned on a busy runner, so the "page stays"
  promise broke; now `aborted` keeps the same document; same-document check fails 3/3 on the old code, passes 3/3 now);
  PR #78's red Windows shard was the test's QR decoder (`cv2.QRCodeDetector` misses about 0.3% of valid symbols; the
  pairing URL is random per run) -> `QRCodeDetectorAruco`, the failing URL pinned. Local Linux: test_browser,
  test_review_c, test_mobile_phone_e2e 72 passed (Playwright 1.64 pointed at the preinstalled Chromium 1243 and
  `phone.lan` kept off this sandbox's proxy; CI needs neither). Windows/Edge path not run locally.
- Still open: `orchestrator/bridge.py` `os.replace` of `bridge.token` can hit WinError 5 on Windows (fix prepared for
  the follow-up PR). Checked and already fixed on main before #90: the `test_canary` 8.8 s case (warm-up + median, c3c0b7b)
  and `test_cli_setup_paths` 175-190 s (fakes, ed14a9f; now 0.78 s locally). The nightly runs browser files without a
  retry, so any browser flake resets the 5-green-nights count for a stable release.
- NOT verified: the proof-run junit download on a real `main` push and the resulting shard times (first merge after #90:
  read the `changes` job summary line "shard plan: ..." of the next PR run; expect about 385 s Linux / 455 s Windows per
  shard and few unmeasured files).
- Owner decisions (#89): merge policy (required check `CI result (single required check)` + Allow auto-merge, no
  "up to date" requirement, drop the update-and-re-run habit; changes CLAUDE.md); stable release needs SWV3_P01 B13/B14
  and SWV3_P04 Q05 live-verified or accepted, and 5 consecutive green nightlies. #84 (next-flow parse failure, every push
  showed a red run) merged as a0c0762.

- Follow-up (owner decisions 23 Oct 2026, same branch after #90 merged): owner set the `main` rule (required check
  `CI result (single required check)`, no "up to date", admins may bypass) and Allow auto-merge; #90 merged by
  auto-merge. `automerge.yml` merges green PRs without update-and-re-run and stops while `main` is red; CLAUDE.md merge
  rule updated; `docs/GITHUB_FLOW.md` rewritten. Owner accepted B13, B14, Q05 (fixed in code, live check later): no
  P0-P2 row stays open in `docs/reviews/*BUG_TABLE*.md`. `orchestrator/bridge.py` `write_token` retries the
  `bridge.token` swap on PermissionError for up to 1 s (Windows WinError 5 race on every Core restart), temp file removed
  on failure; fake-clock tests. NOT verified: the Windows race itself (Linux container); first label-queue merge of a
  PR that is behind `main`. Found, not changed: a dashboard canary page-load timeout reports only `TimeoutError` and drops
  the page errors it collected (`diag/canary.py` `j_dashboard`, run 39740077144).
- 4.0.1 stable prep (owner asked 23 Oct): `docs/reviews/RELEASE_CHECKLIST_4.0.1.md` (gate-readable, evidence per row) and
  `docs/reviews/MANUAL_TEST_4.0.1.md` (28 rows; the generator made 84: one per sentence, guide and page file). The gate
  no longer falls back to the 4.0.0 checklist for later versions. Left for the owner: test the beta and sign off
  (`jarvis release signoff`), then 5 green nightlies, dry run, tag `v4.0.1` on the owner's go.
- Handoff notes are now one file per task in `docs/handoff/` (this file is the first); `docs/HANDOFF.md` keeps history.
- Next (owner approved 23 Oct, separate small PRs): wake-word tests train once; each shard loads only its files; phone
  e2e test to the browser job; Windows xdist 3; installer smoke jobs 4 -> 2; nightly browser files get one retry; an
  issue opens when `main` turns red. Running some tests only on Linux was advised against (Windows-only bugs) and not done.
- #90 and #93 merged by auto-merge (18:24, 18:41). Next PR (test execution): `test_mobile_phone_e2e.py` runs in the
  browser job (one retry; planner and conftest browser lists synced, a test keeps them equal); each Core shard passes
  only its planned files to pytest (cold collection 5.8-6.3 s instead of 9.8-10.4 s per Linux shard, same tests);
  Windows shards were tried with 3 xdist workers and set back to 2 in the same PR (#96: no faster, and
  test_projectmgr's parallel-worktree test failed under the load; that test is load-sensitive, a candidate for the next flake sweep).
