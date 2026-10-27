# 2026-10-23 — no shared "append here" files (merge-conflict reduction)

**Asked (owner):** PR #80 conflicted in `locales/en.js` and `he.js`; "it is a general issue", reduce conflicts everywhere. PR #80 itself was not touched.

**Cause:** a few files are edited by almost every PR at the same place. Counted over the last ~150 merges: HANDOFF.md 81 (already fixed), en.js/he.js 59 each, index.html 41, dashboard.py 41, cli.py 40, jarves-checks.yml 40, cli_parity.py 37, DOC_MAP.md 35, shell.js 33, CHANGELOG.md 33, setup_wizard.py 32, hebrew.py 32.

**Changed (one PR, one new file per change instead of a line in a shared file):**
| Was | Now |
|---|---|
| texts in `locales/en.js` + `he.js` | `locales/parts/<date>-<topic>.json`, written by `catalog_add.py` (`--part`, `--fold` at release); server, selftest and tests read base + parts (`jarves/locale_parts.py`) |
| lines in `CHANGELOG.md` | `changelog.d/<date>-<topic>.md` (`jarves/changelog_parts.py`, `core/tools/changelog_build.py --write/--check`); release notes, issue-registry "Fixes:" tracking, manual test sheet and the Windows package read them |
| rows in `DOC_MAP.md` for new reports | `docs/reports/<date>-<topic>.md`, covered by one DOC_MAP row |
| entries in `cli_parity.FEATURES/TOOL_GROUPS` | `[parity.features]` / `[parity.tool_groups]` in the feature's own guide toml |
| lines in `hebrew.py` `TEXT` | `jarves/hebrew_parts/<date>-<topic>.json` |
| `PLUGIN_MODULES`, `ADDON_MODULES`, `add_cli` + `COMMANDS` at the end of `cli.py` | `jarves/plugins.d/<feature>.toml` (`jarves/plugin_registry.py`) |

Existing entries were NOT moved (that would conflict with every open PR); only new ones use the new places. Open PRs keep working: an old-style edit still merges, `catalog_add.py --resolve` still resolves en.js/he.js.

| nav links and `<script>` tags in `index.html` | `jarves/pages.d/<page>.toml` (`page_registry.py`); the server composes the page from marker comments in index.html |

**Verified:** targeted tests pass (locales, changelog, docs links, hebrew texts, cli parity, guides, plugin registry, setup wizard, dashboard, release notes, manual test sheet, issue registry) and `python core/tools/prepush.py`. Full suite: CI.

**Not done (decision for the owner):**
- Existing nav entries stay written in `index.html` (about 20 tests parse it); only new pages use `pages.d`.
- `.github/workflows/jarves-checks.yml` (40 merges) is not split here: a high-risk CI change goes alone (CLAUDE.md).
- `CLAUDE.md`/`AGENTS.md` still say "a `guides/data/<feature>.toml` entry, a CLI parity entry, Hebrew via `catalog_add.py`": still true, but they should name `[parity.*]`, `changelog.d` and `docs/reports` (high-risk files, owner edits).
- Run `catalog_add.py --fold` and `changelog_build.py --write` when a release is cut (add to docs/RELEASING.md checklist).
