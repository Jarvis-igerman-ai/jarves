Doc drift and dead-code scan of `core/tools` and `core/src/jarves` (JAR-91). Factory 2 quality track. Status: current (main 710f9ac, 14 Nov 2026).

Excluded by scope: `workflow/`, `orchestrator/`, `.github/`, `.claude/`. Tools: `git ls-files`, `git grep`, `ruff` (vulture not installed: not run).

## (a) core/tools scripts with no references or tests

Method: for every tracked `core/tools/*.{py,js,ps1}`, `git grep` its stem outside itself and `changelog.d/`.

| Script | Finding | Action |
|---|---|---|
| `core/tools/m2_dashboard_screens.py` | Only its own usage line mentions it; no test, doc or script calls it. Siblings (`m3_screens.py`, `m4_dashboard_screens.py`) are referenced | Left in place: deleting a screenshot tool is not unambiguous (owner may still use it for SW-V2 M2 review shots). Candidate for removal or a DOC_MAP row |

All other scripts have at least one reference.

## (b) DOC_MAP / guides entries pointing to missing files

- `docs/DOC_MAP.md` Markdown links: **0 missing**.
- Backtick paths in DOC_MAP: `latest.json`, `release_gate.py`, `release_signoff.py`, `suite_music.py`, `suite_voice.py` are shorthand names (a release asset and files under `core/tools/` / `core/src/jarves/`) that all exist; no drift.
- `core/src/jarves/guides/data/*.toml` paths under `docs/` or `core/`: **0 missing**.

## (c) Unused imports / variables (ruff F401, F811, F841)

`ruff check --select F401,F811,F841` found 92 items: 37 in `core/src/jarves`, 55 in `core/tools`.

Fixed in this PR (unused stdlib imports, no monkeypatch or re-export use, guarded by `core/tests/test_no_unused_imports.py`):
`diag/preflight.py` (`sys`), `diag/service.py` (`sys`), `perf_advisor.py` (`re`), `triage.py` (`os`, `tempfile`).

Left for follow-up (not unambiguous or outside the ~8 file budget):

| Location | Items | Why not fixed here |
|---|---|---|
| `cli.py` | `platform`, `zipfile`, `datetime.timezone`, local `time` (1450), unused `bridge` (1836) | large shared file; tests may patch `cli.*` attributes; a separate PR |
| `updates.py:1045` | F811 `auto_update` redefined before use | needs a reading of the import order, possible behaviour change |
| `dashboard.py` | `client_allowed` | may be a deliberate re-export for tests/callers |
| `mobile_companion.py`, `projectmgr/surfaces.py`, `chatgpt_brain.py`, `camera/cli.py` | relative imports (`.capabilities`, `.SKILL`, `.brain.INSTRUCTIONS`, `.ux.explain`) | possible side-effect import or re-export |
| `app.py:164` | `httpcore` | likely an availability probe inside a try |
| `features.py`, `selftest.py`, `voice/live.py`, `whatsapp_bridge.py`, `mobile_prefs.py`, `mobile_drive.py`, `projectmgr/runners.py`, `dialing.py`, `issue_registry.py`, `security_scans.py`, `selfcheck.py`, `usage.py`, `text_session.py` | unused stdlib/typing imports | safe-looking; next batch (file budget) |
| `anydo.py`, `scheduler.py`, `selfcheck.py`, `security_scans.py` | F841 unused `except ... as e` / locals | cosmetic |
| `core/tools/campaign_*`, `suite_*`, `silent_live.py`, `connections_screens.py`, `redaction_audit.py` | 55 F401/F841 | live campaign scripts, not run in CI; fixing without a live run is unverified |

Reproduce: `ruff check --select F401,F811,F841 --exclude core/src/jarves/workflow,core/src/jarves/orchestrator core/tools core/src/jarves`.
