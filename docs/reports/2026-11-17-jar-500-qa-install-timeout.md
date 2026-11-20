# JAR-500: QA install of beta 4.0.1-beta.435 ended with exit 124 and no JSON

Date: 17 Nov 2026. Ticket: JAR-500 (Factory 2, source owner). No product code changed.

## What the ticket says

- Beta 4.0.1-beta.435, kind daily, stage `install`, exit 124.
- `install_build.ps1` printed no JSON summary. Rolled back: no. Revert id: unknown.
- Result file on the owner PC: `C:\factory-eval\factory2\qa\data\installs\job-20261116T230613Z.result.json`.
- The log tail in the ticket is empty.

## Findings

- `install_build.ps1` and the text `no JSON summary` appear nowhere in
  `Jarvis-igerman-ai/jarves` (`git grep -n -i "install_build\|no JSON summary" origin/main`
  returns nothing). The script belongs to the factory QA harness on the owner PC
  (`C:\factory-eval\factory2\qa`), which is not in this repo. This is the same
  situation as JAR-311 (PR #1123).
- Exit 124 is the usual "killed by a time limit" code. The JSON was probably missing
  because the harness stopped the script before it could print it. This is an
  inference, UNVERIFIED.
- Product waits that could add up past a harness time limit (read from the code,
  not measured on the QA machine):
  - setup's post-install health wait: `/HEALTHTIMEOUT=` default 180 s
    (`packaging/windows/jarves.iss`, `RunHealth`);
  - `jarvis install-latest`, if the harness calls it: setup wait up to
    `SETUP_WAIT_S = 1200` s and report wait up to 1500 s
    (`core/src/jarves/install_latest.py`).
- Silent-install steps checked for a wait with no limit (18 Nov, code reading only); none found:
  `jarvis setup --answers` with no `/SETUPANSWERS=` exits 0 at once (`cli.py` cmd_setup);
  a scripted setup skips ChatGPT, Google, connect, tour and browser sign-ins (`setup_wizard.py`);
  add-on prepare uses `timeout=300` (uv sync) and `timeout=20` (SFace download) (`feature_setup.py`);
  `jarvis stop` uses `open_timeout=3`; retaining the old program makes 10 tries, 1 s apart; the PowerShell
  lock and process waits loop until a deadline (`jarves.iss`).
- `packaging/installer_job.ps1` is a CI-only build helper. It never runs an
  installer, so it is not involved.

## UNVERIFIED

- Which command `install_build.ps1` ran, its time limit, and where it was when it was stopped.
- Whether setup itself hung (for example a wizard page shown despite `/VERYSILENT`)
  or was only slow.
- Whether JARVES 4.0.1-beta.435 was left half-installed on the QA machine (rolled back: no).

## What is needed to continue

The harness source, or at least the stdout/stderr and the setup log
(`%TEMP%\Setup Log *.txt`) of job `job-20261116T230613Z`. With those, the fix and
its regression test go in the harness, or in the product if setup really hung.
