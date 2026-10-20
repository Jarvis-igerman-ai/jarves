# JARVES — legacy repository migration audit (20 October 2026)

**Scope:** repository migration only (owner request of 20 Oct 2026: "finish the migration, make sure all is good and nothing is lost; look at the issues"). No V4 feature code was written. The legacy private repo `igalgerman/JARVIS` was only **read**, with one exception the owner authorized: closing finished legacy issues. All work here is in the public repo `igalgerman/jarves`, as draft PRs.

**Method.** The legacy repo was mirror-cloned into a temp folder outside every working tree. For each of its **59 refs** (58 branches + 1 tag), the audit diffed the files changed since the merge base with legacy `main` and looked up each file's **git blob hash** in every public ref: `main`, draft PRs #6-#11 and all of public history. "Preserved" means that exact content exists in public. "Superseded" means public holds a newer version of the same file (verified line by line where it mattered). No private content was copied into this report.

## 1. Summary

| Result | Count |
| --- | --- |
| Legacy refs audited | 59 (58 branches + tag `win-v0.1.0.82`) |
| Legacy `main` itself, preserved in public `main` (except 38 intentionally private files) | 1 |
| Fully preserved in public `main` (merged into legacy `main` first) | 36 |
| Preserved through draft PR #11 (merged into legacy `v4-final`) | 10 (`v4-final` + B, C, D, G, idle-writes, lint-fix, M, R01, wake-train) |
| Gaps found and closed by this audit (draft PR #12) | 3 refs: `v3-priorities`, `v3-plan`, `v4-p01-verify` |
| Superseded, nothing to port | 2 (`v3-p02-dashboard`, `v3-v4-plan`) |
| Intentionally private, must not be ported | 1 (`v3-public-sync`: snapshot tool containing the personal email and redaction strings) + 38 private files on legacy `main` |
| Not ported, owner decision (V5 mobile) | 1 (`demo/android-apk-20261019`) |
| **Still being written by active agents: not fully public yet** | 5 (`v4-e-orchestrator`, `v4-f-whatsapp`, `v4-f-whatsapp-go`, `v4-k-dashboard`, `v4-o-selfdiag2`) |

**Bottom line:** nothing of value is lost **today**: everything not yet public still sits in legacy branches, and a GitHub Archive keeps them readable. The only real loss risk was the owner's decision record (`docs/plans/PRIORITIES.md`). It was missing from **both** `main` branches and is now restored in PR #12. The five active-agent branches must be ported **after** their agents stop. `v4-f-whatsapp` (the Python side of WhatsApp) has **no public PR at all**.

## 2. Legacy refs (head at audit time; `->` = newer head pushed during the audit)

"Commits" = commits not in legacy `main`. "Files" = files added or changed against the merge base with legacy `main` (deletions not counted). "Sensitive" = private content on that ref that must stay out of public.

| Legacy ref | Head | Commits | Files | Where preserved | What is missing | Sensitive? |
| --- | --- | ---: | ---: | --- | --- | --- |
| `demo/android-apk-20261019` | `46b780b` | 7 | 4 | legacy branch only | Android demo APK pipeline: workflow, Godot export preset (gitignored), 2 Godot tweaks; not ported (V5 mobile scope; the preset is gitignored by policy and needs an owner decision). Actions artifact expires 2 Nov 2026 (rebuildable) | no (debug keystore generated in CI) |
| `main` | `a13b56d` | 0 | 931 | public `main` (+#11 for NOW.md) | none: 38 private-only files (3 log/conversation reviews, 35 transcript/conversation/log screenshots) and the private deprecation banners in README/AGENTS are intentionally not public; 91 files exist in public history and are superseded by newer public versions | yes (38 private files) |
| `swv2-integrate` | `e95eb6f` | 0 | 0 | merged in legacy `main` -> public `main` | none | no |
| `v3-approvals` | `e4a7517` | 0 | 0 | merged in legacy `main` -> public `main` | none | no |
| `v3-campaign-dash` | `c8ab0fe` | 0 | 0 | merged in legacy `main` -> public `main` | none | no |
| `v3-campaign-features` | `9b6c82e` | 0 | 0 | merged in legacy `main` -> public `main` | none | no |
| `v3-campaign-voice` | `58ff3e9` | 0 | 0 | merged in legacy `main` -> public `main` | none | no |
| `v3-ci-fix2` | `85a9da1` | 0 | 0 | merged in legacy `main` -> public `main` | none | no |
| `v3-ci-yaml-fix` | `4f3b10c` | 0 | 0 | merged in legacy `main` -> public `main` | none | no |
| `v3-cli-english` | `cbff257` | 0 | 0 | merged in legacy `main` -> public `main` | none | no |
| `v3-connect` | `c99097f` | 0 | 0 | merged in legacy `main` -> public `main` | none | no |
| `v3-final` | `c868f37` | 0 | 0 | merged in legacy `main` -> public `main` | none | no |
| `v3-final-plus` | `e01fd27` | 0 | 0 | merged in legacy `main` -> public `main` | none | no |
| `v3-fix-flake` | `d3bce58` | 0 | 0 | merged in legacy `main` -> public `main` | none | no |
| `v3-fix-show-twice` | `d42312f` | 0 | 0 | merged in legacy `main` -> public `main` | none | no |
| `v3-flaky-tests` | `30598c2` | 0 | 0 | merged in legacy `main` -> public `main` | none | no |
| `v3-flaky-tests2` | `214a3da` | 0 | 0 | merged in legacy `main` -> public `main` | none | no |
| `v3-linux-fixes` | `352b0cb` | 0 | 0 | merged in legacy `main` -> public `main` | none | no |
| `v3-minor-fixes` | `3b9ff5c` | 0 | 0 | merged in legacy `main` -> public `main` | none | no |
| `v3-owner-answers` | `2bf81c4` | 0 | 0 | merged in legacy `main` -> public `main` | none | no |
| `v3-p02-dashboard` | `31fb0b2` | 2 | 23 | superseded | closed legacy PR #45; its Hebrew server texts reached main via PRs #36/#48 and later edits; the 2 extra commits are a merge + Hebrew strings for telegram/instagram features, superseded by the English+Hebrew catalog | no |
| `v3-p02b-hebrew` | `6cd7ef6` | 0 | 0 | merged in legacy `main` -> public `main` | none | no |
| `v3-p04-qa` | `7fedeb6` | 0 | 0 | merged in legacy `main` -> public `main` | none | no |
| `v3-p1-channels` | `a731949` | 0 | 0 | merged in legacy `main` -> public `main` | none | no |
| `v3-p1-instagram` | `42e4b58` | 0 | 0 | merged in legacy `main` -> public `main` | none | no |
| `v3-plan` | `cd8eabf` | 1 | 1 | **PR #12 (new)** | was missing: one owner answer in `FULL_TEST_PLAN.md` | no |
| `v3-popup-tune` | `23fb625` | 0 | 0 | merged in legacy `main` -> public `main` | none | no |
| `v3-popup-ux` | `1f1b6e7` | 0 | 0 | merged in legacy `main` -> public `main` | none | no |
| `v3-priorities` | `612278e` | 5 | 1 | **PR #12 (new)** | was missing: full owner priorities/answers in `docs/plans/PRIORITIES.md` | names removed |
| `v3-profiles` | `58e1099` | 0 | 0 | merged in legacy `main` -> public `main` | none | no |
| `v3-pronounce` | `a0318f7` | 0 | 0 | merged in legacy `main` -> public `main` | none | no |
| `v3-public-sync` | `04c0e92` | 4 | 3 | intentionally private | `scripts/make_public_snapshot.py`, its test and `docs/PUBLIC_REPO.md`: private-to-public sync tooling, obsolete since 20 Oct; contains the personal email and redaction strings of owner speech, so it must stay private | **yes** |
| `v3-research` | `028fa0e` | 0 | 0 | merged in legacy `main` -> public `main` | none | no |
| `v3-say-fix` | `0b62d5c` | 0 | 0 | merged in legacy `main` -> public `main` | none | no |
| `v3-selfdiag` | `c785efd` | 0 | 0 | merged in legacy `main` -> public `main` | none | no |
| `v3-v4-plan` | `841a0d9` | 1 | 2 | superseded | early 281-line V4 proposal; public `main` has the 1147-line approved work package (contains every line of legacy `main`'s version) | HANDOFF only |
| `v3-version-fix` | `7400092` | 0 | 0 | merged in legacy `main` -> public `main` | none | no |
| `v3-voice-fixes` | `fa0e424` | 0 | 0 | merged in legacy `main` -> public `main` | none | no |
| `v3-wake` | `3dfdf29` | 0 | 0 | merged in legacy `main` -> public `main` | none | no |
| `v4-b-privacy` | `0955461` | 11 | 74 | merged in `v4-final` -> PR #11 | none (intermediate versions superseded by the merged `v4-final` files) | HANDOFF only |
| `v4-c-review` | `50aef1b` | 6 | 22 | merged in `v4-final` -> PR #11 | none (intermediate versions superseded by the merged `v4-final` files) | HANDOFF only |
| `v4-d-delegation` | `027e0bd` | 50 | 138 | merged in `v4-final` -> PR #11 | none (intermediate versions superseded by the merged `v4-final` files) | HANDOFF only |
| `v4-e-orchestrator` | `d25413a` -> `0b15eb3` | 68 | 180 | partly: draft PR #7 | ACTIVE AGENT: 3 new files not public (`orch_proc.py`, `test_agents_system.py`, `SWV4_E_ORCHESTRATOR_RESEARCH.md`); 18 modified/newer files to reconcile; port after the agent stops | HANDOFF only |
| `v4-f-whatsapp` | `416e56c` -> `3546800` | 68 | 166 | **not public yet** | ACTIVE AGENT: 14 new files not public (`whatsapp.js`, `whatsapp_bridge.toml`, `whatsapp_admin.py`, `whatsapp_bridge.py`, `whatsapp_policy.py`, `whatsapp_store.py`...); 19 modified/newer files to reconcile; port after the agent stops | HANDOFF only |
| `v4-f-whatsapp-go` | `a4fd753` -> `995999d` | 48 | 156 | partly: draft PR #6 | ACTIVE AGENT: 6 new files not public (`.gitattributes`, `.gitignore`, `main.go`, `wm.go`, `wm_test.go`, `build_whatsapp_bridge.ps1`); 5 modified/newer files to reconcile; port after the agent stops | HANDOFF only |
| `v4-final` | `a226def` | 62 | 150 | draft PR #11 (`v4/integrated-b-c-d-g-m-r01`) + `main` | only `docs/HANDOFF.md` (private session history, intentionally not copied) | HANDOFF only |
| `v4-g-recall` | `bb5f0f5` | 11 | 26 | merged in `v4-final` -> PR #11 | none (intermediate versions superseded by the merged `v4-final` files) | HANDOFF only |
| `v4-idle-writes` | `9860805` | 26 | 70 | merged in `v4-final` -> PR #11 | none (intermediate versions superseded by the merged `v4-final` files) | HANDOFF only |
| `v4-k-dashboard` | `c6ab0d1` -> `f2ba321` | 67 | 199 | partly: draft PR #10 | ACTIVE AGENT: 3 new files not public (`test_dashboard_a11y.py`, `catalog_sync.py`, `SWV4_K_DASHBOARD.md`); 66 modified/newer files to reconcile; port after the agent stops | HANDOFF only |
| `v4-lint-fix` | `71686b7` | 38 | 123 | merged in `v4-final` -> PR #11 | none (intermediate versions superseded by the merged `v4-final` files) | HANDOFF only |
| `v4-m-perf-ci` | `5610cd7` | 3 | 15 | merged in `v4-final` -> PR #11 | none (intermediate versions superseded by the merged `v4-final` files) | HANDOFF only |
| `v4-o-selfdiag2` | `ab57b87` -> `4279ba9` | 73 | 181 | partly: draft PR #8 | ACTIVE AGENT: 0 new files not public; 21 modified/newer files to reconcile; port after the agent stops | HANDOFF only |
| `v4-p01-verify` | `67c85f7` | 1 | 1 | **PR #12 (new)** | was missing: `docs/reviews/SWV4_P01_VERIFY.md` | city removed |
| `v4-p03-parity` | `133dc75` | 0 | 0 | merged in legacy `main` -> public `main` | none | no |
| `v4-plan-full` | `79d4ad3` | 0 | 0 | merged in legacy `main` -> public `main` | none | no |
| `v4-r01-defense` | `3ba9fe4` | 49 | 133 | merged in `v4-final` -> PR #11 | none (intermediate versions superseded by the merged `v4-final` files) | HANDOFF only |
| `v4-wake-train` | `fd08da8` | 1 | 5 | merged in `v4-final` -> PR #11 | none (intermediate versions superseded by the merged `v4-final` files) | HANDOFF only |
| `v4-whatsapp-research` | `ce8215b` | 0 | 0 | merged in legacy `main` -> public `main` | none | no |
| `win-v0.1.0.82` | `bf2d8ff` | 0 | 0 | merged in legacy `main` -> public `main` | none | no |

Closed legacy PRs whose branches are deleted (heads remain readable as `refs/pull/*` after archive): PR #30 "V3 batch 1" (content landed through the merged PRs #31-#34) and PR #11 "PR workflow + agent review" (superseded by the automerge workflow of legacy PR #47). All 22 legacy PRs are merged or closed; none is open.

**Caveat:** "preserved" is not "merged". PR #11 (B/C/D/G/M/R01/idle-writes/wake) and PRs #6-#10 are drafts. Their CI is red (section 6).

## 3. Gaps closed by this audit

| Draft PR | Files | Legacy source | Sanitizing |
| --- | --- | --- | --- |
| [#12](https://github.com/igalgerman/jarves/pull/12) `migration/owner-decisions` | `docs/plans/PRIORITIES.md` (full owner priorities and ~8 answers restored; `main` only had the last 2 bullets) | `v3-priorities` `612278e` | personal first names in pronunciation decisions replaced; superseded "private dev home" rule marked |
| #12 | `docs/plans/FULL_TEST_PLAN.md` (+1 owner answer: approval timeouts, self-check time, profiles) | `v3-plan` `cd8eabf` | none needed |
| #12 | `docs/reviews/SWV4_P01_VERIFY.md` (real-Pear 12/13, Google read-only G1-G6 PASS, wake BLOCKED, bugs B1-B5) | `v4-p01-verify` `67c85f7` | configured city replaced |

Already public, as the brief asked to check: `docs/reviews/SWV4_WAKE_TRAIN.md` + wake training code and tests (`main` and #11), `docs/plans/WHATSAPP_REAL_ACCESS.md` and every other `docs/plans/*.md` (`main`), the `v4-idle-writes` fix and `docs/reviews/SWV4_IDLE_WRITES.md` (#11), `v4-lint-fix` (#11), and every `docs/reviews/SWV3_*` report (`main`).

**Not ported:**
- **Android demo** (`demo/android-apk-20261019`, 4 files). It contains a Godot export preset that `.gitignore` excludes on purpose (export presets can hold keystore passwords; this one has empty keystore fields). The owner should decide when V5 mobile work is scoped.
- **Snapshot tool** (`v3-public-sync`). It must stay private.
- **Active-agent files.** They are still changing.

## 4. Non-git data that an archive could hide or that expires

| Item | Legacy private repo | Public repo / releases | Action |
| --- | --- | --- | --- |
| Actions secrets (names only) | `JARVES_UPDATE_SIGNING_KEY`, `RELEASES_TOKEN` | `AUTOMERGE_TOKEN`, `JARVES_UPDATE_SIGNING_KEY`, `RELEASES_TOKEN` | all present on public; nothing to move. Owner: after archive, revoke/rotate the legacy-scoped tokens if they were created for the private repo only |
| Actions variables, environments, deploy keys, webhooks | none | none | none |
| Releases | `win-v0.1.0.82` (installer + sha256 + build-info) | `igalgerman/jarvis-releases`: `win-v3.0.0.9` (latest, with `latest.json`), `win-v0.1.0.82` (+ a stale **draft** duplicate); `jarves`: `v3.0.0`, `win-v3.0.0.9`, `win-v0.1.0.5` | 0.1.0.82 installer already mirrored in jarvis-releases; owner may delete the stale draft there |
| Actions artifacts | 24 (22 PR installer builds, 2 main installer builds, 1 Android demo APK) | n/a | all expire by 3 Nov 2026; all rebuildable from source; nothing unique |
| Wiki / Pages | disabled / none | n/a | none |
| Projects | repo flag on; listing needs the `read:project` token scope (not granted) | n/a | owner: check github.com/igalgerman?tab=projects once |
| Labels | GitHub defaults + `accessibility`, `automerge` | defaults + `accessibility` | `automerge` label is missing on public. The public automerge workflow (`automerge.yml`) selects PRs by this label, so it needs it (owner/agent: `gh label create automerge -R igalgerman/jarves`) |
| Milestones | none | none | none |
| Issue/PR comments of value | in legacy #3, #4 (design delivery evidence), #15, #17 (live reports), #49 (full spec) | now copied as checklists into public #1-#4 | done |

## 5. Issue triage

### Legacy private issues (17 open at audit start)
| Legacy | Decision | Evidence / reason | Public home |
| --- | --- | --- | --- |
| #17 SW-V2 umbrella | **CLOSED (done)** | SW-V2 WPs merged (legacy PR #29) and shipped in `v3.0.0`; leftovers copied | #1 |
| #14 client delegation | open, **needs owner** | package D built; live 51-61 PASS; OD-15 default and scenario 62 (admin key, OD-05) | #1 |
| #15 privacy redaction | open, **needs review + CI** | package B: 0 leaks in the planted audit; independent review and final-SHA CI URL pending; OD-12 | #1 |
| #16 voice/music review | open, **needs owner** | package C: 9 fixes; real-Pear stalled-video + S10 re-run and paid voice regression pending | #1 |
| #24 Google/Connections | open, partly done | Windows first connect + read-only checks done (P01); 7-day reconnect and live privacy switch pending | #1 (+ Pi part in #3) |
| #25 guides/help | open, **needs owner** | only headless/virtual runs | #1 (+ Pi part in #3) |
| #28 privacy strip/camera | open, **needs owner** | fake camera only; real webcam run pending | #1 (+ Pi part in #3) |
| #49 orchestrator | open, in progress | package E agent active | #2 |
| #18-#23 Pi parity | open, deferred by owner | no Pi hardware | #3 |
| #3, #4, #8 design / hardware | open, deferred by owner | design deliverables done; owner visual sign-off and hardware gates pending | #4 |

The other legacy issues (#1, #2, #5-#7, #9, #10, #13, #26, #27) were already closed; #11 and #12 are PRs.

### Public issues
| Public | Decision | Change made |
| --- | --- | --- |
| #1 V4 release / acceptance | still relevant | body now carries the verbatim legacy checklists of #14, #15, #16, #17 leftovers, #24, #25, #28 with evidence-based ticks; owner checklist posted as a comment |
| #2 Orchestrator | still relevant | body carries #49's P0-P3 checklists, MVP definition and safety contract |
| #3 Pi parity | still relevant (deferred) | body carries every checklist from #18-#23 plus the Pi parts of #24/#25/#28 |
| #4 Physical design | still relevant (deferred) | body carries #3/#4/#8 acceptance with delivered items ticked |
| #5 Consolidation | still relevant | this report; close only after the remaining owner actions below |

## 6. Public state checks

| Check | Result |
| --- | --- |
| README / NOW / HANDOFF / AGENTS | all name `igalgerman/jarves` as the only active repo; legacy links remain only as history. **Stale active-looking links:** README line "CI ... [Issue #2]" points at a legacy issue; product code still links legacy issues as roadmap URLs (`capabilities.py` x6, `updates.py` x3 incl. fallback `REPO = "igalgerman/JARVIS"`, `appearance.py`, `backup.py`, `usage.py`, `operations.py`, `runtime/__init__.py`, `skills/generated.py`, `guides/__init__.py`) and `packaging/windows/jarves.iss` `AppPublisherURL`. `capabilities.py` even validates that roadmap URLs match the legacy repo. Follow-up: one small code PR after the active agents are merged, so it does not collide with their edits to the same files. |
| Update / installer path | effective source is `https://github.com/igalgerman/jarvis-releases/releases/latest/download` (`updates.py` `DEFAULT_RELEASES_REPO`; a trusted Ed25519 key is built in, so the hosted path wins). `latest.json` is present on `win-v3.0.0.9`. `package.yml` publishes with `RELEASES_TOKEN` and signs with `JARVES_UPDATE_SIGNING_KEY`; both exist on public by name |
| CI on public `main` | **RED.** Latest `main` run at `973de2c`: [run 39482195049](https://github.com/igalgerman/jarves/actions/runs/39482195049): `test_redaction_audit.py::test_planted_data_audit_of_a_real_core[privacy_on/off]` time out on Ubuntu + Windows, and the Windows performance gate fails (`idle_disk_write_kb_per_min` 156.6 > 60). Last green `main`: [run 39427183491](https://github.com/igalgerman/jarves/actions/runs/39427183491) at `29fbc83`. The idle-write fix is in draft PR #11, whose own run [39483110366](https://github.com/igalgerman/jarves/actions/runs/39483110366) is also red. Draft PRs #6-#10 are red (expected: added files only). `nightly.yml` has no run yet |
| Secret / private-data scan (patterns of the legacy snapshot tool) over **every blob in all public refs and history** (1336 blobs, 12 refs incl. tags) | personal email **0**; `ghp_`/`github_pat_`/`sk-`/`AIza`/private-key blocks: **0 real**, 3 test dummies; `*.db` / `secrets.env` / `*.log` files **0**; the 3 private review docs **0**; transcript/conversation/log screenshots **0**; Israeli phone-like numbers: only synthetic test values (`054-1234567` style). **Low-risk finding:** 14 blobs (mostly old `docs/HANDOFF.md` versions, 2 CAD scripts, 2 reviews) contain the owner's local Windows profile path (a numeric account name, no real name). A history rewrite is not recommended; drop them from current docs/scripts when those files are next edited |

## 7. Risks

1. **Active agents keep pushing to the legacy repo** (all five heads moved during this audit). Until they stop and their branches are ported, the legacy repo is the only copy of that work. **Do not archive yet.**
2. `v4-f-whatsapp` (Python bridge client, 14 new files) has **no public preservation PR**.
3. Public `main` CI is red, and PR #11, which fixes the perf gate, is also red. "V4 integrated" cannot be claimed.
4. Product code still points users at legacy issue URLs that the public cannot open.
5. The private snapshot tool and the 38 private review files exist only in the legacy repo. After archive they stay readable to the owner only. That is intended, but a local backup of the legacy mirror is advisable.

## 8. Exact remaining owner actions

1. Let the five active agents (E, F, F-go, K, O) finish. Then have **one** integration agent port their branches into public PRs: the new files listed in section 2 plus a 3-way reconcile of the modified files against public `main`/#11.
2. Answer the open decisions in public #1: OD-15 (default delegation mode), OD-05 (read-only OpenAI admin key), OD-12 (`log.privacy` default). Approve a bounded paid voice regression inside the $25 V4 cap. Run the owner-present checks for Pear, Google reconnect, guides, webcam and privacy strip.
3. Decide on the Android demo branch: port it during V5 or drop it.
4. Review and merge draft PR #12 (docs only).
5. Optional: create the `automerge` label on public; delete the stale draft release in `jarvis-releases`; check user Projects.
6. **Archive the legacy repo yourself** (GitHub -> `igalgerman/JARVIS` -> Settings -> General -> Danger Zone -> Archive this repository), **only after** steps 1-2 of the integration are done and no agent pushes there any more. Do not delete it. Then revoke legacy-only tokens and repoint any local clones (`git remote set-url origin https://github.com/igalgerman/jarves.git`) once their work is saved.

*Audit tooling (blob-hash comparison, scanner) ran from a temp folder outside the repository; no legacy content other than the three sanitized files in PR #12 was written to public.*
