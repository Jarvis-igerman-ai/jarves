# Handoff after the release: JARVES 4.5 (tag v4.0.1), 27 Oct 2026

Plain words. Written after the stable release. No secrets, no personal data. Owner decisions live in the owner-opens file kept outside the repo (sections named below); read it fresh before asking the owner anything.

## 1. The release

- JARVES 4.5 shipped as tag `v4.0.1` on 27 Oct 2026 about 07:00 UTC (release notes title "JARVES 4.5 (v4.0.1)").
- Release run 40463143723 succeeded. Public release: https://github.com/igalgerman/jarvis-releases/releases/tag/win-v4.0.1.1214
- Installer 4.0.1.1214, sha256 `a1ec102b3c67a407e912274828a5029fdbf7b5b93200e7347e9721cd4a4fd2c1`.
- Tag commit `ca8eb36b` (main at the time of the tag). Tested beta: 4.0.1-beta.195 at `313425ae` (everything merged before the tag).
- The owner approved in chat (single approval question with an evidence summary). Rows S1-S5 are recorded as "accepted by owner approval" (S2-S4 were not heard by a person; S1 and S5 are proven by CI). The sheet was finished with owner `igalgerman`.
- Known issues accepted for the notes (label `known-issue-4.5`, not counted by the gate): #179 #261 #441 #509 #510 #543 #544 #605.

## 2. What shipped in this session (grouped)

