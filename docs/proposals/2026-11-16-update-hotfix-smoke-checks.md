# Smoke checks for the update and hotfix paths (JAR-129), proposal only

Read-only review of how the update, rollback and hotfix write paths are tested today, and a proposal for small
smoke checks that close the gaps. No code, workflow or installer file was changed. Every check below that touches
`packaging/`, `.github/workflows/`, `hotfix*.py` or `updates.py` is **Tier R** (hotfix/update write paths,
installer/packaging, workflows): it needs its own ticket, the `no-automerge` label and an independent Opus review.
Nothing here is authorised work. Line numbers are from `origin/main` at `f4390920` (16 Nov 2026).

## The paths in scope

| Path | Entry | What it writes | Code |
|---|---|---|---|
| Manual update | `jarvis update install`, dashboard button | download, signed-manifest check, pre-update backup, `previous.json`, silent setup | `updates.py:574` `prepare_install`, `updates.py:652` `launch_installer` |
| Auto update | `auto_update` switch (beta: on) | same signed path, picked at a quiet moment | `auto_update.py:1-25` |
| Install latest | `jarvis install-latest` | same path plus a `--finish` phase: doctor, selftest, status, self-rollback | `install_latest.py:1-20` |
| First-start watch | Core start | `pending.json` attempts; second failed start asks for a rollback | `updates.py:844` `note_start`, `updates.py:905` `offer_rollback` |
| Installer health check | setup `/RESTARTJARVES=1` | swaps back to the previous program when the new Core never gets ready | `packaging/smoke_windows.ps1:215-242` |
| Rollback | `jarvis update --rollback` | data restore from the pre-update backup, then old installer with `/ROLLBACK=1` | `updates.py:799` |
| Hotfix apply | `jarvis hotfix apply --from <ref or folder>` | backup, originals, atomic file copy, build mark, restart, doctor, auto-revert | `hotfix.py:791`, `hotfix.py:858` |
| Hotfix slice 2 | same, with `uv.lock` or bridge pin changes | hash-pinned wheels into site-packages, bridge exe swap | `hotfix_slice2.py:594`, `hotfix_slice2.py:755` |
| Hotfix revert | `jarvis hotfix revert` | originals back, refused if an update already replaced the hotfix | `hotfix.py:1034` |
| Hotfix recover | `jarvis recover-hotfix` (stdlib only) | same restore when `jarvis` itself cannot start | `hotfix_recover.py:199`, `packaging/windows/jarvis.cmd:14` |

## What is already covered

- **Installer, upgrade and rollback on a real Windows runner.** `jarves-checks.yml:544` runs
  `smoke_windows.ps1` (silent install, Core start, doctor, uninstall residue) and `jarves-checks.yml:575` runs
  `upgrade_matrix.ps1` (old 4.0.0.153 installer from `upgrade_fixtures.json` -> seeded data -> silent upgrade ->
  migration with backup -> `jarvis update --rollback --yes` -> old version and data back). The smoke also covers
  locked files, a stale rollback folder, low disk, the health-check swap back, `/WASRUNNING` and "restart only what
  ran" (`smoke_windows.ps1:150-405`). This is a strong base; nothing below replaces it.
- **Unit tests with fakes.** 34 tests in `test_auto_update.py`, 24 in `test_update_flow.py`, 14 in
  `test_updates.py`, and about 110 across the eight `test_hotfix*.py` files. They use a fake install tree and fake
  hooks.

## Gaps

1. **No hotfix ever runs against a real installed build in CI.** `packaging/` and the workflows contain no
   `hotfix` call. The feature row says so itself: `hotfix.py:1245` "never run against a real install by the tests"
   and `hotfix.py:1246` "no real installed-build hotfix recorded yet". The fake hooks cannot catch: the real
   `jarvis backup` / `jarvis doctor` subprocesses, the embedded Python under `{app}\python`, Windows file locks on a
   running Core, `_build_info.json` marking that `jarvis version` reads, or `jarvis.cmd`'s `recover-hotfix` branch.
2. **The in-app update path is not exercised on the installed build.** The upgrade matrix starts setup directly
   and calls `--rollback`, but never `jarvis update install` / `install-latest`. So `prepare_install` (signed
   manifest re-fetch, download, SHA256 and size, backup, `keep_previous`) and `launch_installer`'s second hash are
   tested only with fakes. A candidate whose built-in `UPDATE_PUBLIC_KEYS` cannot verify the live manifest would
   ship and then fail to update in the field.
3. **No check that an update supersedes a hotfix.** `hotfix revert` must refuse after a normal update replaced the
   files (`hotfix.py:1034`), and `jarvis version` must drop `+hotfix.N`. Unit-tested only.
4. **Slice 2 (wheels, bridge exe) has no installed-build check.** Highest risk, since it writes into
   site-packages and swaps an exe. Hardest to smoke without network.

## Proposed checks

Ordered by value for cost. All run in the existing Windows smoke job against the candidate installer, with
`JARVES_HOME` set to the test folder (`smoke_windows.ps1:46-47`), offline unless stated. No new runner, no secrets.

### S1. Hotfix round trip on the installed build (recommended first; Tier R, `packaging/` only)

A new `-Hotfix` block in `smoke_windows.ps1` after the functional install:

1. Copy the installed `jarves` package to a temp folder; change one harmless data file (for example add a
   `smoke_marker` key to a guide TOML that the Core does not need) so the plan has exactly one `replace`.
