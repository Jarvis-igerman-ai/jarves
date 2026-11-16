# Diagnostics coverage check (JAR-145)

Scope: action receipts (PR #989, still open, checked on branch `factory/JAR-55-action-receipts`) and `core/tools/ci_health.py`.

| Item | Result |
|---|---|
| Receipts failure logging | Uses the bounded logger: `logs.warn("receipts", "RECEIPT_FAILED", ...)`; no content in the message. |
| Receipts content | Tool name, ok, code, origin, ms, seq only (per module docstring); no arguments or results. |
| Receipts in support bundle | Not included as content (bundle only has fixed members). File names were pseudonymized in `files.json`: **fixed** here (`receipts` added to the known-folder list in `diagbundle._KNOWN_FILE`, test `test_inventory_names_receipts_files_but_pseudonymizes_unknown`). |
| ci_health | Developer CLI tool (stdout table, optional `gh` comment), not a runtime module: no logger or bundle path applies. Missing data prints `-`, not a fake 0 (truthful). |
| Receipts size bound | One rotated file (`.1.jsonl`) per the module; not re-verified here (UNVERIFIED, belongs to PR #989 review). |

Not done: runtime check that the receipts file is bounded in practice (UNVERIFIED). One fix of up to five found.
