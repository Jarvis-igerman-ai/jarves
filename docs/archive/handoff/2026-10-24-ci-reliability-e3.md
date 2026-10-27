# CI reliability E3 and the org move to the native merge queue (24 Oct 2026)

**Asked:** V4.5 workstream E3: make CI on the repo reliably green and the merge flow smooth; then (owner choices) flaky
tests are show-stoppers, fix-forward instead of auto-revert, and move the repo into a free org for GitHub's merge queue.

**Analysis:** issue #160 (every failed run 21-24 Oct classified, speed table, plan). Red main runs: 5 flaky tests, 3
merged without own proof (2 direct pushes, 1 cancelled Windows job), 1 interaction of two green PRs (#80 + #107), 1
perf-gate noise. One full PR run = 21 jobs vs the 20-job free concurrency limit (>=18 running 72% of a measured 2.3 h).
Cache at the 10 GB cap (53 CodeQL overlay databases = 4.5 GB).

**Merged:** #157 (shard-order `tools` import), #162 (org plan), #169 (quarantine ledger `core/tests/quarantine.toml`,
`-m "not quarantine"`, the non-blocking step really runs, nightly flake scan -> showstopper issues; first entry #163),
#170 (CodeQL cache prune; 52 old entries deleted by hand), #172 (queue judges the 2 required checks + jarves-checks
jobs only, fail closed), #173 (daily CI health comment on #160), #174 (a red main run re-runs its failed jobs once
before the issue), #176 (both required checks report on `merge_group`).

**Org move (#161):** repo transferred to `Jarvis-igerman-ai/jarves`; secrets and the variable arrived; new
`AUTOMERGE_TOKEN` (org resource owner) set by the owner. Ruleset id 25797359 "main: merge queue + required checks":
the 2 required checks, merge commit only, merge queue (build concurrency 3, up to 4 per group, 2 min wait, 60 min
timeout), no deletion or force push, org admins bypass only via a PR. This note's PR is the first one through the queue.

**Verified:** targeted tests per PR locally; CI green on each PR; main run after #176 reused the proof (1.2 min).

**Not verified yet:** a code PR and two PRs at once through the queue; the first beta publish after the transfer
signing with key id `a2bfe35a27dcd5fd` (same as before); after numbers from the nightly health comment.

**Next (in order):** URL replace PR (`igalgerman/jarves` -> `Jarvis-igerman-ai/jarves`, not `jarvis-releases`);
retire `automerge.yml` and `next-flow.yml`; fix-forward PR (branch `ci/fix-forward-red-main`); then delete the classic
branch protection once the ruleset has carried a few merges.

## Decisions needed

- USER DECISION REQUIRED: S2 in #160: build the installer on PRs only when packaging paths change (the merge group
  still builds it), ~3-4 min and 6 runners less per PR run.
- USER DECISION REQUIRED: keep at most ~2 PRs with full CI at a time (others stay draft) while on the free plan; a paid
  plan with more concurrent runners is a cost line only.
- ANSWERED (24 Oct): red main = fix-forward, auto-revert off; flaky tests quarantined at once with a showstopper issue.
