# JAR-311: QA install of beta 4.0.1-beta.402 failed at download (investigation, BLOCKED)

Date: 19 Nov 2026. Status: BLOCKED. The root cause is UNVERIFIED and there is no code change.

## Symptom (from the ticket)
A factory QA install job (`job-20261112T230358Z`, 13 Nov 01:24 Israel time) stopped at stage `download` with
`RuntimeError: download of JARVES-Setup-4.0.1.2716.exe failed (gh exit 1)`. Nothing was rolled back or reinstalled.
The ticket's log tail is empty.

## What this repository shows
- No code in this repository raises that error. The QA install harness lives in factory2 on the factory PC
  (`C:\factory-eval\factory2\qa\`), not in `Jarvis-igerman-ai/jarves`.
- Betas are not published to this repository. The releases list here holds only stable `win-v*` and `v3.0.0`.
  `packaging/release.py beta-publish` publishes each green `main` to the releases repository
  (`igalgerman/jarvis-releases`) as pre-release `beta-v<build>` (`BETA_PREFIX`, line 67).
- After every beta publish, the publisher deletes all but the newest `BETA_KEEP = 5` unpinned betas, along with their
  tags (`packaging/release.py`, lines 68 and 656-664).

## Most likely cause (UNVERIFIED)
`beta-v4.0.1.2716` (4.0.1-beta.402) was probably deleted by the beta pruning between the moment QA queued the job and
the moment it downloaded the installer. Five newer green `main` builds would be enough. A `gh release download` of a
deleted release exits 1.

Other causes still possible, none checked: wrong `--repo` in the harness, or an expired/missing `gh` token on the factory
PC. Checking needs the factory PC, which nobody here can reach.

## Needed to decide the fix
1. The result file `C:\factory-eval\factory2\qa\data\installs\job-20261112T230358Z.result.json` and `gh`'s stderr.
2. Whether `beta-v4.0.1.2716` still exists in `igalgerman/jarvis-releases`. This session cannot read that repository.

## Possible fixes (none applied)
- In the factory2 harness: resolve the newest published beta when the job starts, not when it is queued, and say
  "release pruned" instead of a bare `gh exit 1`. This is outside this repository.
- In this repository: pin the beta under QA (`[pinned]` in its notes is already skipped by pruning), or raise
  `BETA_KEEP`. Both change the release path (Tier R), so they need the owner's decision and an Opus review.
