# Capability matrix: `implemented_unverified` vs `verified_on_device` (JAR-81, T21 spike)

Date 2026-11-09. Base: `origin/main` c8c040ca. Proposal only: no code changed, nothing here authorises work.
Issue: public #3 (Pi parity), line "per-Skill capability matrix with separate `implemented_unverified` vs `verified_on_device`".
Method: read `features.py` and `capabilities.py`, imported every `jarves` module of a `main` checkout (no import failures), then called `features.stage_of()` and `capabilities.get()` for each row.
No device, phone, car or Pi was exercised. Every device outcome below is UNVERIFIED.

## Problem

The data already exists and is shown row by row on the dashboard (`GET /api/features`). What is missing is one complete, reproducible table, and the current fields do not always say what they appear to say.

**`docs/NOW.md` names no product scope** (same finding as JAR-66), so the owner must name this as scope before implementation.

## Current state (`main`, 119 catalog rows)

| Target | `verified_on_device` | `implemented_unverified` | `planned` | `not_applicable` |
|---|---|---|---|---|
| Windows | 7 | 110 | 2 | 0 |
| Raspberry Pi (`linux_arm64_pi`) | **0** | 88 | 20 | 11 |
| Phone (10 declared rows) | **0** | 6 | 4 | 0 |
| Car (7 declared rows) | **0** | 6 | 0 | 1 |

Stage: 108 `beta`, 11 `stable`. Verified Windows rows: music, wake_word, greeting, chimes, background_core, display_control, tray_icon. JAR-66 reported 113 rows on an earlier base; these counts supersede it for this revision.

## What already exists

- **Model:** `capabilities.py` (`Target`: status, evidence, blocker, roadmap_issue; `problems()` rule check; `view()`). `features.py` `stage_of()` derives the stage from the Windows status.
- **Surfaces:** `GET /api/features`, the dashboard Features page, `jarvis features`, guide platform notes (`guides.platforms()`).
- **Tests:** `test_capabilities.py`, `test_v5_capability_evidence.py`, `test_v6_capability_rows.py`, `test_beta_gate.py`. `problems()` currently reports 0 violations.

## Findings that shape the design

1. **Four `stable` rows have no device verification.** `dashboard`, `text_chat`, `update_check` and `beta_channel` set `stage="stable"` explicitly while their Windows status is `implemented_unverified`. The `Feature.stage` comment defines stable as "verified on the owner's device". A row's stage alone therefore cannot be read as proof.
2. **`last_checked` is a constant.** All 119 rows show `2026-10-21`, the module default, including rows added later (for example the V6 rows of 25 Oct). The dashboard exposes it as a per-row date.
3. **Evidence has no date, device or revision field.** It is free text. Four verified rows cite "HANDOFF", and the sections cited for music and wake_word are now in `docs/archive/HANDOFF_2026-10-and-earlier.md`, not `docs/HANDOFF.md`.
4. **The Pi guard has a hole.** `test_no_pi_feature_claims_device_verification` walks only `MATRIX`. Eight rows (for example `background_core`, `autostart`, `backup`) declare targets on the feature itself, and `get()` prefers those. No row claims Pi verification today, but this path is unguarded.
5. **`pi_ready` counts `not_applicable` as ready.** Eleven rows, mostly phone features plus `tray_icon`, `listening_dot` and `hotfix`. The status is documented as "only for device-specific UI mechanics that have an equivalent control". Each row needs individual review before a Pi percentage is ever quoted.
6. **The catalog fills through import side effects.** Importing only `features` gives 27 rows; importing all modules gives 119. A generator that misses the walk silently under-reports.
7. **Two rows are outside the catalog.** `audio_route` and `beta_features` are in `MATRIX` only, so a catalog-driven view omits them.
8. **Windows `implemented_unverified` is uneven.** 48 of 110 have no `blocker`. That is allowed by `problems()`, but the table cannot say what is missing for those rows.
9. **Promotion changes behaviour.** Setting Windows to `verified_on_device` makes a derived row `stable`, which removes the beta opt-in. Editing a status is not just a documentation change.
10. **Skill coverage:** 43 features list owned Skills. Skills owned by no feature were not counted in this spike; the generator should list them.

## Options

