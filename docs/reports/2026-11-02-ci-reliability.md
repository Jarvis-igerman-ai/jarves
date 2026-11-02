Report: CI reliability E3 (#160) status check, V4.5.1/V5, status current (2 Nov 2026). Docs only; no workflow changed.

# CI reliability E3: plan versus what is on main

Source: issue #160 plan table and the daily `ci_health.py` comments (latest 1 Nov 2026), checked against `main` at ca7a23fc.

| Plan item | State on main | Evidence |
|---|---|---|
| E3-0 queue robustness | delivered | #142 |
| E3-1 shard-order `tools` import | delivered | #157, #158 |
| E3-b flake quarantine | delivered; blocking runs use `-m "not quarantine"`, quarantine step runs | `core/tests/quarantine.toml`, `core/tools/flake_scan.py`, `jarves-checks.yml` |
| E3-a red-main fix-forward | delivered; rerun failed jobs once, then ONE "main is red" issue naming tests and suspect PRs; no auto-revert | `.github/workflows/main-health.yml` |
| S1 merge queue | delivered; `merge_group` trigger, proof reuse by tree | `jarves-checks.yml` (`ci_reuse.py merge-group`) |
| S2 installer only for packaging PRs, always in merge group | delivered | #255, #251 |
| S3 Windows 4 shards | delivered | #266 |
| E3-c delete next-flow | delivered (no such workflow file) | `.github/workflows/` listing |
| S5 daily numbers | delivered | `ci_health.py`, daily issue comment |

## Numbers now (last 7 days to 1 Nov 2026)

| Number | 24 Oct (#160) | 1 Nov | Target |
|---|---|---|---|
| red-main rate | 26% (incl. 20 Oct bootstrap) | 0% (0/206) | 0 |
| PR run to green, median min | 15.6 (12.7 in the 24 Oct addendum) | 13.8 | under 8 |
| cancelled runner-minutes | 830 (4 days) | 1014 (7 days) | falling |
| quarantined tests | 1 | 7 in the health comment, 8 ids in the ledger today | each has an open issue, fixed in 2 days |

## Still open

1. PR speed is the one unmet target: 13.8 min against under 8. Code merge groups still run the full matrix plus installer (about 17 min, 25 Oct measurement). No change proposed without fresh timing data.
2. The quarantine is growing. Policy says fix within 2 days, but #548, #549, #550, #551 and #556 date from 26 Oct. Suggested small follow-up: `ci_health.py` prints the age of the oldest quarantined entry so the daily comment shows a broken 2-day rule.
3. "green to merged" prints "-" in the daily comment: the metric is not computed under the merge queue. Candidate small fix to `ci_health.py`, separate PR.
4. Open flaky issues (#660, #709, #722, #788) need a root-cause owner; outside this report.

## Verdict

Every item of the #160 plan is delivered and merged. Recommend keeping #160 open as the tracker for item 1 until a measured speed change lands, or the owner accepts about 14 min. CI behaviour here is read from run history and the workflow files; it was not re-run and is not a tested user scenario.