### Release tooling and gate
- The "five green nightly runs" rule is gone. Stable needs: main green, gate dry run green, workflow dry run green, zero open P0-P2 (except `known-issue-4.5`), the owner's signature.
- The sign-off sheet carries the owner name plus a hash of the rows, checked by the gate (PR #602; #617 hardens owner and cell canonicalization). The hash proves the sheet was not edited; it does not prove who signed (see risks).
- Tag-commit rule: the gate checks that the signed commit is the tagged commit. The release workflow dry run now works from a dispatch (#597, #592). #609 (tag input must equal the tag ref) is still held.
- Docs-only CI fast path was approved by the owner for after 4.5 (not built yet).

### Voice and wake
- Greeting plays after the wake word is heard (#179 fix is in the code; still to measure live).
- Wake threshold semantics clarified: confirm-start threshold (about 0.25 after `jarvis wake test --apply`); instant-accept stays 0.5.
- Wake listening starts about 9 s after Core is ready (was 47 s).
- One pip on wake (the second "ready" pip was removed).
- Hebrew clock: the spoken time is correct.
- Hidden-mode indicator (listening dot, tray icon per state, beta) and idle stop after 45 s of quiet (`voice.idle_stop_s`).
- ClipMonitor and `jarvis wake test` hints about clipping; `jarvis wake record` for own-voice samples.
- Wake training bug fixed, hard negatives added, relative gate added (a candidate model must beat the baseline). Own-voice model test: 95 percent, held-out 100 percent (standard model 70 percent), but near-miss false wakes kept the gate from recommending it. PR #614 (absolute ceilings, baseline = live custom head) is open.

### Phone redesign and PWA icons
- Phone pages redesigned, PWA icons added. #509 and #510 remain: a full PWA install needs a trusted certificate (see next steps).

### Security hardening
- Hotfix: revert and recover paths; backup first, dry run first, auto-revert.
- GitHub write limits; PR, review and rerun writes sit behind an OFF switch (V7 G4, CLI only); extra "approve" switch for APPROVE reviews (#579).
- Browser: DNS pinning and IP checks, backslash host bypass fixed (#623); ports and fail-closed `pin_hosts` (#615, open).
- Popups and phone plans escape text (bidi and zero-width characters in #616, open).
- Skill locks (#583) and failed-run state (#570).

### V7 pilots
- G1-G4 (GitHub read, write limits, approvals, PR/review writes), M1-M4 (Model Lab; M4 is a read-only page that honours its switch, 409 `MODELLAB_OFF`), B1 (browser session model and event stream).

### CI and flakes
- Flake quarantine policy applied: flakes #546-#551 #555 #556 closed with run evidence; showstoppers at 0.
- Browser context leak fixed in the real-browser tests.
- Worktree cleanup done (#600); keep it up.

## 3. Current state

- main at `ca8eb36b` when this was written (more merges may follow: `git fetch jarves`).
- Stable: 4.0.1 (installer 4.0.1.1214, stable `latest.json` OK). Beta channel: 4.0.1-beta.195 (1214) at `313425ae`.
- Owner PC: build 4.0.1.1040 plus hotfix.4 (`313425ae`). Revert ids: `20261026-223500` (hotfix.3) and `20261027-041717` (hotfix.4). Backups in `%LOCALAPPDATA%\jarves\backups`.
- Counts at the freeze: showstoppers 0; open P1 about 16, P2 about 16, P3 about 29 (recount with `gh issue list`).

## 4. Held and queued PRs (at hand-off)

| PR | What | Next action |
|---|---|---|
| #609 | release.yml: tag input must equal the tag ref (#598) | Opus review (release tag source); queue after it passes. |
| #614 | wake gate: absolute ceilings, baseline = live custom head | Review (Opus for wake gate when tokens allow), then queue. |
| #611, #615, #616, #617 | wake head start-up pin (test + evidence, #594), browser ports, phone plans escaping, sign-off canonicalization | Queued after the release; confirm each merged (`gh pr view N`), re-queue any that dropped (`gh pr merge N --auto`). |
| #516 | draft: real-browser retry only re-runs failed files, CI speed plan (#160) | Keep draft until the CI speed lane resumes. |
| #504 | docs: simple releases plan | Held; fold into the release-simplification slice 3 (OD-S1, OD-S2). |

## 5. Open issue themes (recount with `gh issue list -R igalgerman/jarves`)

- Release security, owner-gated (P2): #543 sheet editable after signing, #544 release secrets usable outside a gated job, #605 the hash does not authenticate the owner. Accepted for 4.5; they need owner GitHub settings (tag ruleset, protected environment, signed tags).
- Phone: #509 UI, #510 PWA install.
- Voice and installer: #179 greeting sometimes missing (re-measure live), #261 installer recovery after a dead new program, #441 test_owner_answers flake (closes after a clean nightly flake scan).
- Speed and CI: #159 performance overhaul, #160 CI reliability.
- Smart chat: #447. V7 backlog: #390-#395 (Android A2-A7), #396-#401 (browser B1-B6), #405-#407 (OpenCode D2-D4), #412-#414 (G5-G7), #417 #419 #420 (Model Lab, extras).
- Review follow-ups (P3): #594-#596 wake gate notes, #598, #606-#608, #623-#625.

## 6. Owner decisions (see the owner-opens file)

- Sections: "DECISIONS 26 Oct (owner present): scope of the 4.5 stable", "DECISIONS 26 Oct (owner present, late)", "Phone install (#510)", "Hidden mode indicator and idle stop" (OD-H1 to OD-H4), "Trusted phone connection" (OD-T1 to OD-T3), OD-S1 and OD-S2 (simple releases), "Bug issues that only the owner can finish".
- Standing: a stable release only on the owner's single chat approval per release; open-PR limit 8 and combined PRs are permanent; nightly rule removed; CodeQL #43 #45 #48 dismissed as false positives with proof tests, #46 #47 fixed in 4.5.1.
- Still open for him: Tailscale HTTPS for the phone (guided), phone sheet #193, the live voice and wake retest (OD-H1), one `jarvis wake reset-tune` run, GitHub settings for #543 #544 #605. Safe defaults apply while he is away; never block on these.

## 7. Standing processes

- Stable release: the owner says "do stable release". Procedure (memory note `stable-release-on-owner-approval`): freeze non-docs merges; confirm the newest beta equals main (docs-only since) with CI and installer smoke green; run `jarvis release status`, the gate dry run and the workflow dry run; show a SHORT evidence summary and ask ONE approval; on approve record rows with `jarvis release signoff` (owner name, honest note "approved by owner in chat <UTC time>", never invent a manual test), `--finish`, commit the signed sheet (docs only), re-run status and dry run, tag the signed commit, watch the workflow; then unfreeze, write notes, hotfix his PC. No approval means no tag. Code freeze only between sign-off and tag.
- One combined docs PR for a release.
- A bug burn-down agent is always on (the issue count must trend down).
- Reviews: Opus for Gate, approvals, hotfix, release, browser and wake code (also CA and installer); Sonnet otherwise. Reviewers and fixers push at most once and never a plain merge-main. Every minor finding becomes an issue.
- New-feature Hebrew checklist: Hebrew goes in BOTH places (`hebrew_parts` json and the dashboard locale part) or `test_guides` and CI fail (memory note `new-feature-hebrew-both-places`).
- Stuck required check after a retarget (all green, still BLOCKED): remove and re-add the `no-automerge` label to trigger "Auto-label policy". Never close and reopen.
- Flake quarantine: a flake is a showstopper: quarantine it (non-blocking), open a `showstopper` issue, fix it before feature work in that area, close only with run evidence.
- Auto hotfix to the owner PC after each green batch (commands below). Tests must be clock-independent and hermetic.

## 8. Next steps by version

- 4.5.1: the 8 accepted known issues (#179 #261 #441 #509 #510 #543 #544 #605; the last three need owner GitHub settings), #620, #621, CodeQL #46/#47, the docs-only CI fast path, bug tables retired from the gate (OD-S2), wake gate PR #614 and the voice items once measured.
- 5.0 phone: phone sheet #193 (stays beta until he signs), trusted connection card (branch `trust-ca` slice 1; design `docs/plans/TRUSTED_PHONE_CONNECTION_2026-10-26.md` lives on that branch, not yet on main), PWA install, #509 #510.
- 6.0 drive: hands-free #219, car test #224 (beta until his car sheet).
- 7.0: G5 queue merges (#412), D2 OpenCode (#405), B2-B6 using the real-driver design [BROWSER_PILOT_REAL_DRIVER_2026-10-27](../archive/plans/BROWSER_PILOT_REAL_DRIVER_2026-10-27.md), Android A1 and on (#390), M5 live trial (#419, free-only, cap in code).

## 9. Known risks

- The sheet hash does not authenticate the owner (#605); there is no tag ruleset or protected environment yet (#543, #544). A release is only as safe as the GitHub account.
- The custom wake head has a 2.0 s deaf window at start (#594); it cannot be shortened, and a test pins it (#611).
- The wake candidate model was NOT applied. File: `C:\Users\97254\AppData\Local\Temp\claude\C--work-jarvis\86042260-00de-47fd-82db-c93505a1416d\scratchpad\retrain\hey_jarvis.npz` (temp folder; copy it before cleanup if still wanted). Reason: 7 of 44 near-miss false wakes.
- Mic setup lessons (memory note `mic-setup-lessons`): Realtek array as the default input, level about 0.80, about 30 cm; never the USB webcam mic; stop Core before recording; judge by median frame levels, not peaks; change the level in small steps. If wake is poor, check the mic first.
- Tests that use machine probes or the clock break CI; new always-on tools break the prompt budget (use lazy tools).

## 10. Commands to resume

- Watchdog: `bash C:\Users\97254\.claude\watchdog.sh` (CI and queue state line).
- Release state: `jarvis release status --version <ver> --report` (gate and blockers); dry run through the release workflow dispatch.
- Installed CLI: `C:\Users\97254\AppData\Local\Programs\JARVES\jarvis.cmd`. Hotfix flow (backup first, dry run first, silent): `jarvis hotfix`, then `jarvis selftest` and `jarvis doctor`; report the version and the revert id. Installers only from PowerShell with `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /RESTARTJARVES=1`.
- Queue: `gh pr list -R igalgerman/jarves`; `gh pr merge N --auto` (re-queue); `gh pr checks N`; `gh run list -R igalgerman/jarves --branch main --limit 5`.
- Local checks: from `core/`, `PYTHONPATH=src C:\work\jarvis\core\.venv\Scripts\python.exe -m pytest tests/test_docs_links.py`; `python core/tools/prepush.py` before pushing.
- Always branch from fresh `jarves/main` in `C:\work\wt\<branch>`; remove the worktree and branch the day it merges.