| Option | What | Size | Verdict |
|---|---|---|---|
| A. Committed `docs/CAPABILITY_MATRIX.md` with a drift test | Generated file in the repo | S | Reject. Every feature PR must regenerate one shared file, which conflicts across parallel PRs and breaks the "one file per change" rule |
| B. Read-only generator run on demand | `core/tools/capability_matrix.py`, Markdown or JSON to stdout, nothing committed | S | **Recommended** |
| C. New owner command `jarvis features matrix` | Same output inside the product | M | Later, only if the owner wants it. Needs a guide, parity entry and Hebrew strings, and the dashboard already shows these rows |

## Proposed table (option B)

One row per feature, plus `MATRIX`-only rows marked `no switch`. Columns:

| Column | Source | Honesty rule |
|---|---|---|
| Feature, Skills | catalog | Unowned Skills are listed in a separate section |
| Stage + basis | `stage_of()`, `Feature.stage` | Print `stable (declared, not device-verified)` for finding 1; never a bare `stable` |
| Windows | `Target.status` | `VERIFIED` only for `verified_on_device`; otherwise `UNVERIFIED (built)`, `planned`, `blocked` |
| Pi | `Target.status` | Always `UNVERIFIED`, `planned`, `blocked` or `n/a`. Any `verified_on_device` makes the tool **exit non-zero** until an owner-approved Pi run changes this rule |
| Phone, Car | `EXTRA` | Blank means "no path declared", never "fine"; same exit rule as Pi until a real run exists |
| Evidence / blocker | `Target` | Printed as written; `no blocker recorded` when empty (finding 8). A test file is not device evidence |

`last_checked` is left out until finding 2 is fixed. The summary has counts per target and no overall "percent done": `not_applicable` is never counted as ready.

Illustration from current data:

| Feature | Stage + basis | Windows | Pi |
|---|---|---|---|
| music | stable (derived) | VERIFIED | UNVERIFIED (built) |
| dashboard | stable (declared, not device-verified) | UNVERIFIED (built) | UNVERIFIED (built) |

## Suggested work (not started)

1. **PR 1, size S, Tier N:** generator, hermetic test (full module walk, fake rows for each honesty rule, non-zero exit on a false Pi claim), extend the Pi guard to `get()` rows (finding 4). No `.github/` change.
2. **PR 2, size S, Tier N by the tool:** optional per-target `checked_on` and `revision` fields, and retire the constant `last_checked`. Depends on JAR-66 gap 1 if results are to be recorded by the owner.
3. **Owner decision, no code until answered:** for finding 1, recommended **keep the four rows `stable` and label the basis**. Moving them to `beta` would put the dashboard, text chat and update check behind the opt-in.

**Not duplicated from JAR-66:** gap 1 (owner path for recording results) and gap 4 (stale phone rows) stay there. This proposal only defines the read-only view and the honesty rules.

## Risks

- **Privacy:** evidence strings are free text. Output stays on stdout, and a sweep for personal data is needed before any copy is published. No outside service is involved.
- **Security:** read-only, no network. The module walk imports all of `jarves`, so the test needs a temporary home and no real credentials. Walk side effects beyond the catalog were not audited.
- **Cost:** none at runtime.
- **False confidence:** the largest risk. A tidy table can read as a status report; the header must say it reports declared status and proves nothing.
- **Gating:** see finding 9. Row promotion remains a reviewed PR with real evidence and is outside PR 1.

## Tier

- **This doc PR:** Tier N (docs only).
- **PR 1 and PR 2:** `risk_class.py` on `capabilities.py`, `features.py`, `core/tools/capability_matrix.py` and a docs path reports no high-risk path; recheck on the final file list.
- **Any change to stage or beta-gate logic, or a promotion to `verified_on_device`:** not proposed here. Treat as owner-reviewed.

## Real-device verification

The generator needs none, because it only reports. Every Pi cell stays UNVERIFIED until a real Raspberry Pi 5 run with recorded evidence; none exists and no hardware purchase is implied. Phone and car rows stay UNVERIFIED until the owner runs them.

## Acceptance for PR 1

- Row count matches a full module walk and includes `MATRIX`-only rows.
- The four declared-`stable` rows print the "not device-verified" basis.
- A fake Pi or phone `verified_on_device` row makes the tool exit non-zero, for rows declared either way (`MATRIX` or `feature.targets`).
- Nothing is written to disk, and no status or stage changes.
