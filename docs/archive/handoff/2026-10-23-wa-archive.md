# WhatsApp: long retention, encrypted local archive and real search (#17)

**Asked (owner):** keep WhatsApp messages much longer than 14 days and search them fast, local, encrypted, opt-in.

**Design (no Go change, no new signed bridge build).** The Core keeps its own archive, `core/src/jarves/whatsapp_archive.py`:
- Where: `<home>/whatsapp_bridge/archive/` (`store.enc`, `store.key`, `policy.json`). The folder is inside `whatsapp_bridge/`, which
  backups and the diagnostic bundle already exclude; the bundle inventory now also skips `whatsapp_bridge/archive/` by name.
- Encryption: an in-memory SQLite (FTS5), serialized and sealed only as `store.enc` with the SAME container as the bridge store
  (`whatsapp_store.SecureStore`: `JWS1` + nonce + AES-256-GCM; key wrapped by DPAPI on Windows, 0600 key file elsewhere, a copy on
  another PC/user raises `StoreLocked`). Never plaintext on disk. Rewritten at most every 5 s; purges run `VACUUM` + `secure_delete`
  so deleted text does not stay in free pages. A second process (the CLI) is followed through the file signature, so a CLI wipe is
  never undone by the running Core.
- Ingest: only through the bridge USER api the Core already uses: `/status`, `/chats` (the read scope and allow-list decide which chats
  exist), `/messages` per chat. Triggers: `message.new` event (that chat) and a ticker (all chats at start and every 5 min). Skips on
  mode off, kill, bridge "keep nothing" and a locked store. A chat that stops being readable is dropped from the archive at the next
  sync and filtered from every search at once (the live `/chats` list is the read gate). Edits and deletions (last 2 days re-read) update the index.
- Search: FTS5 `unicode61` over a folded copy: NFKD, marks (niqqud, accents) and bidi controls removed, case folded, punctuation and
  emoji dropped, Hebrew final letters unified, Hebrew prefix letters ו ה ב ל מ ש כ indexed as extra stems ("בכספת" finds "כספת" and
  the reverse; two-letter combinations ו+x, ש+x, כש). Prefix match for 3+ letters, all words must match, newest first, substring
  fallback when nothing matches. Filters: contact (chat), `--since/--until` (2026-09-01 or 30d/2w/3m/1y). 20,000 messages search in well under 0.5 s in the test.
- Retention: 14 days (default, unchanged), 90, `year` (365), `forever` (0) through ONE backend (`whatsapp_admin.set_retention`, used
  by the CLI and the dashboard). Up to 90 days it is the bridge's retention as before; more needs the archive switch (else
  `WA_ARCHIVE_OFF`, EN/HE) and then the bridge keeps 90 and the archive the chosen time. Shrinking purges rows and index at once.
- Wipes: "keep nothing", "delete messages" (all or older-than), unlink (CLI/dashboard) and the `logged_out` event wipe the archive; the
  owner's retention choice stays. Switch off = stop ingesting and stop archive search (the file stays until `archive wipe`).
- Model exposure unchanged: the `whatsapp_search` tool (now with optional `who`, `since`, `until`) is untrusted_output and taints as
  before; nothing new reaches a model automatically; local search uses no AI.

**Surfaces:** `jarvis whatsapp search "<words>" [--from c] [--since d] [--until d]`, `jarvis whatsapp archive on|off|status|sync|wipe`
(`on` prints the EN/HE privacy trade-off and asks), `jarvis whatsapp retention <days|year|forever>`; `status` shows the archive
(messages, MB). Dashboard WhatsApp page: "Days kept" is now 14/90/1 year/forever, a "How much is kept" card (messages, MB, honest
"limited history" note), an archive card (explanation, on/off with confirm, wipe, search box with contact/date). Connect/setup
finish with the retention note. Feature `whatsapp_archive` (default OFF, risk high, needs `whatsapp_bridge`; voice turn-on asks on screen with the trade-off). Guide `whatsapp_archive.toml`
(EN+HE), 22 dashboard strings via `catalog_add.py`, Hebrew feature texts in `hebrew.py`, CLI parity row, setup-wizard group (hidden from the wizard: explained and confirmed on its own).

**Tests:** `core/tests/test_whatsapp_archive.py` (32, offline, fake bridge): folding/stems, Hebrew+English search incl. prefix letters, niqqud, case,
punctuation, captions, edits/deletes, filters, order, 20k perf; ingest limited to readable chats and scope changes; skips (mode off, kill,
keep nothing, bridge down); retention for 14/90/365/forever; no deleted text in the database image; keep-nothing/delete/unlink/logged_out wipes;
switch off stops ingesting; no plaintext in any file under home (all encodings), `JWS1` container, copy to another key is locked, not in a
backup plan, not in or listed by the diag bundle, logs carry no text; CLI and dashboard flows; send feature untouched. Also green: the
other 8 `test_whatsapp_*` files, backup, connect, features, profiles, setup wizard, capabilities, diag bundle (194 + 132). `python core/tools/prepush.py` run via `uv` before push.

**NOT verified (no real WhatsApp, no Windows here):** DPAPI wrapping of the archive key (same code as the bridge store, tested only
on the file-key path on Linux); the real Go bridge's `/messages` paging and what real history sync delivers; behavior on a very large
real archive on a slow disk (whole-database re-seal every 5 s when dirty: fine for tens of MB, would need chunking beyond that); the
dashboard layout was syntax-checked and API-tested but not looked at in a browser; the Hebrew wording was written, not reviewed by a native reader.

**Known limits:** one `/messages` call returns at most the newest 500 messages per chat, so a first import of a busy chat is capped at 500 until
more arrive (the bridge only holds ≤90 days anyway). Two processes writing in the same instant could lose one write (rare; re-synced).
Messages deleted/edited on the phone more than 2 days after arrival are not re-read.

**Decisions for the owner:**
1. **Semantic search ("the message about the plumber") is NOT built.** It needs an embedding model: a new download of tens to hundreds of MB and
   CPU/RAM load on every message (or text leaving the PC to a cloud embedding API, which is excluded). Options: (a) skip, keyword + word-stem
   search is what ships; (b) later, an optional local multilingual embedding model (e.g. a small multilingual-e5 / similar, ONNX on CPU) behind a
   separate switch with an explicit "download ~100-500 MB" yes and vectors stored inside the same encrypted archive; re-check the best
   model at that time (AGENTS: newest best-in-class). Say if you want (b).
2. Archive OFF keeps the encrypted file (so re-enabling is instant); `archive wipe` deletes it. Prefer "off also wipes"? One-line change.
3. Anyone signed in to the Windows account can use JARVES to read the archive (as for the bridge store). Add a separate PIN/typed-code gate for
   archive search? Not done.
4. The dashboard Features page can switch the archive on without the typed explanation (the card text is on that page; the voice path asks on screen). Add a confirm there?
5. A `logged_out` event (unlinked on the phone) wipes the archive too. Alternative: keep it until the owner wipes. Chosen the stricter reading of "unlinking wipes it".
