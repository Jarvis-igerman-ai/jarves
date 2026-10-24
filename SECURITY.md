# Security policy

## Reporting a vulnerability

Please do **not** open a public issue for a vulnerability. Use GitHub private vulnerability reporting:
https://github.com/Jarvis-igerman-ai/jarves/security/advisories/new

Include what you found, the version (`jarvis --version`), and steps to reproduce. Never include real keys, tokens,
conversation content or personal data; a content-free description is enough. You will get an answer within a few days.

## Supported versions

Only the latest stable release and the latest beta (if you opted in with `jarvis update --channel beta`) receive fixes.
Fixes ship as a new version; JARVES asks before it installs anything.

## What JARVES does to stay safe

- Secrets live in the OS credential store or the environment, never in the repository or in logs (log redaction is
  audited by `core/tools/redaction_audit.py`).
- Updates are Ed25519-signed and verified inside the app before an installer is started.
- Nothing leaves the PC without approval (see the in-app Privacy page and `docs/RELEASING.md` for the release gate).
- CI runs CodeQL (Python), dependency updates arrive through Dependabot, and GitHub secret scanning is on.

## Test tokens

`core/tools/redaction_audit.py` and `core/tools/campaign_stages_v4.py` contain deliberately planted fake tokens to prove
that redaction works. They are excluded from secret scanning in `.github/secret_scanning.yml` and are not real credentials.