2. `jarvis hotfix apply --from <temp> --dry-run`: exit 0, lists exactly that file, nothing changed on disk (hash
   of the installed file unchanged).
3. `jarvis hotfix apply --from <temp>` with the Core running: exit 0; `jarvis version` ends with `+hotfix.1`;
   installed file hash equals the new one; a backup exists in `jarvis backup list`; the Core is running again;
   manifest `status = applied`, `doctor_rc = 0`.
4. `jarvis hotfix revert`: exit 0; hash is back to the original; `jarvis version` has no `+hotfix`.
5. `jarvis hotfix status`: no mark.

Proves: real backup/doctor subprocesses, embedded Python, file replace on a running install, build mark, revert.
Cost: about 1 to 2 minutes on the existing job. Does not prove slice 2.

### S2. A bad hotfix reverts itself (Tier R, `packaging/` only)

Same setup, but the changed file is a Python module the Core imports at start, with a syntax error.
Expect: `apply` exits non-zero with `HOTFIX_REVERTED`; the installed file hash equals the original; the manifest
says `reverted`; the Core runs again and `jarvis doctor` is as clean as before. This is the fail-closed promise in
`hotfix.py:27` and the most valuable single smoke, because an owner hotfix runs on the only working install.

### S3. Recovery when `jarvis` cannot start (Tier R, `packaging/` only)

Apply a good hotfix whose one changed file is a module the CLI imports at start. Then overwrite that same
installed file with a syntax error, so the hotfixed file is what breaks `jarvis`. This is the case where a hotfix
broke start-up and `jarvis hotfix revert` cannot run. Expect: `jarvis hotfix revert` fails to start;
`jarvis recover-hotfix --dry-run` lists the file and changes nothing; `jarvis recover-hotfix` restores the saved
original through the stdlib-only branch of `jarvis.cmd`, and `jarvis version` works again. Recover checks the
saved originals, not the current file (`hotfix_recover.py:86-95`), and it accepts the newest applied manifest
whose id matches the build mark (`hotfix_recover.py:155-172`). Files outside the hotfix (for example a broken
`__init__.py` that was never hotfixed) are out of scope: recover never restores them.

### S4. Update supersedes a hotfix (Tier R, `packaging/` only)

In the upgrade matrix: install the old fixture, apply S1's hotfix (needs the old build's `hotfix` command, which
4.0.0.153 has), upgrade silently to the candidate. Expect: `jarvis version` = candidate with no `+hotfix`;
`jarvis hotfix revert` refuses (no downgrade). About 1 minute extra on a job that already installs both builds.

### S5. Signed manifest dry run with the candidate's keys (Tier R, `packaging/` plus one HTTPS read)

`jarvis install-latest --channel beta --dry-run` on the installed candidate. It reads the public
`latest-beta.json` and verifies its signature against the keys baked into the candidate (`updates.py:276`,
`updates.py:386`). Expect: "update available", "up to date" or a named reason, but **never** `unavailable` for a
signature reason. Catches a key rotation or `SIGN_DOMAIN` mistake before release. Read-only: the dry run writes
only the check cache (`install_latest.py:18-19`). Needs network on the runner; mark the step "network" and on a
network error report `NOT RUN` instead of failing.

### S6. Full in-app update end to end (not recommended now)

Serving a locally signed `latest.json` to the installed build needs a private signing key in the smoke job. Two
options, both heavier:
- a CI-only test key built into smoke-profile installers only. This changes update trust and workflow secrets:
  Tier R and a security decision for the owner;
- reuse the release key in a job that already has it. This widens the secret to more jobs, so no.

The daily owner-PC `install-latest` beta check already covers this path on real hardware. Do S5 instead. Return
to S6 only if a field update failure shows S5 is not enough.

### Slice 2 (wheels, bridge)

No smoke proposed now. An offline wheel check needs a pinned wheel fixture plus a `uv.lock` delta that matches the
candidate's environment, and the bridge check needs a pinned bridge exe artifact. Both are real work. Record as a
follow-up once S1 to S4 are green: "slice 2 installed-build smoke with a SHA-pinned wheel fixture".

## Observation (not a defect, for the findings ledger)

`auto_update.py`'s docstring says "installed builds ignore env/config overrides". `UpdateChecker` does ignore the
repo, channel and extra-key overrides on an installed build (`updates.py:377-386`), but still honours
`JARVES_UPDATE_BASE_URL` / `[update] base_url` (`updates.py:395-396`, HTTPS only). Trust is not weakened, because
the signature must still verify against built-in keys and `refuse_downgrade` applies. The sentence is broader than
the code. Proposed: reword the docstring, or decide that installed builds ignore `base_url` too. That second
option is Tier R.

## Recommendation

One Tier R ticket for **S1 + S2** in `packaging/smoke_windows.ps1` (one PR, one Opus review). It turns the hotfix
feature's "no real installed-build hotfix" blocker into CI evidence, for about 3 minutes on an existing job. Next:
S3, S4 and S5 as a second small PR. The owner-PC hotfix
run stays the acceptance evidence: CI on a clean runner is not the owner's install.

## UNVERIFIED

- Run times above are estimates, not measured.
- Whether 4.0.0.153's `hotfix` command can apply to its own tree (S4) was not run.
- S3 expectations are read from `hotfix_recover.py`, not run on an installed build.
