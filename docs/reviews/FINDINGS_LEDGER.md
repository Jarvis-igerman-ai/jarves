# Findings ledger (P3 and nits)

Created 27 Oct 2026 under the owner-approved lean process.

## Ledger rules

- Nits and P3 findings live here, not as GitHub issues. Only real defects (P0-P2) become issues.
- An item re-opens as an issue when it bites a user or when an agent picks it up to fix.
- Items carrying `security`, `known-issue-4.5`, `release-blocker`, `showstopper` or owner-gated labels, or that mention user data loss or a privacy leak, stay as issues.
- Weekly sweep: once a week skim this table, fix cheap items together in one small PR, delete fixed rows, and re-open any that now matter.
- Add new P3 rows here (one row per finding, with the source PR or test).

## Open items

| # | Title | Area | Why it matters | Source |
|---|-------|------|----------------|--------|
| 258 | speedtest on owner PC: spawn_to_ready 13.6 s, core_start 14.1 s (WARN) | performance | Slow start-up on the owner PC; single run, may be noise | test |
| 314 | Skills Platform 2.0 hardening list (Opus review of #309) | skills (v6) | Small hardening gaps in the skills platform | review |
| 362 | Orchestrator UX: approvals expire in 60 s and re-issue ids, cost 0 until end, no mid-turn cost stop | orchestrator UX | Confusing approvals and late cost display | owner |
| 424 | Real-browser test for the bug-reports popover (EN and HE) | testing | No end-to-end test of the popover | test |
| A2-1 | Android CI: add the Gradle wrapper-validation action (needs a verified, pinned commit SHA) | android / supply chain | The wrapper jar is only checksum-pinned by distributionSha256Sum; needed before any non-debug build | review #680 |
| A2-2 | Android: Gradle dependency verification (verification-metadata.xml) | android / supply chain | Dependencies are version-pinned but not hash-verified; needed before any signed APK | review #680 |
| A2-3 | Android: replace the deprecated androidx security-crypto alpha (EncryptedSharedPreferences) with Keystore-backed storage | android / security | Deprecated alpha library holds the pairing token; needed before any non-debug build | review #680 |
| A2-4 | Android: the Keystore-backed session store and the 15-minute notification check have no instrumented test | android / testing | Keystore, Doze and OEM battery limits only behave on a real device; goes on the phone sheet (A7) | review #691 #700 |
| 425 | Windows toast fallback test when tray/popup cannot show | testing | Fallback path is untested | test |
| 426 | Installer restart smoke: silent install restores pre-install state (#383) | testing / installer | Restart behaviour not covered by a smoke test | test |
| 427 | Hotfix apply test on a real source tree (copy, apply, verify, rollback) | testing / hotfix | Only mocked trees are tested | test |
| 497 | GitHub write: reset-audit / deleting audit files reset the rate limit without popup | github write | Rate limit can be reset by local file edits (owner-local only) | review |
| 508 | Release gate: signed sheet has no owner identity or content hash | release gate | Sheet signature is not bound to the owner or content | review |
| 518 | release_flow: review findings from PR 514 | release | Minor review nits | review |
| 526 | Budget cap fixed at claude launch for long-lived resumed sessions | budget | Cap does not follow later changes | review |
| 538 | Release gate: sheet Version line not checked against tag; known-issue-4.5 applier not verified | release gate | Wrong-version sheet could pass | review |
| 545 | Release gate installer check is tautological in release.yml | release / CI | Check always passes, gives false comfort | review |
| 568 | hotfix recover/revert: minor findings from #560 review | hotfix | Minor review nits | review |
| 569 | github write: minor findings from #561 review | github write | Minor review nits | review |
| 580 | github writes: approve_batch skips READ preview and leaves a granted slot reserved after a refusal | github write | Missing warnings and a stuck slot on refusal | review |
| 581 | github writes: CI re-run popup shows GitHub text without stripping control/bidi characters | github write | Spoofable popup text | review |
| 589 | Minor findings from review of PR 587 and 588 | misc | Minor review nits | review |
| 594 | wake: custom head drops 100 start-up embeddings but burst is 91 | wake | 720 ms extra dead time after start/reset/resume | review |
| 595 | wake train relative gate: no absolute ceiling; baseline always stock hey_jarvis | wake | Gate can pass a worse model | review |
| 596 | wake train: held-out gate list has phonetic twins in training negatives | wake | Gate is weaker than it looks | review |
| 600 | Clean up local worktrees and branches under C:\work\wt | chore | Disk hygiene | owner |
| 618 | wake gate: fph ceiling skipped without baseline, no campaign ceiling, corrupt live head crashes train | wake | Gate holes and a crash on bad input | review |
| 619 | wake gate: baseline and candidate measured at different levels; two-stage confirm not modelled | wake | Gate numbers not comparable | review |
| 620 | hotfix restart seen as a crash: self-check runs with trigger=crash after planned stop | hotfix | Misleading crash report after a planned restart | test |
| 621 | Beta build numbers out of order (4.0.1.1215 older code than 4.0.1.1214) | release | Confusing version ordering | test |
| 624 | release signoff: explicit --repo that is not a checkout falls back to current folder | release signoff | Silent wrong-folder fallback | review |
| 625 | release signoff: legacy cells with double backslash lose one on re-save | release signoff | Old sheets re-save differently, generic hash message | review |
| 634 | flake scan reopens showstoppers for already quarantined/closed tests | CI | Noise from the flake scan | test |
| 614a | wake gate: 30 per hour false-wake cap only checked when a baseline exists; apply always | wake | Gate can pass a model with too many false wakes | review |
| 614b | wake gate: campaign-phrase comparison has no cap | wake | Campaign phrases can regress unnoticed | review |
| 614c | wake: relative voice.wake_word path resolved against cwd; a missing file silently falls back to the stock model with no note | wake | Wrong model without a hint | review |
| 614d | wake test: fph_budget assert passes when the value is None | wake | Weak test | review |
| 614e | wake gate: recall-margin check passes trivially when the live head is for another phrase | wake | Gate weaker than it looks | review |
| 667a | workflows other than release.yml pin first-party actions by tag (actions/checkout@v7, codeql-action@v4, cache@v6, upload-artifact@v7), release.yml pins by SHA; the pull_request_target workflows (autolabel, dependabot-automerge) use the tag form | CI | A moved tag runs new code in a workflow with a write token; pin by SHA and let Dependabot bump | review (agent 1 read-through 27 Oct) |

## Kept as issues

These P3 issues stay open because they carry the `security` label: #206 (V5 security P3 hardening list); #529 (Model Lab M3 minor hardening); #574 (browser pilot real-driver limits of pin_hosts/request_allowed); #575 (pilots minor gaps after #567 review); #623 (browser pilot pin cache race). No P3 issue carried the known-issue-4.5, release-blocker, showstopper or owner-gated labels or a data-loss/privacy title.
