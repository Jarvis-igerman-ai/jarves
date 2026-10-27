# WhatsApp message order (issue #17) — 23 Oct 2026

## Asked
Owner (real PC, Hebrew, read-only bridge): "it reads messages in the wrong order". No example given, so: investigate,
reproduce with tests, apply ONE convention everywhere.

## Findings (honest)
The Go bridge is correct (`msgstore.go`: newest `limit`, then `ORDER BY ts ASC, id ASC`; ts = Unix seconds). I could not
reproduce a literal mis-sort from the bridge. The Python side had these defects that make a conversation read wrongly:
1. `whatsapp_summarize_asked`: `limit 30` per person (a busy day lost the EARLIEST messages), and the 4000-char budget
   was spent oldest-first so the NEWEST messages were blanked.
2. Time labels were `HH:MM` only: a window over midnight/several days looked out of order (22:10 after 09:05).
3. `whatsapp_chat` spoke the last 3 of `limit` without saying it is a subset ("latest N of M").
4. Inbox spoke newest chat first and search returned newest first, neither stated; chats/messages read oldest first, also
   not stated, so the model could read any list as "latest first".
5. Python trusted the bridge order (no sort) and had no tie rule; fake bridge returned insertion order.
6. Dashboard messages view and `jarvis whatsapp inbox <contact>` showed `HH:MM` only; chat list order was the bridge's.

## Convention (applied in whatsapp_bridge.py, whatsapp_admin.py, dashboard whatsapp.js)
- Conversations (chat, summary, dashboard messages, CLI): OLDEST first, sorted by (ts, id), `when` label includes the date
  when the window spans >1 day or is not today ("yesterday 22:10", "Tue 09:05", "10 Sep 2026 09:05"; Hebrew `when_he`);
  `date` is always the ISO day. ms timestamps are scaled to seconds defensively.
- `whatsapp_chat`: fetches up to 500, shows the latest `limit`, `shown`/`total`/`total_is_minimum`, spoken "N האחרונות מתוך M".
- `whatsapp_summarize_asked`: limit 500 (the bridge cap), all messages kept, long texts shortened evenly (budget up to 12000),
  `earlier_not_shown` when 500 came back. NOT done: true paging; the bridge has no `until`/`before` parameter, so the
  earliest of >500 messages in a window cannot be fetched (would need a bridge/protocol change, Go: not touched).
- Inbox: most recent chat first, `order: most_recent_first`, spoken and rule say so. Search: newest first, dated, labeled.
- Tool descriptions, `BACKEND_RULE` and every result `rule` state the order. Fake bridge now sorts like the Go store.
- Guide `whatsapp_bridge.toml` (EN+HE) expect text updated; Hebrew UI strings via `catalog_add.py`.

## Verified
`core/tests/test_whatsapp_order.py` (14 tests: shuffled/newest-first input, ties, midnight dates, 120-message busy day,
over-cap notice, budget, inbox, search, descriptions, fake-bridge round trip, dashboard route) plus the existing WhatsApp,
locale, guide and parity tests. `python core/tools/prepush.py` run before push.

## Not verified / open
- Real bridge with real data on the owner's PC; DST edge (labels use local time of the epoch seconds, so no DST bug expected).
- If the owner still sees wrong order, ask for one example (which command, which chat): remaining suspects are the model's
  own re-ordering when summarizing, or a bridge ts problem on edited/history-synced messages (Go side, not changed).
