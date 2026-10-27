# 2026-10-23 Issue registry and diagnose output: readable, classified, auto-resolving (issue #17)

Evidence (owner PC, `jarvis issues list`, 17 open): raw keys as titles (`FINDING_version:4.0.1.278`, `FINDING_anomaly:wake_false`),
environment states shown as P1/P2 bugs (Godot face disconnected, no camera, Pear not running), a fixed code
(`CHAT_TURN_FAILED`) still open, and `jarvis diagnose` printing 92 findings with duplicates and a scrambled Hebrew line.

Root causes: `clean_title` rejected any title with `/` (rates like `0.5/h` in version findings) or over 100 chars and fell back to the
code; the Fixes-line match compared the bare code, never the code inside a `FINDING_anomaly:newcode:X` key; nothing separated
"expected on this PC" from a bug; the Hebrew word inside an English line was reordered by the terminal's bidi algorithm.

## Design
- `core/src/jarves/issue_text.py` (new, pure): `describe(record)` -> title/title_he, meaning, next step, kind (`bug`|`info`), hint,
  evidence count, closed text. A raw key is never a title (generated from the fingerprint family). `classify()`: `info` for
  environment states (face_link / LINK_FACE_DISCONNECTED, camera, Pear/music, Google not connected) and `INFO_*` / `version:*`.
  Also `split_mixed` / `format_mixed` (Hebrew parts on their own `#1:` lines) and `isolate` (FSI/PDI) for bidi safety.
- `issue_registry.py`: titles are cut/normalised instead of replaced by the key (paths, addresses, markup still refused whole);
  findings pass cause/fix as meaning/next step; `version:*` findings become ONE `INFO_NEW_VERSION` item (old per-version records are
  merged by `Registry.tidy()`); `counts()['open']` and `list('open')` count bugs only, `list('info')` the information items
  (header badge unchanged: it reads `open`); ignoring an info item (dashboard "Ignore this on this PC", `jarvis issues ignore ID`) sticks.
- Auto-resolve (`tidy()` / `check_updated()`): a Fixes line (code inside a FINDING key counts) closes an open issue not seen in the
  installed version; an issue not seen for `QUIET_DAYS` (3) after an update to a newer version closes as `closed_reason=quiet`
  ("not seen since <last version>"); history (count, versions) is kept; it reopens if it comes back. Runs from the Core start, the
  CLI list/show/export and the dashboard list/count. "N runs" is not tracked by the registry; days only.
- CLI: `jarvis issues list [--open|--all|--info] [--since 7d|12h|2w|DATE] [--json]`, `fixed ID [--in V] [--note T]`,
  `export [--all|--open|--info] [--since] [--format json|text] --out FILE` (UTF-8, no BOM, LF; `--json` to stdout is ASCII-escaped so a
  legacy console never fails).
- Diagnose (`diagnose.by_cause` / `render_summary`, `cli.cmd_diagnose`): store issues merged by cause (both dead-air rules; a tool's
  fail/never-returned/repeat), worst severity kept, counts summed, environment states listed separately as "not problems";
  dashboard `/api/issues` rows carry `cause`, `kind`, `hint`; overview "open issues" counts causes without info.
- Dashboard `bugs.js`: Information filter, meaning/next step/closed text, "Ignore this on this PC". Texts via `catalog_add.py`.
- Guide `issue_reports.toml` (EN+HE) extended; no new feature, so no new parity row.

## Verified
`uv run pytest core/tests/test_issue_registry_noise.py core/tests/test_issue_registry.py core/tests/test_guides.py core/tests/test_cli_parity.py core/tests/test_hebrew_texts.py core/tests/test_dashboard_locales.py` (offline), then `python core/tools/prepush.py`.

## Not verified
- Not run on the owner's real data/PC; the classification rules are matched on the codes in the issue text, not on a captured
  `issues_registry.json`. The sample titles in the issue are covered by tests.
- Windows console rendering of the `#1:` Hebrew lines and of FSI/PDI marks in files (the CLI uses `#1:` lines, not marks).
- Browser rendering of the new Bug reports controls (no real-browser test was run for them).

## Decisions needed
- `QUIET_DAYS = 3` and "environment state = info" for Google-not-connected: confirm. Camera/Pear/face are info even when the feature
  is switched on (the owner can ignore per PC); a stricter rule would need the registry to read the feature switches.
