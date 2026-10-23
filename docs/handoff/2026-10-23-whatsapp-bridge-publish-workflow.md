# WhatsApp bridge: manual "Publish WhatsApp bridge" workflow (#17)

**Asked:** the owner wants the "Download the WhatsApp bridge" button (PR #86) to work without building Go or publishing by hand.

**Changed:** `.github/workflows/bridge-publish.yml` (manual, `workflow_dispatch` only), one contract test in
`core/tests/test_ci_pipeline.py`, notes in `docs/RELEASE_HOSTING.md` and `docs/HANDOFF.md` (the old "follow-up workflow" block).

- Build job (windows-2025, no secrets): pinned Go 1.27.0, `scripts/build_whatsapp_bridge.ps1`, the `-Verify` rebuild, SHA-256 must
  equal `packaging/bridge_pin.json`. Uploads exe, `.sha256`, `build-info.json` (1 day).
- Publish job (ubuntu-24.04): `python3 packaging/release.py bridge-publish --dir bridge-dist`. `dry_run` defaults to true (no secrets,
  no network). A real run is refused outside `main`. `RELEASES_TOKEN`, `JARVES_UPDATE_SIGNING_KEY` and `vars.RELEASES_REPO` are set on
  that one step only.
- Action pins: checkout and download-artifact reuse the repo's SHAs; setup-go v7.0.0 (`b7ad1dad...`) and upload-artifact v7.0.2
  (`cf430e03...`) were checked against the upstream tags with `git ls-remote`.

**How the owner runs it:** GitHub > Actions > "Publish WhatsApp bridge" > Run workflow > keep "dry_run" ticked first, read the log,
then run again from `main` with it unticked. Check the app: `jarvis whatsapp install` (or the dashboard button) now downloads it.

**Verified:** actionlint clean over all workflows; `test_ci_pipeline`, `test_ci_workflows`, `test_docs_links`, `test_release_hosting`,
`test_whatsapp_bridge_install` (98) and `python core/tools/prepush.py` pass.

**NOT verified:** the workflow has never run on GitHub. Not confirmed: that the Windows runner reproduces the pinned SHA-256 with Go
1.27.0; the artifact layout (`bridge-dist/` with the three files at its root); that an empty `vars.RELEASES_REPO` falls back to the
default repo; a real upload to the releases repo; the `main`-only guard on a real non-main run (source-level test only).

**Decisions needed (owner):** none new. Open from #86: old versioned assets are not pruned from the `whatsapp-bridge` release; the
downloaded exe is not Authenticode-signed.
