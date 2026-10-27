# JARVES 4.5 (v4.0.1) release notes

4.5 is the product name of this release; v4.0.1 is the technical version used by installers, updates and tags.

Status (27 Oct 2026): stable, tag `v4.0.1`. Built from the tested beta 4.0.1-beta.195 (installer
4.0.1.1214, commit `313425ae`); only documentation changed on `main` since then. Approved by the owner in chat on 27 Oct 2026 (06:36 UTC) and signed off
(sheet `docs/reviews/MANUAL_TEST_4.0.1.md`; rows S1-S5 accepted by that approval, not tested by hand); published by the tag `v4.0.1` ([RELEASING](RELEASING.md), [evidence](release/EVIDENCE_4.0.1.md)). This replaces 4.0.0
(22 Oct 2026). Short per-change list: [CHANGELOG](../CHANGELOG.md) (section 4.0.1). Previous notes: [RELEASE_NOTES_4.0](RELEASE_NOTES_4.0.md).

Features marked **beta** are off by default and only visible when you turn on the global "beta features" switch (Features page
or the setup wizard section Beta). Their stage label is shown in `jarvis features list`.

## New since 4.0.0

| Area | What you get |
|---|---|
| Updates | Update channels (`jarvis update --channel beta|stable`, Updates page), progress with stages and percent, health check after an in-app update with automatic rollback, free-disk check before anything is stopped, only what was running is restarted. |
| Hot fixes | `jarvis hotfix`: a code-only fix in about a minute, with backup, dry run, revert (`jarvis hotfix revert`) and a recovery entry when Core cannot start. |
| Release notes and status | Release notes page and `jarvis release-notes`; `jarvis whatsnew` (short, users only); `jarvis about`, `jarvis roadmap`, `jarvis --version` shows BETA on beta builds. |
| Setup and tour | One wizard for every topic (`#/setup`, `jarvis tour`): one card at a time, Works / Did not work / Later, a Failed list that comes back when the bug is fixed. |
| Approvals | One approval popup (the tray toast only as a fallback, with a JARVES identity); goal-plan and plan-step approvals only on the computer; bidi characters escaped in popups. |
| Voice and wake | Wake greeting and hidden-mode indicator, idle stop, audio route / device-change handling, `jarvis wake record` (guided recorder that picks a working mic), wake training with your own recordings and a stricter apply guard. |
| Self-awareness and issues | Issues registry (`jarvis issues`) with honest, explained counters; self-diagnosis on failure asks first and is read-only; dashboard Start and Stop buttons, mic mode menu. |
| Phone (paired, on your home network) | Redesigned phone web app (chat-style), learned preferences, goal plans with pause/cancel, mic indicator, pairing fixes. |
| Integrations | Any.do (official MCP, off by default), WhatsApp: one person = one chat, messages shown in the chat (read-only); Camera page. |
| Drive (beta) | Drive mode policy and queue, drive briefing, journey planning, phone Drive tab, hands-free Drive voice: all off by default. |
| Skills (beta) | Skills manifest v2 with local trust, update and rollback, revoke races fixed. |
| Coding agents and GitHub (beta) | Software project manager (`jarvis project ...`), read-only GitHub client, GitHub writes class 1 (issues, comments, labels) and class 2 (PRs, reviews, CI re-run, CLI only), each behind an approval popup. |
| V7 pilots (beta) | Model registry and spend policy (free-only by default, no hidden paid fallback), OpenRouter adapter, Model Lab page, browser pilot session, OpenCode adapter (read-only), Companion API v1 (read-only). |
| Security and privacy | Phone and LAN devices cannot read conversations, plans or agents; drive-mode origin allow-list; e-mails masked in health text; scheduled security scans (`jarvis scans`); confined git for project agents. |
| Speed | Faster dashboard start (serves before page modules load, cached slow APIs), CLI loads only the needed command, faster setup wizard. |

## Upgrade notes

- From 4.0.0: accept the in-app update or run `jarvis update`. A backup is taken first; if the new version does not come up
  healthy it rolls back by itself. Go back by hand with `jarvis update --rollback`.
- Beta users: `jarvis update --channel stable` returns you to the stable channel. Beta-only features stay off unless the
  "beta features" switch is on.
- The update source is `Jarvis-igerman-ai/jarves`; signed installers stay in `igalgerman/jarvis-releases`.
- The installer is not code-signed (owner decision); the update manifest is Ed25519-signed and checked before install.

## Behaviour changes

- `jarvis whatsnew` shows at most 8 user-facing lines; developer entries are hidden (`jarvis release-notes` has everything).
- Goal-plan and plan-step approvals can only be answered on the computer; the phone shows the title and Deny only.
- An approval shows ONE popup; the tray notification is only the fallback when the window cannot open or the PC is locked.
- The "Bug reports" counter is red only for new unresolved P1/P2 local tickets; nothing is sent anywhere.

## Known issues

- #179 voice start: the greeting audio sometimes never arrives (about 1 in 5 starts); JARVES continues without it.
- #261 installer: no automatic recovery if the new program is dead after a real database schema change (backup is kept).
- #509, #510 phone web app: looks poor on some phones and cannot be installed as a home-screen app (PWA) yet.
- #441 one test is flaky in CI (no user impact; under watch).
- Release hardening, accepted as known issues by the owner on 27 Oct 2026 (owner settings and a workflow change come in 4.5.1): #543 the sign-off sheet
  is not bound to the owner, #544 release secrets are not in a protected environment, #605 the sign-off hash is not
  authentic, #606 sign-off owner and cell hardening, #598 a tag-ref dispatch may use a different tag input.
- Wake training follow-ups (#594, #595, #596, #618, #619); the custom wake head ignores about the first 2 s after start.
- Phone plans page escaping of zero-width characters (#607); hotfix restart reported as a crash by the self-check (#620);
  beta build numbers out of order (#621); CodeQL alerts #46 and #47 (`skills/generated.py`).
- A long silence closes the voice conversation (doctor WARN `voice.idle_disconnect_s`); say the wake word again.

Follow-ups for 4.5.1 are tracked in #505.
