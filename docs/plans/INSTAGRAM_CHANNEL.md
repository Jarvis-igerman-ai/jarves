# Instagram channel (#17)

Status: research + design, 2026-10-20. Meta docs change often; verify the permission list in App Review.

## What the official API allows
- The Instagram Messaging API works only for Instagram Professional accounts (Business or Creator). Personal accounts cannot use it; reading arbitrary personal DMs is not possible.
- Needs a Meta developer app with the "Manage messaging and content on Instagram" use case; permissions `instagram_basic`, `instagram_manage_messages`, `pages_manage_metadata` (account linked to a Facebook Page, or the Instagram Login flow).
- Inbound: webhooks (`messages` field) to a public HTTPS endpoint; the app must be published/live to receive webhooks. Polling conversations via Graph API is a secondary option.
- Outbound: Send API, only to users who messaged first, within 24h of their last message. A Human Agent tag extends this to 7 days for manual human replies (may need separate App Review; automated sends must not use it). One-Time Notifications are not available on Instagram.
- App Review: required for accounts of people without a role on the app. If the IG account belongs to a user with a role (admin/developer/tester) on the app, the owner's own account can be used without public review. This is the viable path for JARVIS (single owner).
- Policy: promotional spam and cold outreach are disallowed; replies must respond to the user's message.
- Not possible officially: scraping, personal-account DMs. Unofficial libraries (instagrapi etc.) risk bans; do not use.

## Design: `jarvis instagram start`
Goal: owner's Business/Creator account; JARVIS reads incoming DMs, drafts replies, sends only after explicit confirmation.

1. Webhook receiver: small HTTPS endpoint exposed through a tunnel (Cloudflare Tunnel; no inbound ports). Verifies `hub.verify_token` and `X-Hub-Signature-256` with the app secret.
2. Inbox store: SQLite (message id, sender igsid, text, ts, window_expires_at, status); dedupe by message id.
3. Drafting: LLM proposes a reply from the message and recent thread. Never auto-sends.
4. Confirmation gate: every draft is presented (voice: "Anna asked X, reply Y? yes/no/edit"; dashboard or Telegram button as alternatives). Send API is called only on explicit approval of that exact text; edits need re-confirmation; no batch approve; timeout discards.
5. Window guard: refuse to send if now > last user message + 24h; tell the owner and offer a manual Human Agent reply.
6. Audit log of every draft, decision and send result.
7. Secrets: token and app secret in the existing secret store, never in the repo; refresh the long-lived token before its 60-day expiry.

CLI: `jarvis instagram setup` (guided token + webhook subscribe), `jarvis instagram start` (receiver + confirmation loop), `status`, `stop`.

Risks: token expiry, Meta policy changes, tunnel availability, prompt injection via DM text (treat DMs as untrusted data; the LLM only drafts text, the only action is send-after-confirm).

## Open questions for owner
- Is the account Business/Creator and linked to a Facebook Page?
- Preferred confirmation surface: voice, dashboard, or Telegram?

## Sources
- https://developers.facebook.com/docs/instagram-messaging/overview
- https://developers.facebook.com/docs/instagram-messaging/webhooks
- https://developers.facebook.com/docs/messenger-platform/policy/policy-overview
