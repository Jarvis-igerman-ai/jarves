# JAR-290 plan: WhatsApp long history backfill, part 2 (Core)

Status: BLOCKED. This depends on part 1 (JAR-272, PR #1091: the bridge endpoint `GET/POST/DELETE /history/backfill`),
which is still under review and has not merged. The Core client must follow the merged endpoint contract.

Plan once #1091 merges (base: the new main):
1. Core client: start the backfill (`POST`), drain with `GET ?cursor=N` until `state` is done, stopped or failed,
   and write each message into the encrypted archive. Progress comes from the endpoint's own counts; stopping
   calls `DELETE`. The read scope, allow-list, kill switch and locked store come from the bridge's error codes.
2. CLI: `jarvis whatsapp archive backfill [--from CONTACT] [--since DATE]` (default: all history), with progress
   and Ctrl+C to stop.
3. Dashboard WhatsApp page button, a guide (EN and HE), a CLI parity entry, Hebrew strings.
4. Hermetic tests with a fake bridge. No message text in logs, tests or PRs.

Expected size is above the 200-line budget; it may be split (client and CLI, then dashboard and guide).

## Status, 20 Nov 2026

Still blocked. PR #1091 and PR #1215 (JAR-272, the bridge endpoint) were both
closed without merging on 16 Nov 2026. `origin/main` (df7606a) has no
`/history/backfill` route. JAR-272 has to be re-landed before this Core work can start.
