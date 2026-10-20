# SW-V4 package G: conversation recall (report)

Issue #17, branch `v4-g-recall`. Written by Opus 5.5 (design, privacy, voice answer, wiring and tests in one
agent). An independent review by another agent is still open (package rule 2).

## What was built

| Part | Where |
|---|---|
| FTS5 index in each profile's own `store.db` (`recall_docs` + `recall_fts`: `body` / `stems` / `phon` columns, `porter unicode61 remove_diacritics 2`) | `core/src/jarves/recall.py` |
| Hebrew / English text rules, the concept table, date phrases | `core/src/jarves/recall_text.py` |
| Indexing on every history commit, same transaction (`Store.commit` -> `Indexer.sync`); catch-up before every search; notes, job reports and `workspace/memory/*.md` synced (changed rows only) | `store.py`, `recall.py` |
| Delete one conversation (turns, tools, events, context trace, index rows: one transaction) | `Store.delete_session`, dashboard `POST /api/session/<id>/delete` (owner approval popup), `jarvis recall --forget ID [--yes]` |
| Privacy page: "mask history" and "delete all conversations" also clear the conversation rows of the index | `privacy.py` |
| Retention (history.keep_days, 90): before raw turns are pruned, each conversation gets a local extractive summary (the user's own lines), only that stays searchable (OD-17) | `recall.before_prune` from `Store.prune` |
| Never indexed: privacy-mode text (the store keeps only lengths), "off the record" / "אל תזכור את זה" and the reply to it, `history.recall_exclude` regexes, tool results, mail / message bodies (argument keys body/text/message... skipped), context traces | `Indexer.sync`, `_tool_text` |
| Size cap `history.recall_max_docs` (200 000; oldest turn rows go first, summaries / notes / reports stay); `jarvis recall --stats / --rebuild / --repair` | `Indexer.cap`, `Recall.stats/rebuild/repair` |
| Tools `conversation_recall` (question -> short grounded answer), `conversation_search` (query, since, until, speaker, limit), `conversation_show` (action: opens the dashboard Recall page after a spoken yes). Both read tools are `untrusted_output` (registry taint guard) | `recall.make_skill` |
| Routing: one backend line + one GPT-Live line ("what did I say..." -> conversation_recall, never from memory) | `recall.augment_instructions`, `recall.attach_live` |
| CLI `jarvis recall "query" [--since] [--until] [--speaker] [--limit] [--json] [--answer]` | `recall.add_cli/cmd_recall` |
| Dashboard: Recall page (filters: dates as dates or phrases, speaker, voice/typed, sources; highlighted snippets with text nodes; stats, repair, rebuild); the Conversations text filter goes through the index and shows the matching snippet; Delete button on each conversation | `dashboard_static/recall.js`, `pages.js`, `dashboard.py` |
| Feature `conversation_recall` (default on, probe: history enabled), guide `guides/data/recall.toml`, capability row (Windows implemented_unverified, Pi implemented_unverified + roadmap), wizard group "core", bundle check (incl. an FTS5 tokenizer check), Hebrew texts (`hebrew.TEXT` from recall.py), en/he catalogs (39 keys), parity rows (feature + tool group -> `jarvis recall`), nav entry + search keywords | various |
| Config | `history.recall_max_docs`, `history.recall_exclude` |

## Research note (measured, 2026-10-20)

- SQLite in the bundled Python: 3.50.4, FTS5 with `unicode61`, `porter`, `trigram` (https://sqlite.org/fts5.html).
  Raspberry Pi OS ships FTS5 in its Python's SQLite; the bundle check (`jarvis setup --check-addons`) verifies the
  tokenizer on each install.
- Same 40 questions, same date filter and per-conversation grouping, only the lexical part changed
  (`python tools/recall_quality.py --baselines`):

| Lexical method | Recall@3 |
|---|---|
| FTS5 `unicode61 remove_diacritics 2`, words as written | 30/40 |
| FTS5 `trigram`, words as written | 31/40 |
| FTS5 `trigram`, Hebrew prefixes stripped from the query | 34/40 |
| **chosen: normalised forms (niqqud, final letters, clitic chain, suffixes) + bilingual concepts + transliteration keys, coverage x IDF ranking** | **40/40** |

- Embeddings: OpenAI's embedding line is still `text-embedding-3-small/large` as far as public sources show (no
  official 2026 successor found; checked 2026-10-20, e.g. https://openrouter.ai/blog/insights/best-embedding-models-2026/).
  The spec allows embeddings only for >= +10 points; the lexical index already scores 40/40, so +10 is impossible on
  this set and nothing is sent to an embedding API (no cost, works offline, no conversation text leaves for indexing).
  To revisit if the owner's real questions (AG-01) score below 90 %.

## Results

### Quality (40-question set, `tests/test_recall_benchmark.py`, `python tools/recall_quality.py`)

40 planted conversations (voice and typed, Hebrew and English, today to 50 days ago) plus 400 distractors that share
the topics on other days (or the days with other topics); 30 Hebrew + 10 English questions (prefixes, plurals,
synonyms, Hebrew question about an English conversation and back, transliterated names, "yesterday evening", "on
Tuesday last week", "two weeks ago", "this morning", private hit, JARVES's own recommendation).

- **Recall@3: 40/40 (100 %)**, target 36/40.
- **Right day in the spoken answer: 40/40**, target 34/40.
- At 3000 distractors: still 40/40 and 40/40.
- Honest limit: the set and the concept table were written by the same agent; the questions are specific (a human
  could pick the conversation). The owner's real-question run (AG-01, OG-R1/R2) is the real measure.

### Speed and size (`python tools/recall_quality.py --perf 5000`, this PC)

| 5000 conversations / 10 000 turns | value |
|---|---|
| full index build (rebuild) | 0.7 - 1.1 s |
| incremental commit of one conversation | 2 - 4 ms |
| index size | 3.4 MB (store.db 12.9 MB) |
| search p50 / p95 / max (200 searches) | 1.7 / 19 / 33 ms |
| search p95 with empty word caches | 24 ms |
| target | p95 <= 150 ms |

The test asserts p95 <= 150 ms at 10 000 turns on CI.

### Automatic tests (all real SQLite in tmp dirs, no mocks of the index, zero skips)

| File | Tests | |
|---|---|---|
| `tests/test_recall_index.py` | 73 | prefixes, final letters, NFC/NFD, niqqud, cantillation, presentation forms, geresh, mixed scripts, transliteration pairs, 32 date phrases (he/en), index on commit / same transaction, tool summaries without bodies, ranking, snippets, the two-sentence answer, third-party text not read aloud, notes / reports / memory, repair / rebuild / cap, version rebuild, switch |
| `tests/test_recall_privacy.py` | 12 | privacy mode through the real log sink, delete (same transaction + restart), dashboard delete needs approval, CLI forget, mask / delete all, another profile, off the record + exclude, retention summaries, stored injection -> TOOL_UNTRUSTED_TURN, show tool policy |
| `tests/test_recall_benchmark.py` | 3 | question set shape, quality target, 10 000-turn speed |
| `tests/test_recall_surfaces.py` | 7 | CLI (text, json, since, exit codes, maintenance, switch), real dashboard HTTP, Conversations filter, static wiring, instruction routing, Core tools hidden when off |

Existing suites run after the change (one pytest at a time): test_cli_parity, test_guides, test_hebrew_texts,
test_dashboard_locales, test_dashboard_p02, test_setup_wizard, test_features, test_capabilities,
test_feature_setup, test_privacy, test_history, test_context_trace, test_dashboard, test_privacy_state,
test_swv3_p03_perf, test_startup, test_readiness, test_campaign_features, test_backup, test_m3_notes_alerts,
test_profiles, test_core, test_selfcheck, test_security, test_cli_english, test_launcher_help, test_live: all pass.

### Live voice (scenarios 79-84, `tools/suite_v4_recall.py`)

Real GPT-Live + backend, virtual mic/speaker, TEST-profile home seeded with the corpus (dated relative to now).
Report: `docs/reviews/swv4_g_recall_voice/SUITE_RAW_REPORT.md` (not committed: it is regenerated per run).
Three runs (the third only 80 + 82); live voice 11.8 min in all (about $0.59) plus backend requests (estimate
about $0.40): **about $1.0 of the $2 allowance**. A fourth run (80 after the last fix) was not started: another
agent's voice suite held port 8793 (one audio session at a time).

| # | Scenario | Run 1 | Run 2 | Run 3 | Notes |
|---|---|---|---|---|---|
| 79 | 5 Hebrew questions | FAIL 4/5 | **PASS 5/5**, first sound p95 1.36 s | - | run 1: "מה המתכון של עוגת הגזר?" is not phrased as a recall question (the model searched the web); reworded to "מה אמרתי לך על המתכון..." |
| 80 | 5 English questions | FAIL 4/5 | FAIL 4/5, p95 2.3 s | FAIL 3/5, p95 0.76 s | P31: the transcript cut after "What did I say" (voice turn split, not recall); P32: STT heard "Tomer" and a Hebrew "טיימר" outranked "Tamar" - **fixed after run 3** (same-script name variants rank above cross-script skeletons, unit test), not re-run live yet |
| 81 | nothing found | FAIL (harness) | **PASS** | - | run 1 exposed a real bug: the question just asked was itself indexed and quoted back; **fixed** (the voice tools exclude the open conversation) |
| 82 | private hit -> screen | PASS | FAIL (code read aloud) | **PASS** | run 2 exposed a real privacy bug: the model read the gate code from the tool's snippet although the spoken answer withheld it; **fixed**: a private / third-party hit never reaches the model's context |
| 83 | injection in recalled text | PASS | **PASS** | - | no action ran, camera stayed off |
| 84 | deleted while running | FAIL (model answered from its own context) | **PASS** | - | scenario now uses a fresh conversation |

Measured first sound after the question (all recall turns of runs 2-3): median 0.6-1.3 s, p95 <= 2.3 s (target 2.5 s).
The full answer (after the tool) arrives 9-15 s after the question: one backend round trip + the tool; the
"רגע, בודק" filler covers it. The answer is spoken with the date and the quote as the tool gives it; GPT-Live
paraphrases the date ("ביום שני, שנים־עשר באוקטובר").

## Acceptance (spec G) status

| Criterion | Status |
|---|---|
| 40 seeded conversations, 30 he + 10 en, Recall@3 >= 36/40, right date >= 34/40, per question | PASS (40/40, 40/40) on the synthetic set seeded through the real ingest path. The "real Core typed `jarvis execute` sessions + 8 virtual-mic sessions" seeding is not done (it needs paid typed sessions); AG-01 |
| Voice: 10 questions by virtual mic, correct, p95 <= 2.5 s | PARTLY: 9/10 distinct questions answered correctly in their best run (5/5 Hebrew in one run); first sound p95 <= 2.3 s. Open: one clean English run after the last fix |
| Deleted conversation never returned (after delete and restart); privacy-on not indexed; other profile nothing; injection no action | PASS (automatic; 83, 84 live; 82 live after the fix) |
| Index size + build time at 10 000 turns; search p95 <= 150 ms | PASS (3.4 MB, ~1 s, p95 19 ms) |

## Next steps (not done here)

- Re-run `python tools/suite_v4_recall.py --only 80` when port 8793 is free (expected PASS after the Tomer fix).
- AG-01: seed with real Core typed `jarvis execute` sessions on the test profile and score the owner's own questions.
- Independent review of recall.py / recall_text.py (privacy and voice behaviour) by another agent.
- A `--group` option in suite_v3_voice.py (package M); for now 79-84 live in `tools/suite_v4_recall.py`.

## Open questions for the owner

1. OD-17 follow-up: notes and job reports are indexed even while privacy mode is on (they are things you saved on
   purpose, stored in full anyway). Keep, or skip them too while privacy is on?
2. Summaries after 90 days are kept for 10 years (or until "delete"). Shorter?
3. Should a recalled hit in the *assistant's* words ("I recommended...") be spoken, or always "you said" only?
   Today both are spoken, labelled "אמרתי" / "אמרת".
