# Release and test system (alpha, beta, stable; scenario runner; sandbox)

Status: **plan for owner review** (22 Oct 2026). Docs only. Builds on PR #44 (CI speed-up, continuous release) and
PR #57 (Linux shards, fast validation), which another CI agent is still improving. Everything here is an **increment on
top of #57**, not a new design. Numbers marked *measured* come from real GitHub runs (see `docs/CI_PERFORMANCE.md`);
numbers marked *est.* are estimates and must be replaced by measurements.

## 0. Why: what went wrong on the 4.0.0 night

| What happened | Fix in this plan | Section |
| --- | --- | --- |
| (a) the full 25-min suite ran on every PR and again after merge | light PR CI (affected tests), full CI once per batch, main reuses the proof (#44) | 5 |
| (b) main CI had cancel-in-progress, each merge cancelled the release run | main runs queue (fixed in #44 head, commit 9b13c6a); batch runs never cancel | 5 |
| (c) a test called the real GitHub API and failed on rate limit | network guard in every test + offline stub (`ci/deterministic-tests`) | 5.4 |
| (d) timing tests flake on shared runners | event waits (`until`), one retry of failed files, quarantine, perf only in the perf gate | 5.4 |
| (e) 15-20 open PRs re-ran CI every time main moved | PRs go to `next`; no "must be up to date"; drafts run nothing | 5.1 |
| (f) one channel: every merge published to Latest | three channels: alpha (auto), beta (promote), stable (promote + sign-off) | 4 |
| (g) manual testing = 31 rows by hand | scenario runner in a sandbox does about 20 rows; owner keeps 9 rows, < 15 min | 2, 3, 6 |
| (h) the installer takes about 4 min (16.5k files, Defender, about 600 MB) | fewer files, no bytecode at install, optional Defender exclusion, delta updates | 8 |

## 1. Test pyramid: what runs where and when

```
                        owner, real PC (9 rows, < 15 min)           stable only
                  ----------------------------------------------
                 Windows VM pack: real installer + installed app     beta gate (6 min)
                ------------------------------------------------
               scenario runner, Linux container, real Core,         PR (affected) + batch (all)
               fake model + fake integrations (CLI + HTTP API)       2-4 min
             ----------------------------------------------------
            browser (Playwright, all pages EN/HE), e2e Core-Godot    batch (5 min)
          --------------------------------------------------------
         pytest unit + component (about 160 files), offline,         PR (affected) + batch (all)
         network guard, xdist, 12 shards                             3-4 min per shard
       ------------------------------------------------------------
      validate: workflow contracts, shard coverage, doc links        every push, 1 min
```

| Layer | Tool / job | PR (light) | Batch on `next` (full) | Nightly | Beta gate | Stable gate | Time |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Validate | job `validate` (#57) | yes | yes | yes | - | - | about 1 min |
| Unit + component, Linux | job `core` Linux shards | affected files | all, 8 shards | 3x (flake detector) | - | - | 3-4 min per shard (est.) |
| Windows-only tests | job `core` Windows shards | only if a Windows file is affected | 4 shards | 1x | - | - | 3-4 min (est.) |
| Browser, all pages | job `browser`, `all_pages_check.py` | if dashboard files changed | yes | yes | - | - | 5 min |
| Installer build + install smoke | job `installer` (#44) | if packaging, godot, lock or core/src changed and the PR is labeled `installer` | yes | - | - | - | 7m46s *measured* (run 39733332477) |
| Upgrade matrix | split out of `installer` into `installer-upgrade` | - | yes | - | - | - | 3 min (est.) |
| Scenario smoke | new job `scenarios` (Linux container) | scenarios of affected features + 10 smoke | all scenarios | all | - | - | 2-4 min (est.) |
| Windows installed-app pack | new job `scenarios-windows` on the alpha artifact | - | - | yes | yes | - | 6 min (est.) |
| Perf gate | `perf_gate.py` (nightly, #44) | - | - | yes (3 runs) | last 3 nights green | yes | 5 min |
| Failure injection | `failure_injection.py --all` | - | - | yes | yes | - | 4 min |
| Owner hands-on | sign-off sheet | - | - | - | - | yes | < 15 min |

Rule: **the PR shows what the change breaks; the batch proves the product; the beta gate proves the installed product;
the owner checks only what a machine cannot.**

## 2. Scenario runner

Goal: replace most of the manual sheet with scripted runs against the **real Core process** on a throw-away home, with
the **fake model** and fake integrations, through the text surfaces: `jarvis chat`, `ask`, `execute`, `features`,
`doctor`, `timers`, `lists`, `recall`, `update`, `release`, and the dashboard HTTP API (`/api/...` on 127.0.0.1).

### 2.1 Files

| File | What |
| --- | --- |
| `core/scenarios/<feature_id>/<name>.toml` | one scenario (TOML, like the guides) |
| `core/tools/scenario_run.py` | runner: `run`, `list`, `seed`, `coverage` |
| `core/src/jarves/testing/fake_model.py` | fake model server, moved from `core/tests/openai_fakes.py` (`ResponsesHTTPServer`, `FakeLiveServer`) so the runner and the tests share one copy |
| `core/tests/test_scenarios.py` | runs the runner's own unit tests and checks every scenario parses |
| `core/tests/test_scenario_coverage.py` | definition of done: every feature with `since >= 4.1.0` has a guide **and** a scenario |
| `artifacts/scenarios/<version>/` (CI artifact `scenario-evidence-<tree>`) | transcripts (`*.jsonl`) and `SUMMARY.md` |

### 2.2 Format

```toml
# core/scenarios/timers/chat_start_cancel.toml
id       = "timers.chat.start_cancel"
feature  = "timers"                     # id from the features registry (core/src/jarves/features.py)
since    = "3.0.0"
tags     = ["smoke", "chat", "hebrew"]
profile  = "test"                       # offline TEST profile (profiles._write_test_defaults)
fakes    = ["model"]                    # model | live | camera | whatsapp | telegram | releases | pear | clock
timeout_s = 60

# What the fake model answers. Matched in order on the last user message (regex).
# No match = the scenario FAILS with "no script for: <text>" (never a silent default answer).
[[model]]
match = "(?i)ten minute timer|טיימר"
calls = [{ name = "timer_start", args = { minutes = 10, label = "pasta" } }]
then  = "Timer set: ten minutes for the pasta."

[[model]]
match = "(?i)cancel|תבטל"
calls = [{ name = "timer_cancel", args = { label = "pasta" } }]
then  = "The pasta timer is cancelled."

[[steps]]
run    = "jarvis features on timers"
expect = { exit = 0 }

[[steps]]
chat   = "A ten minute timer for the pasta"          # sent to `jarvis chat --silent` (one session per scenario)
expect = { out_has = ["ten minutes", "pasta"], tool = "timer_start" }

[[steps]]
api    = "GET /api/timers"
expect = { status = 200, json_has = { "0.label" = "pasta" } }

[[steps]]
run    = "jarvis timers list"
expect = { out_has = ["pasta"] }

[[steps]]
chat   = "תבטל את הטיימר"
expect = { out_has = ["cancelled"], tool = "timer_cancel" }

[[steps]]
api    = "GET /api/timers"
expect = { json_len = 0 }

[check]                                   # checked after the last step, on every scenario
no_traceback   = true                     # jarves.log has no Traceback
no_secret      = true                     # redaction audit over log + transcript
model_requests = { max = 2 }              # proves no hidden extra calls
```

Step kinds: `run` (any `jarvis ...` command), `chat` (a line into one `jarvis chat --silent` session), `ask`,
`execute`, `api` (HTTP on the dashboard port), `wait` (`until` a condition, never a fixed sleep), `fake` (tell a fake
to do something: `{ whatsapp = "incoming", from = "Dana", text = "hi" }`, `{ camera = "frame", file = "x.png" }`,
`{ releases = "publish", version = "4.1.0.200" }`).
Expect keys: `exit`, `out_has`, `out_not`, `tool`, `no_tool`, `status`, `json_has`, `json_len`, `approval` (a request
appears; `approve = true/false` answers it through the signed approvals API, not through a window), `log_has`.

Example for a V5 feature (goal plans, PR #55), to show the same format covers approvals:

```toml
id = "goal_plans.chat.propose_then_deny"
feature = "goal_plans"                           # guide core/src/jarves/guides/data/goal_plans.toml (PR #55)
since = "4.1.0"
tags = ["release:4.1.0", "approvals"]
[[model]]
match = "(?i)pasta plan"
calls = [{ name = "goal_plan_propose", args = { title = "pasta", steps = [
  { tool = "timer_start", args = { minutes = 10, label = "pasta" }, why = "cook" } ] } }]
then = "I drafted a one-step plan. Shall I run it?"
[[model]]
match = "(?i)^yes$"
calls = [{ name = "goal_plan_run", args = { id = 1 } }]
then = "Not started: you did not approve the plan."
[[steps]]
chat = "Make a pasta plan"
expect = { tool = "goal_plan_propose", out_has = ["one-step plan"] }
[[steps]]
chat = "yes"
expect = { approval = { approve = false }, out_has = ["Not started"] }   # deny in the approvals API
[[steps]]
run = "jarvis goals plan list"
expect = { out_has = ["pasta"], out_not = ["done"] }
[[steps]]
api = "GET /api/timers"
expect = { json_len = 0 }                        # denied plan started no timer
```

### 2.3 Fake model provider (deterministic)

* The Core already uses the `openai` SDK, which reads `OPENAI_BASE_URL`. The runner starts the fake server on
  127.0.0.1 and starts the real Core with `OPENAI_BASE_URL=http://127.0.0.1:<port>/v1`, `OPENAI_API_KEY=test-not-real`,
  `ai.provider = "openai"` in the throw-away home. No code change is needed in the brain for text.
* Voice (GPT-Live) uses `FakeLiveServer`; ChatGPT-plan text uses the `chatgpt_brain` transport hook. Both already exist
  in tests.
* Safety: the Core prints `FAKE MODEL (loopback)` in `jarvis status` and the dashboard header when the base URL is
  loopback, and the network guard (`core/tools/netguard.py`) blocks every non-loopback address in the runner, so a
  scenario can never reach a paid API, even with a real key in the environment. The runner also removes
  `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` and the release secrets from the child environment.
* The fake records every request body. Scenarios can assert on what the model was given (tools offered, instructions
  contain the user's language, no secret inside).
* Later, optional: **replay** of a real answer recorded once in the capped live campaign (section 9) as a scenario
  script, so "realistic" answers stay free.

### 2.4 Seeding and definition of done

`python core/tools/scenario_run.py seed --version 4.1.0` writes draft scenarios (marked `draft = true`, which fail
`coverage` until a human or agent finishes them) from:

1. the CHANGELOG entries of the version (one draft per "Added"/"Changed" line, with the feature id when it names one);
2. the guide of each feature (`core/src/jarves/guides/data/<feature>.toml`): each `say = [[he, en], ...]` pair becomes a
   `chat` step; `expect_en` becomes the step comment; `action = "feature_on:x"` becomes `run = "jarvis features on x"`;
   `check = "enabled:x"` becomes an `api = "GET /api/settings"` check; `action = "open:#/page"` becomes a browser
   check handled by `all_pages_check.py`;
3. the features registry (`since`, `default`, prerequisites): one "turn on / turn off / doctor says why" scenario per
   feature.

**Definition of done for a new feature (add to CLAUDE.md):** guide entry + CLI parity entry + features row + Hebrew
strings (today) **+ at least one scenario tagged with the feature id and the version (`release:X.Y.Z`)**, in English and
Hebrew where the feature talks. `test_scenario_coverage` enforces it.

### 2.5 Transcripts as release evidence

Each run writes, per scenario, a JSONL transcript (step, command, output, model requests, tool calls, approvals,
duration, pass/fail) and a `SUMMARY.md` table. CI uploads them as `scenario-evidence-<tree>`; the package workflow copies
the summary into the alpha release notes. The sign-off sheet links to them (section 4.3).

## 3. Sandbox: the same clean environment every time

### 3.1 Options (honest)

| Option | Start | Works on owner PC (Windows 11 Home) | What it can test | Verdict |
| --- | --- | --- | --- | --- |
| **Linux container** (Docker, `python:3.12-slim` + uv) | 2-5 s; image about 450 MB, cached | yes, Docker Desktop on WSL2 | Core, CLI, HTTP API, fakes, Playwright | **use for about 85% of scenarios** |
| Windows container (servercore ltsc2025) | image 3-5 GB, pull 3-5 min | **no** (Home has no container feature) | Core on Windows, no GUI, no installer UI | no: slow, not runnable at home |
| Windows Sandbox | 20-40 s | **no** (needs Pro/Enterprise) | full desktop | no |
| **GitHub `windows-2025` runner** (already a fresh VM each job) | 20-60 s | n/a (CI) | real installer, `jarvis.cmd`, Job Objects, Tk popup, Edge | **use for the Windows pack** |
| Local Hyper-V/VirtualBox VM snapshot | 1-2 min restore | VirtualBox yes, Hyper-V no | everything except real hardware | optional, owner-side only |

Decision proposal: **Linux container for the scenario runner, the GitHub Windows runner as the ephemeral Windows
machine.** The Core and the whole pytest suite already run on Linux, so most scenarios need no Windows.

### 3.2 What is faked

| Real thing | Fake | Exists? |
| --- | --- | --- |
| OpenAI text / GPT-Live | `fake_model.py` (`ResponsesHTTPServer`, `FakeLiveServer`) | yes (tests), move to `jarves.testing` |
| Godot face | fake face client on the Core link (records `face.emotion`, `assistant.activity`; from `silent_live.py`) | yes, extract |
| Microphone, speaker | voice off; for voice scenarios the virtual mic/speaker of `suite_voice.py` with the fake live server | yes (Windows); Linux: text only |
| Camera | `camera/backends.py` `FakeCamera` | yes |
| Phone Link / `tel:` | `webbrowser.open` recorder (from `suite_v3_voice.py`) | yes |
| WhatsApp bridge | `core/tools/fake_whatsapp_bridge.py` | yes |
| Telegram, Home Assistant, weather, Instagram, Pear | `core/tests/channels_fakes.py` servers | yes |
| GitHub releases / update server | `core/tests/release_fakes.py` `FakeReleaseServer` + `JARVES_UPDATE_BASE_URL` | yes |
| Clock | a `jarves.clock` seam (now/monotonic offset) for timers, scheduler, briefing | **no**: build it; until then use second-long timers |
| Approval popup | answered through the signed approvals API; the popup look is a human row | yes |

### 3.3 Image build and caching

* `packaging/sandbox/Dockerfile`: `python:3.12-slim`, `uv sync --locked --extra test`, Playwright Chromium, a non-root
  user, `HOME=/home/qa`, no secrets. Entry: `uv run python core/tools/scenario_run.py run "$@"`.
* Job `sandbox-image` builds and pushes `ghcr.io/Jarvis-igerman-ai/jarves-sandbox:<hash>` only when the hash of
  `Dockerfile + core/uv.lock` changes (about 3 min, rare); other jobs pull it (about 20 s) or run the same steps without
  Docker on the runner (the runner is already clean; Docker adds sameness with the owner PC).
* Local: `docker run --rm --network none -v %cd%:/src:ro ghcr.io/Jarvis-igerman-ai/jarves-sandbox:<hash> --tag smoke`.
  `--network none` is the strongest network guard: the container has only loopback.

### 3.4 AI QA agent (optional, outside CI)

The deterministic runner is the gate. The AI agent only **adds** exploratory checks and explains failures. It never
runs in GitHub Actions (no paid key in the pipeline); it runs on the owner PC in the same container, through Claude Code
on the owner's subscription, with read-only source mount, no secrets, `--network none` except the model API (host
allow-list in the container proxy).

| Tier | When | Budget cap (per release) |
| --- | --- | --- |
| 0 shell watcher (`scenario_run.py`, no AI) | always | 0 |
| 1 Haiku | a scenario failed: read transcript + log, say "bug / flake / scenario wrong", draft the issue | 20 runs |
| 2 Sonnet | beta candidate: explore each NEW feature for 15 min of text chat, write new scenarios as drafts | 1 session per new feature, max 8 |
| 3 Opus | only on owner request, for a failure Sonnet could not explain | 2 sessions |

Caps live in `core/scenarios/qa_budget.toml` (`max_sessions`, `max_turns`, `tier`); the wrapper
`core/tools/qa_agent.py` refuses to start past the cap and writes what it used into the evidence folder. Agent-written
scenarios enter the gate only after review (they start as `draft = true`).

## 4. Release qualification: alpha, beta, stable

One build, three channels. **No rebuild**: promotion re-signs a channel manifest that points at the same `.exe`
(same SHA256, checked by `release.py verify`).

| Channel | Who | Trigger | Gate | Manifest |
| --- | --- | --- | --- | --- |
| **alpha** | owner's test PCs, opt-in | automatic, every green batch fast-forwarded to `main` | full batch CI green (tests, installer smoke, scenarios) | `latest-alpha.json` on rolling pre-release `alpha-channel` |
| **beta** | early users, opt-in | `gh workflow run promote.yml -f build=4.1.0.212 -f to=beta` (or automatic once a day, owner choice) | alpha at least 12 h old with no new P0-P2 issue; Windows installed-app pack green on that exact artifact; nightly perf + failure injection green; release scenario pack (`--tag release:4.1.0`) 100% | `latest-beta.json` (exists, #44) |
| **stable** | everyone | tag `v4.1.0` pointing at the beta commit | `release_gate.py` (#44): no open P0-P2, latest nightly green, signed sheet (owner 26 Oct: no five-nights rule), tested commit = tag commit | `latest.json` |

What exists: candidate + beta publishing with the switch `ENABLE_BETA_PUBLISH` (#44, `package.yml` jobs `candidate`,
`beta`); stable gate (`release.yml` job `stable`, `release_gate.py`); rollback (`release_rollback.py`); status
(`release_status.py`); `jarvis update --channel`. What is new: rename today's "beta on every green main" to **alpha**
(one string in `package.yml` + `updates.py` channel list + guide `beta_channel.toml` -> add `alpha`), and add
`promote.yml` + `release.py promote` (download the alpha's signed manifest, verify, re-stamp `channel`, sign, upload).

### 4.1 Parallel work on the next version, no freeze

* `next` always carries the **next** version. When 4.1 goes to beta, the next batch bumps `next` to 4.2.0
  (`core/pyproject.toml`, one commit by the promote workflow). New features keep flowing into alpha 4.2.
* Fixes for the 4.1 beta: normally **fix forward** (the fix ships in 4.1 beta N+1 only if the bump has not happened
  yet). After the bump: `hotfix/4.1-*` PRs go to branch `release/4.1` (cut from the beta commit), get full CI, produce
  `4.1.0.N` builds on the beta channel; the same fix is merged into `next` too (bot cherry-pick).

### 4.2 Owner sign-off sheet, pre-filled

`python core/tools/manual_test_sheet.py --version 4.1.0 --evidence scenario-evidence-<tree>.zip` (generator from PR
#46, plus a new `--evidence` option) writes `docs/reviews/MANUAL_TEST_4.1.0.md` with two parts:

1. **Automated rows** (already filled): row, scenario ids, result, link to the transcript, run id. Example:
   `| A07 | Dashboard pages EN/HE load, no untranslated text | all_pages_check 62/62 | auto-pass | run 4021 |`.
   The owner reads, does not repeat.
2. **Human rows** (section 6), each with "why a human" and the minutes it takes.

`jarvis release signoff --version 4.1.0 --row H3 --result pass` and `--finish` work as today (#44); `--finish`
also checks that every automated row is `auto-pass` for the **same build** the owner tested.

## 5. CI and PR flow (increments on #57)

```
 feature PRs (any number, in parallel)
   |  light PR CI <= 8 min: validate + affected Linux tests + affected scenarios (+ Windows if affected)
   v  automerge (green + approved label)            NOT required: "branch up to date"
 +------------------------------ next ------------------------------+
 |  push -> batch run "jarves-checks" (concurrency group batch,      |
 |  cancel-in-progress: false; a newer push only replaces the        |
 |  PENDING run, so pushes batch up by themselves)                   |
 |                                                                   |
 |  minute 0 ---------------------------------------------- 8       |
 |  installer build + install smoke  ==============>                 |
 |  installer-upgrade (matrix)          =======>                     |
 |  core linux 1..8                  ====>                           |
 |  core windows 1..4                ====>                           |
 |  browser linux, browser windows   =====>                          |
 |  scenarios (container) 1..2       ===>                            |
 |  validate =>                          ci-ok (fan-in) ------------>|
 +-------------------------------------------------------------------+
   | green: fast-forward main to the batch commit (same tree)
   v
 main push: "changes" finds the proof (30 s, #44) -> package.yml -> ALPHA (2-3 min)
   |
   | promote.yml (manual or daily) + beta gate (Windows installed-app pack, 6 min)
   v
 BETA  --- owner sign-off (< 15 min) + tag vX.Y.Z --- release.yml/release_gate.py --> STABLE
 red batch: batch_bisect.py -> revert the guilty PR on next -> comment on the PR
 hotfix/*: PR to main (or release/X.Y), full CI, alpha/beta from it, auto-merge back into next
```

### 5.1 Integration branch `next`

* All feature PRs target `next`. `main` moves **only** by fast-forward to a green batch commit (or a hotfix). So every
  `main` commit is green and is an alpha.
* Because PRs do not have to be up to date with `next`, a moving `next` does not re-run 15-20 PRs (fix for (e)).
  Conflicts are still caught by GitHub (mergeable state).
* The fast-forward is done by `package.yml` (it already runs on `workflow_run` of a green `jarves-checks`): new first step
  `git push origin <batch sha>:main` (fast-forward only, fails safe if `main` moved). A push made with `GITHUB_TOKEN`
  starts no new workflow; that is fine, the tree is already proven and `package.yml` continues to the alpha itself.
  Branch protection on `main` must allow this one bot push (open decision 2).
* Batch size: whatever merged while the previous batch ran (typically 1-6 PRs). Max batch time about 8 min, so the
  wait from PR merge to alpha is 8-19 min in the worst case, about 11 min typical (est.).

### 5.2 Light PR CI (<= 8 min) and affected tests

New command in `core/tools/ci_plan.py` (the classify step already exists):
`ci_plan.py affected --base origin/next --plan plan.json` returns the test files to run:

1. Build a static import graph once (AST of `core/src/jarves/**` and `core/tests/*.py`; about 1 s).
2. A test file is affected when it, `conftest.py`, a fake it imports, or any `jarves.*` module in its import closure
   changed. Changes to `uv.lock`, workflow files, CI tools or `conftest.py` = all files (as today).
3. Data files map by rule: `guides/data/*.toml` -> `test_guides*`, `dashboard_static/**` -> browser + dashboard tests,
   `hebrew.py` / locales -> catalog tests, `packaging/**` -> installer job.
4. Scenarios: those whose `feature` id belongs to a changed module (map in `core/scenarios/owners.toml`) + the 10 `smoke`.

Deselection, never skipping, so the zero-skips rule holds. The batch always runs everything, so a wrong graph can only
delay a failure by one batch, never ship it.

### 5.3 Sharding and parallelism (owner target 12-16 shards, all jobs start at minute 0)

Measured base: Windows serial 1726 s, Linux serial 851 s (`CI_PERFORMANCE.md`). Runner start about 2 min.
Measured with #57's shape (commit ed14a9f, run 39733332477, green in **9m46s**): Linux shards 4m33s / 2m04s / 4m41s,
Windows shards 5m09s / 3m24s / 9m22s, installer 7m46s, fast validation 13s. So the long pole today is **Windows shard 3
(9m22s, it included one flake retry)**, then the installer. Shards are unbalanced (2m04s vs 4m41s on Linux), because
`durations.json` was not seeded yet; the first `durations` merge on main fixes most of that by itself.

* **Linux does most tests.** Mark Windows-specific test files with `pytestmark = pytest.mark.windows` (Job Objects,
  `jarvis.cmd`, restricted token, Tk popup on Windows, Edge, registry, autostart, Phone Link); `ci_plan.py` already
  plans per OS, so Windows shards get only those files plus a 60 s cross-platform smoke.
* **Shards:** 8 Linux (about 110 s of tests each + 2 min start = about 4 min) and 4 Windows (est. 3-4 min, depends on how
  many files are Windows-only; measure with the first run). Total 12 test shards, plus `installer`,
  `installer-upgrade`, `browser` x2, `scenarios` x2, `validate`, `changes`, `ci-ok` = **19 jobs at peak**, inside the
  20-job limit of a free public repository. Only one batch runs at a time, and light PR runs use 3-6 jobs, so two
  parallel PR runs plus a batch can queue for a minute; acceptable. Shard count is a repository variable
  (`CI_LINUX_SHARDS`, `CI_WINDOWS_SHARDS`) so it can be tuned without a code change.
* **All jobs start at minute 0.** Today (#57) every heavy job `needs: validate`. Validation takes only 13 s
  (measured), but each `needs` hop adds a job start (about 20-60 s). Increment: the `installer` job (7m46s measured, the
  long pole once Windows shards are balanced) needs only `changes`; `validate` stays a required part of `ci-ok`, and on
  failure it calls `gh run cancel` for the run. Saves under 1 min; lower priority than balancing the Windows shards. (The
  CI agent decides; trade-off: a bad commit burns a few runner minutes before the cancel.)
* xdist inside shards: Windows 2 workers, Linux 1 (measured problem with 3, see #57); keep `CI_XDIST_WORKERS`.
* **Test-result cache by content hash:** after a green full run, write `passed-hashes.json` (test file -> sha of: the
  file, its import closure, conftest, fakes, `uv.lock`, OS, Python version) into the Actions cache
  (`jarves-passed-<os>-<run>`, restore by prefix). On a PR, a file whose hash already passed is deselected, the same way as
  "not affected". Same graph as 5.2, so one implementation.
* Single fan-in check: `ci-ok` "CI result (single required check)" (exists in #44/#57). Branch protection requires only it.

### 5.4 Determinism

From branch `ci/deterministic-tests` (local worktree `C:\work\jarves-det`, commit f1977cf, **not pushed yet**):
`core/tools/netguard.py` (any non-loopback connection raises `NetworkBlocked`), `JARVES_TEST_OFFLINE=1` updates stub,
`core/tools/pytest_ci.py` (rerun only failed files once), `flake_report.py` -> `docs/reviews/FLAKES.md`, quarantine
(`@pytest.mark.quarantine` + issue after 2 flakes in 7 days, still runs in a non-blocking step, never skipped). #44 already
has `ci_flake.py` (retry known transient patterns only). Merge the two into one retry tool on top of #57 (one owner:
the CI agent). Timing budgets live only in `perf_gate.py`; tests use `until()`.

### 5.5 Queueing and PR hygiene

* `main` and `next` runs: `cancel-in-progress: false` (main done in #44 head). PR runs: cancel the superseded run of the
  same PR (exists).
* Drafts run only `changes` + `validate`.
* PR hygiene bot (extend `automerge.yml`, daily cron): label PRs with conflicts `needs-rebase` and comment once; label
  PRs older than 7 days without a push `stale`; close PRs superseded by another PR (`Supersedes #N` in the body); keep
  the open-PR count visible in `release_status.py`. Never re-run CI on a PR just because `next` moved.

## 6. Human-only rows (< 15 min)

From the 31 rows of `MANUAL_TEST_4.0.0.md` (PR #46): **22 become automated**, 9 stay human. The generator adds a human
row only when the release changed that area (by changed paths), so most releases need fewer than 9.

| Row | Check (only a human can judge) | From 4.0.0 rows | Min |
| --- | --- | --- | --- |
| H1 | Update the real PC from the dashboard; it restarts with your data (real Defender, real profile) | M02, M30 | 3 |
| H2 | English voice: "Hey Jarvis, what time is it?" sounds natural, right time | M12 | 1 |
| H3 | Hebrew voice: wake + answer, sounds natural | M13 | 1 |
| H4 | Wake 10 times from 1 m; TV does not wake him | M15 (20 -> 10) | 2 |
| H5 | Camera preview, then the camera light goes off within a second | M17, M18 | 1 |
| H6 | Privacy strip words match what you hear; mute works | M19 | 1 |
| H7 | Approval popup: on top, looks right, voice "כן" works | M21 | 1 |
| H8 | Phone Link opens the right number (only if dialing changed) | M24, M25 | 2 |
| H9 | WhatsApp QR link (only if WhatsApp changed) | M27 | 2 |
| | **Total (all rows)** | | **14** |

Automated instead: M01, M03-M11, M14 (training on fixture clips), M16 (exit by text), M20, M22, M23 (popup keys, typed
code: `popup_keys_check.py`), M26 (cost cap against the fake model's usage numbers), M28, M29, M31 (update, rollback,
check against `FakeReleaseServer` on the Windows VM pack).

## 7. Failure handling

* **Red batch:** `core/tools/batch_bisect.py` (new): takes the failing test files and scenarios, lists the PR merge
  commits since the last green batch (linear on `next`), runs **only the failing files** on each commit in order
  (1-2 min each, in parallel jobs), names the first bad PR, opens a revert PR on `next` with automerge, and comments on
  the original PR. A flake (passes on every commit) goes to the quarantine flow instead. Until the revert is in, `next`
  keeps accepting merges; the next batch includes the revert.
* **Bad alpha/beta/stable after release:** `release_rollback.py --channel <c> --to <previous build> --execute`
  (exists, dry run by default) re-points the channel manifest to the previous build: "promote previous". Users run
  `jarvis update --rollback`. Then fix forward with a higher build.
* **Hotfix lane:** branch `hotfix/<issue>` -> PR to `main` (stable/alpha fix) or `release/X.Y` (beta fix). Full CI on
  the PR (it is the batch for itself), merge, alpha/beta from it, the bot merges `main` back into `next`. Target: fix
  merged to published in about 15 min (8 CI + 3 package + review).

## 8. Installer speed (measured today: about 4 min install, 16.5k files, about 600 MB, 198 MB exe)

| Idea | How | Expected (est., measure with `packaging/installer_metrics.py`) | Risk |
| --- | --- | --- | --- |
| No `.pyc` in the installer | `build_windows.ps1` drops `--compile-bytecode` (lines 93, 100); Python writes `__pycache__` on first import. Install is per user (`PrivilegesRequired=lowest`, `{autopf}` = user folder), so it is writable | about 40% fewer files and about 30% less size to write; first start of the Core +3-6 s once | first-start slower once; the startup perf budget needs a "first start" exception |
| Or: compile **after** install, in background | `[Run]` `python -m compileall -q -j 0` with `runhidden nowait` after setup ends | install time as above, first start normal | CPU for a minute after install |
| Pure-Python libs in one archive | put pure packages into `python\lib\site.zip` (zipimport), keep native packages (`.pyd`, data files) unpacked; checked by `payload_check.py` | files 16.5k -> about 4-6k | some packages read files next to `__file__`; needs a per-package allow list and a test |
| Defender exclusion (option, off by default) | installer task "Faster install and start (Defender exclusion for the JARVES folder)", needs admin once, removed at uninstall; guide entry required | large on PCs where Defender scans each file (it is most of the 4 min, est.) | security trade-off: owner decision 5 |
| Faster CI build | cache the staged payload by hash of `core/`, `godot/`, `packaging/`, `uv.lock`; only re-compress when unchanged | build 7 min -> 3-4 min when only tests changed | none for the bytes: same inputs, same output |
| Delta updates | split the payload into components (`python-libs`, `core`, `face`) each with its SHA256 in the signed manifest; the updater downloads only changed components, full installer stays the fallback | typical update 198 MB -> 5-20 MB | new code in `updates.py`; rollback must keep component backups |

First measure (one CI run, numbers into `perf_budgets.toml` `[installer.*]`, which still says `baseline_pending`):
install time, file count, size, with and without `.pyc` and with the Defender exclusion. Then pick.

## 9. Cost model

* **CI minutes:** the repository is public, so GitHub-hosted standard Linux and Windows minutes cost $0. The real
  limit is **20 concurrent jobs**. Per batch about 19 jobs x 4-8 min = about 90 runner-minutes; per light PR about
  15-25 runner-minutes; nightly about 150. Storage: installer artifacts (198 MB) kept 7 days on PRs, 30 on main; keep
  under the free artifact storage by deleting PR installers after merge (`ci_reuse.py` already finds by tree).
* **API spend in the pipeline: $0.** Fake model everywhere; network guard + `--network none`; secrets never in the
  scenario environment.
* **Optional live campaign:** once per beta, `silent_live.py` + `suite_v3_voice.py` on the owner PC, hard cap **$3 per
  run, $10 per month** (`ai.daily_request_limit`, `daily_realtime_minutes`, plus the delegation cap), off by default,
  started by the owner. Inside the existing V4 total cap (OD-01).
* **AI QA agent:** owner subscription only, caps in `qa_budget.toml` (section 3.4). Expected per release: 0 Opus, about
  1-2 h Sonnet, a few Haiku runs.

## 10. Phased rollout

Status column: **built** = in a PR head now; **in progress (CI agent)** = owned by the agent of #44/#57, do not
duplicate.

| Item | Status |
| --- | --- |
| sharded Windows/Linux jobs (3 + 3), `ci_plan.py` (classify, shard-files, durations-merge), xdist | built, in progress (CI agent): implementation now in #44 (c00a773), evidence in #57; first green run 9m46s (run 39733332477) |
| installer in parallel, `jarves-installer-<tree>`, main reuses the PR proof (`ci_reuse.py`) | built: #44 |
| main runs never cancelled | built: #44 head (9b13c6a) |
| fast `validate` job, `ci-ok` single check | built, in progress (CI agent): #57 |
| known-flake retry `ci_flake.py` | built: #44 |
| candidate + beta publish (`ENABLE_BETA_PUBLISH`), stable gate, sign-off CLI/page, rollback, `release_status.py` | built: #44 (`ci/release-train` is already merged into it) |
| manual sheet generator + 4.0.0 sheet | built: #46 (do not merge until the freeze ends) |
| network guard, offline stub, quarantine, flake report | built locally: `ci/deterministic-tests`, not pushed |
| fake model / live / release / channel / WhatsApp fakes | built (tests and tools), need moving to `jarves.testing` |
| everything else in this document | to build |

| Phase | What | Effort (agent-days) | Win |
| --- | --- | --- | --- |
| **P1 (first, biggest win per hour)** | seed `durations.json` from run 39733332477's junit; push and merge `ci/deterministic-tests` on top of #57; mark Windows-only test files; 8 Linux + 4 Windows shards; installer without `needs: validate` | 1.5 | 9m46s -> about 8 min (est.); no network flakes |
| P2 | `next` branch + fast-forward in `package.yml` + PR hygiene in `automerge.yml`; rename beta -> alpha, add `promote.yml` + `release.py promote` | 2 | no more re-runs of all PRs; three channels |
| P3 | scenario runner (`scenario_run.py`, `jarves.testing.fake_model`, 15 scenarios: smoke + timers, lists, features, doctor, update, recall, approvals) + `scenarios` job + evidence artifact | 3 | first automated "manual" rows |
| P4 | `ci_plan.py affected` + passed-hash cache | 1.5 | light PR CI 4-6 min |
| P5 | sandbox image + Windows installed-app pack + beta gate; sheet `--evidence`; seed + coverage test + CLAUDE.md rule | 2.5 | owner sheet < 15 min |
| P6 | installer measurements, then `.pyc` + Defender option; payload cache | 2 | install about 1-2 min (est.), build 3-4 min |
| P7 | `batch_bisect.py`, `jarves.clock` seam, AI QA wrapper with caps, delta updates | 4 | less owner time on failures; small updates |

Total about 16.5 agent-days, mostly parallel (P1 and P3 have no shared files; P2 waits for #44/#57 to merge).

## 11. Open decisions for the owner (each with a recommendation)

1. **Integration branch `next`** (PRs go to `next`, `main` = only green batches). *Recommend: yes.* It removes the
   re-run storm and makes every `main` commit an alpha.
2. **Bot fast-forward of `main`** (branch protection allows the package workflow to push `main`). *Recommend: yes*,
   only fast-forward, only to a commit whose `ci-ok` is green.
3. **Beta promotion: manual or automatic daily** (when the beta gate is green). *Recommend: automatic daily for alpha
   -> beta, manual for stable.*
4. **Sandbox: Linux container + GitHub Windows VM** (no Windows containers; Windows Sandbox needs Pro). *Recommend:
   yes.* Optional: install Docker Desktop on the owner PC to run the same container locally.
5. **Defender exclusion option in the installer** (off by default, admin once). *Recommend: offer it, off by default*,
   after the measurement shows a real gain.
6. **AI QA agent budget per release** (Haiku 20 runs, Sonnet max 8 sessions, Opus 2 on request; never in CI). *Recommend:
   these caps; the deterministic runner stays the only gate.*

## Commands (summary)

```
python core/tools/ci_plan.py affected --base origin/next --plan plan.json      # new (P4)
python core/tools/scenario_run.py run --tag smoke                               # new (P3)
python core/tools/scenario_run.py run --tag release:4.1.0 --evidence out/      # new (P3)
python core/tools/scenario_run.py seed --version 4.1.0                          # new (P5)
python core/tools/scenario_run.py coverage --since 4.1.0                        # new (P5)
python core/tools/manual_test_sheet.py --version 4.1.0 --evidence out/         # PR #46 + new option
gh workflow run promote.yml -f build=4.1.0.212 -f to=beta                       # new (P2)
python core/tools/release_status.py                                             # exists (#44)
python core/tools/release_rollback.py --channel beta --to beta-v4.1.0.205      # exists (#44)
jarvis release signoff --version 4.1.0 --row H3 --result pass                   # exists (#44)
```
