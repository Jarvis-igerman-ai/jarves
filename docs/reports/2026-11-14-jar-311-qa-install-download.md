# JAR-311: QA install of beta 4.0.1-beta.402 failed at download (BLOCKED)

Status: BLOCKED, root cause UNVERIFIED. No code changed.

## What failed
The factory QA install job (13 Nov 01:24 Israel, result file
`C:\factory-eval\factory2\qa\data\installs\job-20261112T230358Z.result.json`)
stopped at stage `download`: `RuntimeError: download of
JARVES-Setup-4.0.1.2716.exe failed (gh exit 1)`. Nothing was installed and
nothing needed a rollback. The ticket's log tail is empty.

## What was checked (14 Nov 2026)
- The message text `download of ... failed (gh exit ...)` does not exist anywhere
  in this repo. The download is done by the factory QA harness on the owner PC
  (`C:\factory-eval\factory2\qa`), which is not in `Jarvis-igerman-ai/jarves`.
- The product's own update path (`core/src/jarves/updates.py`,
  `install_latest.py`) downloads over HTTPS from `DEFAULT_RELEASES_REPO`
  (`igalgerman/jarvis-releases`), not with `gh`, and is not involved.
- Beta installers are published to `igalgerman/jarvis-releases`
  (`packaging/release.py`, tag `beta-v<build>`). This repo has no beta
  releases. The cloud session could not read the releases repo, so it is
  UNVERIFIED whether `beta-v4.0.1.2716` and its asset
  `JARVES-Setup-4.0.1.2716.exe` existed at 01:24, or whether `gh` on the QA
  machine was signed in.

## Likely causes (unverified, in order)
1. The harness ran before the beta release/asset was uploaded (race with
   `beta.yml`), or used the wrong repo/tag for `gh release download`.
2. `gh` on the QA machine was not authenticated, or hit a network error.

## Needed to continue
The harness source (or its `gh release download` command line) and the
stderr of the failing `gh` call from the result file. With that, the fix and a
regression test belong in the harness's own repo, not in the JARVES product.
