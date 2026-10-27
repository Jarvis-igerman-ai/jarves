# Simple releases: one flow, under 20 minutes of owner time

**Status:** design, 26 Oct 2026. Owner order: "the next releases must be much simpler".
**Slice 1 (this plan, first PR after it):** `jarvis release status` and `jarvis release sheet` (evidence report plus the reduced owner sheet).

## 1. The problem today

A stable release (4.0.1 as the example) needs: a 36-row manual sheet (about 90 minutes), bug tables with 32 open rows that
nobody keeps current, checklist rows, a hand-written `SIGN_OFF_<v>.md`, a separate `SIGNED_<v>` file, nightly and main checks
read by hand, manual `jarvis hotfix` runs and a dozen scattered commands (`manual_test_sheet.py`, `release_gate.py`,
`release_status.py`, `jarvis release signoff` per row, `git tag`). Most rows re-test what CI already proves on every PR
(guides, dashboard pages, installer smoke, changelog items). The owner is the bottleneck for work that machines already did.

## 2. The rule that does not change (hard rules kept)

- The owner's signature. Without it no stable tag.
- Zero open P0-P2 issues. Main green. Latest nightly green (at most 2 days old).
- No force push, no bypass of checks, no tag from an unverified commit. `release_gate.py` stays the single judge and stays
  strict: this plan changes what feeds it, not what it demands.

## 3. What to cut, merge or automate

| Today | Decision | After |
|---|---|---|
| 36-row manual sheet, 18 "Changed" rows, guide row, page row, 15 standing rows | CUT to a smoke set of 5 rows on the owner's PC; every other row is recorded `accepted-blocked` with the note "accepted by automation" plus evidence (CI run, test file) | one short table, about 15 minutes |
| Bug tables `docs/reviews/*BUG_TABLE*.md` (32 open rows, stale) | REPLACE by live GitHub labels P0-P2 as the only source. Tables stay as history, the gate stops reading them in slice 3 (after one release proves the issue labels cover them) | `gh issue list --label P0,P1,P2` is the truth |
| `RELEASE_CHECKLIST_<v>.md` rows | MERGE into the evidence report: every row is a machine check with a live value | no hand-kept checklist |
| `SIGN_OFF_<v>.md` written by hand | AUTOMATE: generated as the evidence report `docs/release/EVIDENCE_<v>.md` | nothing to write |
| `SIGNED_<v>` file | AUTOMATE (slice 2): created from the owner's single sign-off command; same `owner/date/commit` lines, so the gate is unchanged | one confirmation |
| Scattered commands | MERGE into `jarvis release` (status, sheet, signoff, later `--tag`) | one command to remember |
| Beta rolls out | already automatic on every green main (`ENABLE_BETA_PUBLISH`); keep | nothing |
| Stable | = PROMOTE a beta that has been green and that the owner smoke-tested; the tag commit is that beta's commit (docs-only ancestors allowed, as today) | no rebuild, no second test |
| Auto hotfix after green merges (`jarvis hotfix`) | KEEP as is | unchanged |
| "five green nights" | already gone (26 Oct); the nightly stays one fresh green run | unchanged |

## 4. The one flow (`jarvis release`)

1. `jarvis release status` runs every automatable gate and prints PASS or TODO per line: main green (jarves-checks on the
   head of main), latest nightly fresh and green, open P0-P2 issues (live labels), the sign-off sheet, the SIGNED file.
   It ends with exactly what remains for the owner. `--report` writes `docs/release/EVIDENCE_<v>.md`.
2. `jarvis release sheet --version X.Y.Z` writes the reduced owner sheet (the existing `MANUAL_TEST_<v>.md` format, so the
   gate, the dashboard page and `jarvis release signoff` keep working unchanged).
3. The owner updates to the newest beta, does the smoke rows, then `jarvis release signoff --version X.Y.Z --finish`.
4. (slice 2) `jarvis release sign` writes `SIGNED_<v>`, and tags only after `status` says READY. The tag runs `release.yml`
   and the unchanged gate.

## 5. The smoke set (owner, on his PC, about 15 minutes)

| ID | What | Time |
|---|---|---|
| S1 | `jarvis update --channel beta`, then `jarvis doctor` shows no red line | 4 min |
| S2 | Say "היי ג'רוויס, מה השעה?": wakes and answers in Hebrew | 2 min |
| S3 | Interrupt an answer: it stops fast and listens | 1 min |
| S4 | Medium-risk action by voice: popup on top, say "yes" approves once | 4 min |
| S5 | Update and rollback keep the data (`jarvis update --rollback`, then forward again) | 4 min |

Everything else (changelog items, guides, dashboard pages, installer, approvals logic, selftest) is proven by CI and the
nightly; the sheet records those rows as `accepted-blocked` with a note that names the evidence, so the signature still
covers the whole release and nothing is silently dropped. A feature that CI cannot prove (phone, car, hardware) keeps its own
owner-gated row, added by hand with `--extra`, exactly as now.

## 6. Before and after

| | Before (4.0.1) | After |
|---|---|---|
| Owner manual time per stable | about 90 min (36 rows) | under 20 min (5 rows) |
| Documents the owner or an agent maintains | sheet, bug tables, checklist, SIGN_OFF, SIGNED | none by hand (generated) |
| Commands to know | 8 or more | `jarvis release status / sheet / signoff` |
| Source of "open P0-P2" | 32 table rows plus issues, they disagree | GitHub labels only |
| Gate strictness | full | full (unchanged) |

## 7. Slices

1. This document.
2. `jarvis release status` (evidence plus what remains), `jarvis release sheet` (reduced owner sheet), tests, guide EN+HE,
   CLI parity, feature row. No new model-facing tools, so ZERO prompt additions.
3. `jarvis release sign` (creates `SIGNED_<v>`), `--tag` with dry-run first; the gate reads only issue labels (bug tables and
   checklist rows retired after one release; the tests that keep their format stay until then).
4. Beta promotion note: `status` names the beta that is being promoted and its green run.

## 8. Owner decisions (safe defaults, none blocks)

- OD-S1: accept "accepted by automation" rows in the signature. Default: yes (the owner can add any row back by hand).
- OD-S2: retire the bug tables from the gate in slice 3. Default: yes after 4.0.1 ships with open P0-P2 issues at zero.
