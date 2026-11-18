Improvement ideas with evidence and cost/benefit, drawn from the repo and the Factory 2 trial so far (JAR-67). Status: current (main 50946d4, 16 Nov 2026). Proposals only: nothing here is implemented or authorised.

Sources: the last 60 completed `jarves-checks` runs (14 Nov 20:41 to 16 Nov 01:01 UTC, GitHub Actions API), the jobs of the slowest of them (run 43740480736), `git log origin/main` since 1 Nov 2026, `.github/workflows/jarves-checks.yml`, `core/tools/flake_*.py`, [the JAR-91 drift scan](2026-11-14-quality-drift.md) and the "recurring review findings" counts handed to every Factory 2 developer ticket (7-day window). Tier: N = ordinary change, R = Tier R category (workflows, Gate, release) needing the Opus sign-off. Costs are rough agent-hours, not measured.

## 1. Push less often to a PR while it is still red locally

**Evidence:** 20 of the 60 runs (33%) ended `cancelled`, all superseded PR runs (`cancel-in-progress` for `pull_request`, workflow line 29). Three branches were cancelled 3 times each (`f2/JAR-114-...`, `f2/JAR-400-...`) or twice (`f2/JAR-401-...`). Factory ticket rule 11 asks for small early pushes, so every intermediate commit starts a full matrix that is then thrown away.
**Cost:** about 0.5 h: change the factory ticket wording to "push the first draft commit early, then push again only after `prepush.py` is green", or mark early commits `[skip ci]` until the draft is un-drafted.
**Benefit:** up to a third fewer runner-minutes on PR runs and shorter queues for the runs that count (see 2).
**Tier:** N (ticket template text; no workflow change).

## 2. Find out why a 12-minute matrix takes 36 minutes wall clock

**Evidence:** median run 12.4 min, p90 29.7 min, max 36.0 min (`run_started_at` to `updated_at`). In the slowest run the longest job took only 12.3 min (Linux shard 1/3); the Linux shards took 9.9 to 12.3 min against 6.7 to 10.8 min for the four Windows shards. The gap between job time and wall time is not explained by the job list; runner queueing behind cancelled runs (1) is the likely cause but is UNVERIFIED.
**Cost:** 1 h to read `created_at`/`started_at` per job over a week of runs and decide; a 4th Linux shard is a small workflow edit plus one CI run.
**Benefit:** a measured answer before any speed work; if it is queueing, idea 1 is the fix and no workflow edit is needed.
**Tier:** R if the workflow is edited (`.github/workflows/`); N for the measurement only.

## 3. One shared atomic-write helper

**Evidence:** 52 files under `core/src/jarves` call `os.replace` directly and three modules carry their own private helper (`hotfix.py` and `hotfix_recover.py` `_atomic_copy`, `hotfix_slice2.py` `_atomic_replace_file`). "Shared-file updates are atomic (lock plus unique temp file, os.replace)" was a review finding 11 times in 7 days.
**Cost:** 2 to 3 h for a tested helper (unique temp name, fsync, `os.replace`, optional lock) and a rule pointing new code at it; migrating existing callers is separate work, one PR per area.
**Benefit:** removes a whole class of review round-trips; one place to fix Windows replace retries.
**Tier:** N for the helper and ordinary callers; R for the hotfix/update write paths.

## 4. One shared "log once per outage" helper

**Evidence:** four modules implement their own once-per-outage logging (`mobile.py`, `auto_update.py`, `health.py`, `voice/delegation_client.py`); JAR-346 needed four commits (16fc620, 893b92d, 014a5fb, d52a2ff) to get it right across them. "Logs and warnings are bounded" was a review finding 6 times in 7 days.
**Cost:** about 2 h for a small tested helper (key, outage state, reset on recovery); adopt it in new code first.
**Benefit:** fewer review findings and one tested behaviour instead of four.
**Tier:** N.

## 5. Check before review that the changelog claims only what the diff does

**Evidence:** "The changelog fragment, PR body and docs state only what the diff really does" was a review finding 14 times in 7 days; 8 commit subjects on main since 1 Nov mention rewording or narrowing such claims (for example 8c09918 "changelog no longer overclaims", d52a2ff "changelog states ... exactly").
**Cost:** about 1 h: a short self-check step in the factory developer prompt (each changelog sentence names the test or diff line behind it) before un-drafting. A lint is possible but hard to make reliable.
**Benefit:** fewer Opus review cycles spent on wording; Opus time goes to Tier R risk.
**Tier:** N.

## 6. Prove each new test fails without the change

**Evidence:** "Every changed behaviour ... has a test that fails without the change" was the most frequent review finding (19 in 7 days).
**Cost:** about 2 h for an optional helper that runs the PR's new or changed test files against the merge base (source files reverted, tests kept) and reports which still pass; advisory, not a gate.
**Benefit:** catches tests that pin nothing before a reviewer has to; matches rule 2 of the factory tickets.
**Tier:** N (local tool; adding it to CI later would be R).

## 7. Wire up or retire the flake aggregator

**Evidence:** `core/tools/flake_report.py` says it writes `docs/reviews/FLAKES.md`, but no workflow calls it and that file has never been committed. Flakes are still found: the nightly runs `flake_scan.py --issues` and main since 1 Nov carries 8 commits that quarantine or deflake tests (c1f44ca, 24b75f0, 165df93, 5c94966, 0af013a, 7148db2, 363f561, 6eed859: #992, #994, #1049, #1051, #1052, #722, #422, JAR-402). `core/tests/quarantine.toml` is currently empty, so today's state is good.
**Cost:** 0.5 h to retire the tool and its test, or about 1 h to call it from the nightly.
**Benefit:** no dead tool that suggests a report exists; one flake trail (the nightly issues).
**Tier:** N to retire; R to add it to a workflow.

## 8. Keep unused imports from coming back

**Evidence:** the JAR-91 scan found 92 ruff F401/F811/F841 items (37 in `core/src/jarves`, 55 in `core/tools`); 5 were fixed, the rest left for follow-up, and `core/tools/m2_dashboard_screens.py` has no reference at all.
**Cost:** about 1 h: run `ruff check --select F401` on changed files only inside `prepush.py`, so old items do not block anyone.
**Benefit:** the number stops growing without a big cleanup PR; later cleanups stay small.
**Tier:** N (prepush is a local tool).

## Suggested order

1 and 5 are text-only and cheapest; 2 should be measured before any workflow change; 3 and 4 remove the most frequent review findings; 6 to 8 are small follow-ups. Each needs its own ticket; none is started.
