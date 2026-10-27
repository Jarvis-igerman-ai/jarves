# V4.5 workstream C: recurring security scans (branch feat/security-scans, no PR yet) - 24 Oct 2026

Owner decision 24 Oct: daily LIGHT scan + weekly FULL scan (Sunday), `auto_fix` OFF and not settable, a scan may at most
create or update ONE tracker issue; never merge, dismiss, deploy or send.

## What exists
- `core/src/jarves/security_scans.py`: settings (`security_scans.json`: enabled default OFF, tz Asia/Jerusalem, light days/time,
  full days/time, quiet hours 22:00-07:00, max 2 automatic runs/day, report_github default off, dry_run), state and history
  (`security_scans_state.json`, last 30), DST-aware slot calculation (gap -> first valid minute, repeated hour -> first
  occurrence), one catch-up run (never a burst), retry back-off 30 min / 2 h / 6 h, collectors, dedupe by
  fingerprint = source|rule|location|app version, ONE tracker issue (marker `jarves-security-scans:v1`, signature in the body,
  closed tracker respected, duplicates or uncertain lookup fail closed), `jarvis scans status|run|on|off|set|history`.
- LIGHT: code-scanning, Dependabot, secret-scanning alert inventory (secret TYPE + number only), last `jarves-checks` on main,
  last `jarves-nightly` (new failing tests), recurring local diagnostics from the issues registry (offline).
  FULL adds: last CodeQL run (failed / never / stale > 10 days; CodeQL itself keeps its own weekly workflow) and a boundary
  tests summary (core/tools/security_scans.py runs approvals / privacy / redaction / netguard tests; not run = coverage incomplete).
- No model calls at all (`llm_calls` is always 0), no paid API. A source the token cannot read is "incomplete", never zero;
  an alert counts as resolved only when its source was read completely in that scan.
- Hosted: `.github/workflows/security-scans.yml` (cron 03:20 UTC Mon-Sat light, Sun full; workflow_dispatch; no push / PR trigger;
  permissions contents read, issues write, security-events read, actions read) calls `core/tools/security_scans.py` (stateless).
  PC-local: `jarvis scans run --if-due` from Task Scheduler (or any timer). The Core scheduler was NOT used (its jobs call voice
  tools only; a job would need a new tool for no gain). Difference is documented in the guide and the module docstring.
- Guide `guides/data/security_scans.toml` (EN+HE, steps, 5 problems, `[parity.features]`), CLI parity via the guide (`security_scans = "scans"`;
  `Guide` got a `parity` field, the loader already read it from the TOML), feature `security_scans` (default OFF, targets
  implemented_unverified), Hebrew via `hebrew_parts/2026-10-24-security-scans.json` (server texts), no dashboard catalog text needed.
- Tests `core/tests/test_security_scans.py` (27): one synthetic finding -> one non-sensitive issue, dedupe, unchanged writes nothing,
  closed / duplicate trackers, privacy drops, coverage / resolved, offline, retry and back-off, 5xx retry, DST (Israel + gap + fall back),
  3 days a week and actual next run, catch-up, quiet hours, budget, auto_fix refusal, CLI, workflow shape, hosted tool.

## NOT done / not verified
- Dashboard Updates/Health card and `/api/...` route: not built (not cheap: needs a page card + catalog texts); status is in `jarvis scans status`.
- Never run against the real repository: real alert payload shapes follow the GitHub REST docs only; the hosted workflow has never run.
- `GITHUB_TOKEN` cannot read Dependabot or secret-scanning alerts, so hosted runs report those as incomplete until the owner supplies a token
  with `security_events` (repo secret + one line in the workflow) or runs `jarvis scans run` locally with their own login.
- The boundary step needs the test dependencies; the workflow installs `uv sync --frozen` without extras, so it may report "incomplete" there.
- Time of the hosted cron is UTC (03:20 = 06:20 / 05:20 Israel); the local run honours the owner's timezone.

## Questions for the owner
1. Should scheduled hosted runs write the tracker issue (current default: yes for schedule, off for manual dispatch unless ticked)?
2. Provide a read token for Dependabot / secret-scanning alerts (repo secret), or accept "incomplete" there?
3. Merge-order: this adds a workflow file (high-risk CI change): merge alone, never with a large merge.
