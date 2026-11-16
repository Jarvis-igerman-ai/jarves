# Reconciling the 18 requirements with no issue link (JAR-97)

Date 2026-11-09. Base: `origin/main` e52fe5c4. Proposal only: no code or plan data changed, nothing here authorises work.
No device, phone, car or live sign-in was exercised. Every device outcome below is UNVERIFIED.

## Why this gap

`docs/NOW.md` lists "Map V4.5.1/V5/V6/V7 scope and GitHub state" as authorised work and states that 18 of 94 requirements lack a granular issue mapping. JAR-66 named this only as a closing note and JAR-81 did not touch it. No open PR or Paperclip task covers it, including #1000, #1006 and #1002.

## Problem

An empty `issue_numbers` reads as "nothing happened". For most of these rows that is wrong: merged work exists. For a few it is right, and the tracker cannot tell the two apart.

## Evidence

Method: `core/src/jarves/workflow/version_plan.json` at the base above, matched against `gh` snapshots taken 9 Nov (381 issues, 562 merged PRs, both below the 1000-row limit). I searched titles by keyword and searched issue and PR bodies, plus `git log`, for the exact requirement IDs. Then I checked code and test names for selected rows.

**Limits:** matches in groups A and B1 name the requirement or its exact subject. Group B2 matches are by title and are candidates; their diffs were not read against each acceptance criterion.

| Group | Meaning | Rows |
|---|---|---|
| A | A specific issue exists | 2 |
| B1 | No issue; a merged PR names the requirement ID | 4 |
| B2 | No issue; candidate merged PR by title | 8 |
| C | No implementation link found | 4 |

| Requirement | Validation level | Group | Link found | What is still unproven |
|---|---|---|---|---|
| `V451-SKILL-REDACTION` | automated | A | Issue #862 (closed); PRs #728, #864, #884 | Live state of CodeQL alerts 46 and 47: UNVERIFIED, not queried |
| `V451-INSTALL-LATEST` | app | A | Issue #644 (closed); PRs #650, #802; `test_install_latest.py` | Installed run, including restore after a failure: UNVERIFIED |
| `V451-MERGE-PROOF` | automated | B1 | PR #753 by ID; PR #678 by title | Whether both criteria are met was not checked |
| `V451-WATCHDOG` | automated | B1 | PR #747 (`test_workflow_release_health.py`) | Built on the workflow deprecated 8 Nov; see decision 1 |
| `V5-CORE-WIRING` | app | B1 | PR #810 | Installed app check: UNVERIFIED |
| `V5-ALWAYS-ON` | app | B1 | PR #811 | Boot start and firewall prompt on a real PC: UNVERIFIED |
| `V451-WAKE` | device | B2 | PR #614 (already in `related_pr_numbers`); `jarvis wake test` exists | Owner run on the real microphone: UNVERIFIED |
| `V451-RELEASE-RUN` | app | B2 | PR #645; `test_release_run.py` | Real release run: UNVERIFIED |
| `V5-PAIRING` | device | B2 | PRs #25, #360; open P1 issue #357 is related but narrower | Emulated pairing passes in `SWV5_ACCEPTANCE.md`; real Android: UNVERIFIED |
| `V5-MOBILE-API` | app | B2 | PR #25; `test_mobile_access.py` covers scopes, rate limits, audit | Installed app check: UNVERIFIED |
| `V5-PROJECT-MANAGER` | app | B2 | PRs #88, #94; `test_projectmgr.py` | Real agent demo, which the row says fakes cannot replace: UNVERIFIED |
| `V5-TAILSCALE` | device | B2 | PR #676, decision PR #33; `test_tailnet.py` | Real tailnet: UNVERIFIED |
| `V5-PHONE-APPROVALS` | device | B2 | Decision PR #33; refusal path in `mobile.py` | Emulated pass; "the PC acting on a real tool approval" is still open in `SWV5_ACCEPTANCE.md` |
| `V5-PRIVACY-DEFAULTS` | automated | B2 | Decision PR #33; `NEW_INSTALL_DEFAULTS` note in `config.py`; `FULL_TEXT_HOURS = 24` in `logs.py` | My test-name search did not confirm both criteria; needs a direct read |
| `V5-LIVE-AGENTS` | app | C | None | Live run needs owner approval for subscription and project |
| `V5-LIVE-GOAL` | app | C | None for a live run; goal executor PR #55 is the feature, not the proof | Local Skill run: not done |
| `V5-PHONE-PERF` | device | C | None; PR #809 and issue #122 are PC voice latency | Same as JAR-66 gap 5 |
| `V6-LIVE-TRAFFIC` | app | C | None | Optional, gated on provider choice and spend. `journey.py` already labels estimates "no live traffic" |

