# Dashboard Home follow-ups (UX review of the navigation tree) — 23 Oct 2026

Follow-up to the navigation tree (PR #78, beta 4.0.1-beta.12). Owner: Python dashboard (static UI only, no API change).

## What changed
- Setup checklist on Home: progress bar, only the next open step is spelled out, the rest are one line each.
- Home tiles are links (Connected -> Connections, Today's cost -> Costs, Conversation issues -> Issues).
- The top-bar chip and the Home tile counted different things under similar names. The chip counts the bug-report registry and is now "Bug reports: N"; the Home tile counts issues found in conversations and is now "Conversation issues". Same data, two honest names.
- Approvals count badge in the sidebar (updates every 2 s from the existing /api/approvals poll).

## Verified
- `python core/tools/prepush.py`: all eight checks passed. Dashboard, locale, guides, CLI-parity and recall tests pass locally. A Home screenshot (light, English) was checked by eye.
- The chat-engine hang that blocked this PR's CI was a real race, fixed separately in PR #95 (busy flag cleared together with the reply/notice event, new regression test).

## NOT verified
- Hebrew/RTL and dark mode on a real screen (only the locale tests cover them).
- Browser accessibility tests run only in CI (the sandbox cannot launch them).

## Still open (owner decision on order)
- Settings page: sticky save bar and an "Advanced" toggle that hides the raw key names.
- Merge the three overlapping health pages (Health, Health & speed, Self-check) into one tabbed page.
