# Quality drift scan (JAR-91), 2026-11-09

Scope: `core/tools`, `core/src/jarves` (excluding workflow/, orchestrator/, .github/, .claude/).
Method: `git ls-files`/`git grep` only. ruff, vulture and pyflakes are NOT installed, so unused imports came from a throwaway AST scan (name occurs once in the file, no `__all__`). Not a substitute for ruff; results are UNVERIFIED against it.

## (a) core/tools with no references
- `core/tools/m2_dashboard_screens.py`: the only script with no reference in any tracked file (word match). Possibly a manual tool; owner to decide delete or document. Not touched.

## (b) DOC_MAP / CONTEXT_MAP links
- No dangling paths found. Mentions without a directory (`release_signoff.py`, `release_gate.py`, `suite_*.py`, `latest.json`) resolve to existing files or to a published artifact.

## (c) Unused imports
Fixed in this PR (plain stdlib imports, no test patches them): `cli.py` (zipfile, timezone), `diag/preflight.py` (sys), `diag/service.py` (sys), `mobile_prefs.py` (time), `perf_advisor.py` (re), `triage.py` (os, tempfile).

Left alone, needs a human call (possible re-exports, side-effect or lazy imports, test patch points):
- src: `app.py` httpcore; `camera/backends.py` 5 names; `camera/cli.py` explain; `chatgpt_brain.py` INSTRUCTIONS; `dashboard.py` client_allowed; `dialing.py` Any; `features.py` sys; `github_write.py` github_prs/github_merge; `issue_registry.py`, `security_scans.py` argparse; `mobile_companion.py` capabilities; `mobile_drive.py`, `projectmgr/runners.py` Path; `operations.py` security_scans_mod; `selfcheck.py` asdict/timezone; `text_session.py` asyncio; `ui_blocks.py` anydo; `usage.py` field; `voice/live.py` json; `whatsapp_bridge.py` unicodedata/AESGCM.
- tools: `campaign_*.py`, `suite_*.py`, `redaction_audit.py` (re, np), `suite_system.py` (shutil, tomllib), `suite_voice.py` shutil. Many `cli`/`suite_*` imports look like registration side effects.
Full raw list: rerun the AST scan; ~68 hits total.
