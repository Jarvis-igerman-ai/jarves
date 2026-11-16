# JAR-120: DOC_MAP / CONTEXT_MAP drift check (16 Nov 2026)

Scope: `docs/DOC_MAP.md` and `docs/CONTEXT_MAP.md` against files on disk at `origin/main` 5e2e365. No doc under `docs/workflow/**` was edited.

## Findings

| Check | Result |
|---|---|
| Relative links in both maps resolve to a file/folder | 0 dead links (already enforced by `test_all_relative_links_under_docs_resolve`) |
| `#anchor` parts of those links name a heading in the target | 0 broken (was not checked before) |
| Every `docs/**/*.md` is listed in DOC_MAP (dated `handoff/`, `reports/` covered by their README rows) | 0 missing (already enforced by `test_every_doc_is_listed_in_doc_map`) |
| Every CONTEXT_MAP target is also in DOC_MAP | 0 missing (was not checked before) |
| Back-ticked file names in DOC_MAP (`suite_voice.py`, `suite_music.py`, `release_signoff.py`, `release_gate.py`, `latest.json`) | Not links; the scripts exist under `core/tools/` or `core/src/jarves/`, `latest.json` is a published release artifact. No fix needed. |

No dead links or missing entries were found, so no map was changed.

## Test added

`core/tests/test_docs_links.py`: `_broken_links()` (file and anchor check), `test_link_checker_flags_dead_files_and_anchors` (hermetic `tmp_path` fixture), `test_doc_map_and_context_map_have_no_dead_links_or_anchors`, `test_every_context_map_entry_is_in_doc_map`.

Reproduce: `python -m pytest -q core/tests/test_docs_links.py`.
Mutation check: appending this line to CONTEXT_MAP

```
[x](NOW.md#no-such-heading) [y](reports/2026-11-16-suggestions.md)
```

made the two map tests fail with exactly those targets; reverted.

Not checked: links inside `docs/reviews/` (excluded by the existing test because evidence reports quote old paths) and anchor slugs for headings with unusual Unicode/emoji (the slug helper follows the common GitHub rule only).
