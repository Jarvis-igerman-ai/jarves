# Auto-update (issue #17)

## Asked
The owner should not have to type `jarvis update install`. Security first: the SAME signed-update path, no new download source, no new trust. Later owner decision: ON by default only for beta-channel users, OFF for stable; an explicit choice always wins; the channel is chosen at install (or first run); everything stays changeable.

## What changed
- `core/src/jarves/auto_update.py` (new): `AutoUpdater` decides only WHEN to run `UpdateChecker.check()` -> `prepare_install(approved=True)` -> `launch_installer(silent=True, restart=True)`. Stricter than before: it refuses any update that is not from the signed manifest, even in a source checkout.
  - Notice BEFORE (EN+HE, "update now / later / turn off"), then a 15 min grace, then install only at a quiet moment: no voice/chat session, not listening, no pending approval, no running coding-agent session, not in quiet hours or paused alerts (`core_busy`). "Update now" is the owner's explicit choice and skips the wait.
  - Notice AFTER once the new version is healthy (what changed, how to roll back). Backup and `jarvis update --rollback` are the existing ones.
  - Failure: nothing changes, back-off 1 h / 4 h / 12 h, after 3 failures for one version it stops and tells the owner once; a newer version or "update now" starts again. A setup that never ran (`not_applied`) counts as a failure.
  - Channel: stable gets `latest.json`, beta `latest-beta.json`; a channel switch restarts the announcement and installs nothing; a downgrade is refused by the existing `refuse_downgrade`.
  - Core thread (`auto_update.start`, every 10 min, first look after 5 min), `after_start()` hook in `app._update_confirmed`.
- Default follows the channel: `features.Features.default("auto_update")` returns `enabled("beta_channel")` (never cached); an explicit value in `features.json` wins and survives channel changes and upgrades; `features reset` returns to following the channel. Turning on needs `update_check`.
- First-run choice (instead of an installer page, see below): a one-time EN+HE notice plus a card on the Updates page ("Which update channel?" with four buttons: stable/beta x automatic yes/no). It only asks; until answered the channel stays stable and nothing installs by itself. `set_channel` (CLI, dashboard) also marks it answered; someone with an explicit beta choice is not asked.
- Surfaces: `jarvis update auto on|off|status|now|later` (`cli.py`), Updates page card ("Automatic updates: on (beta default) - Turn off"), routes `/api/updates/auto`, `/auto/answer`, `/auto/channel`, setup-wizard "updates" group, tour topic, CLI parity (`auto_update` -> `update`), features/capabilities row (Windows `implemented_unverified`, Pi `planned`).
- Guide `core/src/jarves/guides/data/auto_update.toml` (EN+HE, steps, problems with codes), Hebrew dashboard strings via `core/tools/catalog_add.py`.
- Tests `core/tests/test_auto_update.py` (30): defaults by channel, explicit wins, upgrade keeps, silent install path never switches channel, on/off, quiet-moment gating, notices, signed only, unsigned/older refused, stable vs beta, failure leaves old version, back-off, give-up, dashboard API, CLI.

## Verified
`uv run pytest tests/test_auto_update.py test_guides test_cli_parity test_updates test_update_flow test_release_channels test_capabilities`: 147 passed (fake loopback release server, fake clock, fake launcher; no real install). Prepush result: see the commit/PR.

## NOT verified
- No real installer, published release, Windows run or real update was done. The Core thread and `core_busy` were tested against fakes, not a running Core.
- Crash-looping new version: the existing watch (`note_start` -> approval -> `offer_rollback`) still asks the owner; it is NOT rolled back automatically, because a rollback restores the pre-update data and must not happen without a yes.
- Install-on-quit / at app start is not implemented (quiet-moment only).
- Installer (`packaging/windows/jarves.iss`, Inno Setup) was NOT changed: it cannot be built or tested here and a wrong change could break installs/upgrades. The channel choice is therefore on first run of the app. A later installer page would have to write the same `features.json` keys (`beta_channel`, `auto_update`) only on a fresh install, never on upgrade or `/VERYSILENT`.
- No `.github/workflows` change is needed.

## Decisions needed
1. Confirm the installer page can wait (first-run prompt instead), or approve a separate PR for `jarves.iss`.
2. The 15 min grace and "later = 24 h" values.
3. Whether a crash-looping update should roll back automatically (needs a data-restore policy).