## Facts that shape the options

1. **A closed issue changes tracker data.** `sync.py:290` sets `source_closed` when all linked issues are closed. The label at `sync.py:294` says this is not installed-app verification, so a link must never be read as "done".
2. **PR links are not read.** No Python file refers to `related_pr_numbers`, and `sync.py:285` counts a row with no issue as unmapped. Group B cannot leave the unmapped list without an issue.
3. **Mapping an open issue removes it from another list.** `sync.py:176-181` skips mapped issues when adding version-labelled extras, so linking #357 to pairing could hide it from that list. The label was not checked.
4. **The consumer is deprecated.** `sync.py` and `store.py` belong to the workflow retired on 8 Nov. The plan file is still the only scope list, and JAR-66 relied on it.
5. **`VERSION_MAPPING_2026-10-30.md` forbids** a fabricated issue, an unrelated number or a shared acceptance issue as a substitute link.

## Options

| Option | What | Unmapped before → after | Cost | Benefit | Verdict |
|---|---|---|---|---|---|
| A. Keep this table as the record | No data change | 18 → 18 | None | Owner can see what exists now; tracker stays stale | **Recommended first step** |
| B. Link only group A | Add #862 and #644 to `issue_numbers`, after decision 1 | 18 → 16 | S, one data line each | Exact and low risk, but small | **Recommended if decision 1 is yes** |
| C. Open one issue per row | Up to 18 new issues | 18 → 0 | M, plus tracker noise | Count looks finished; proves nothing | Reject: AGENTS rule 8 reserves issues for real defects, and the count would flatter the state |
| D. Extend sync to read PR links | Treat `related_pr_numbers` as a link | 18 → 6 if all 12 group B rows were accepted | M, code in a deprecated module | Clears group B | Reject for now: invests in retired code, and group B2 is unconfirmed |

## Recommendation

Take option A now, then option B only if the owner says the plan file remains the scope list. Route the unproven rows into work that already has a home:

- Device and live rows join the owner test list from JAR-66 gap 1 (`V451-WAKE`, `V5-PAIRING`, `V5-TAILSCALE`, `V5-PHONE-APPROVALS`, `V5-LIVE-AGENTS`, `V5-LIVE-GOAL`).
- `V5-PHONE-PERF` stays JAR-66 gap 5.
- `V6-LIVE-TRAFFIC` stays optional and out of any completion count.

**Size:** S. **Tier:** this PR is docs only. `risk_class.py` reports no high-risk path for `version_plan.json`; recheck on the final file list.

## Owner decisions needed (no code until answered)

1. Does `version_plan.json` remain the product scope list now that the workflow is deprecated? Recommended: yes, as plain data, until a replacement exists.
2. Should `docs/NOW.md` name product scope? JAR-66 and JAR-81 raised this too; it is listed here only because option B depends on it.

## Risks

- **False confidence:** the largest risk. A filled link can read as completion, so every row keeps its unproven column.
- **Privacy and security:** none added. Read-only analysis; no outside service beyond GitHub metadata for this repo.
- **Cost:** none at runtime.

## Reproduce the count

```
python -c "import json;d=json.load(open('core/src/jarves/workflow/version_plan.json',encoding='utf-8'));r=[x for v in d['versions'] for x in v['requirements']];print(len(r),sum(not x['issue_numbers'] for x in r))"
```

Expected on the base above: `94 18`.

## Real-device verification

This analysis needs none. Every device or live row above stays UNVERIFIED until the owner runs it.
