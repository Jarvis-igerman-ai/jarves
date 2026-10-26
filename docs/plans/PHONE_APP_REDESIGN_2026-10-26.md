# Phone app redesign: ChatGPT-like, fully functional (26 Oct 2026)

Issue: #511 (P2, enhancement, phone). Owner order 26 Oct 2026: make the phone app fully functional and beautiful,
ChatGPT-like, and test it on the connected test phone.

Scope: the phone web app (PWA) under `core/src/jarves/dashboard_static/mobile/` served by Core at `/m/`, plus the shared
chat view `dashboard_static/chatview.js|css`. **Only the existing phone API** (`/api/m/*`) is used: no new routes, no new
write capabilities, no prompt additions. The PWA-install fix (icons, manifest, install help) and the concrete broken
controls / RTL bugs are a separate slice (worktree `phone-ui`); this redesign does not duplicate them and keeps its code in
separate files (`m_theme.css`, `m_shell.js`) so the two merge cleanly.

## 1. Information architecture

Chat is the home screen. Everything else lives in a side drawer, like ChatGPT.

```
+--------------------------------------+
| [=]  JARVES  (o connected)   [+] [HE] |   top app bar: menu, title + connection dot, New chat, language
|--------------------------------------|
| (offline / audio / note banners)     |
|                                      |
|   assistant bubble (markdown, code)  |
|                 user bubble          |
|   ui_block card (checklist / cards)  |
|   approval card (Approve / Deny)     |
|                                      |
|--------------------------------------|
| /new /resume /status ... (chips)     |
| [+] [ Message JARVES...     ] [mic][>]|   bottom composer, safe-area aware
+--------------------------------------+
```

Drawer (opens from the inline-start edge; RTL: from the right):
1. **New chat** (sends `/new`).
2. **Recent conversations**: filled from the `/resume` command's answer (`sessions` list in the notice event);
   a tap sends `/resume N`. No new API: the same list the terminal chat shows.
3. Pages, shown only when the device has the page/scope (the server refuses the rest anyway):
   Chat, Approvals (badge with the waiting count), Plans (`tasks`), Learned (`prefs`), Drive (`drive`), Voice
   (big push-to-talk, hands-free friendly), Health and speed, This phone (pairing info, install help, un-pair).
4. Footer: language switch, connection state.

Approvals also appear inline in the chat (as today, from the chat events), so the owner rarely needs the page.

## 2. Chat (ChatGPT-like)

| Element | Behaviour | Source |
|---|---|---|
| Bubbles | user: accent bubble at the inline end; assistant: full-width plain text block (no bubble), like ChatGPT | chatview.js |
| Markdown | paragraphs, headings, lists, quotes, inline code, bold/italic, links (http/https only), all via DOM calls | chatview.js (existing) |
| Code blocks | LTR, monospace, header with language + Copy, horizontal scroll inside the block only | chatview.js (existing) |
| Streaming | `delta` events update the open answer; caret while streaming | chatview.js (existing) |
| Thinking state | three-dot "thinking" row from the moment a message is sent until the first delta or the reply | new, client only |
| Composer | auto-growing textarea, Send (arrow icon, 44x44), push-to-talk mic (hold), attachments `+` | m_shell.js + chatview hook |
| Push-to-talk | hold the mic in the composer: records 16 kHz WAV, `POST /api/m/voice` (HTTPS only), transcript + reply shown as a local voice exchange in the log | existing `/api/m/voice` |
| Attachments | placeholder: tapping explains "not available on the phone yet" (no upload API exists) | client only |
| Copy | each finished answer gets a Copy button (clipboard API) | client only |
| Regenerate | **not offered**: the phone API has no regenerate call; adding one would be a new capability | n/a |
| ui_blocks v1 | `text`, `checklist`, `cards` render as cards in the shared view. **The server never sends them to a phone** (#363: they carry owner-only text from tool results, `chat_engine.state/poll` filter `display` and `ui_block` for devices), and `ui_action` is PC-only (SMART_CHAT §5). The phone therefore shows tasks through the Plans page and approval cards; the chat view still guards cards as read-only on a device in case the server rule ever changes. Changing #363 is an owner decision, not part of this redesign. | chatview.js |
| Errors | a failed send shows a per-message error under the user's text with **Retry** (same message id: answered once) | chatview.js |
| Empty state | greeting + 3 suggestion chips (/status, /resume, /help) | chatview.js |
| Jump to latest | round button when scrolled up | chatview.js (existing) |

The dashboard Chat page uses the same chatview.js; new behaviour is opt-in through `mount` options (`device`, `composer`,
`onEvent`), so the desktop page does not change unless it asks for it.

## 3. Design tokens

All in `m_theme.css` on `:root`, light by default with a dark set under `prefers-color-scheme: dark`.

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#ffffff` | `#0f1115` | page |
| `--surface` | `#f4f5f7` | `#1a1d23` | cards, assistant blocks, composer |
| `--surface-2` | `#e9ebef` | `#252932` | chips, pressed |
| `--fg` / `--text` | `#14161a` | `#eceef2` | body text (>= 14:1) |
| `--muted` / `--text-2` | `#555b66` | `#a3aab6` | secondary text (>= 4.5:1 on bg and surface) |
| `--line` | `#d5d8de` | `#30353f` | hairlines |
| `--accent` | `#1553cc` | `#4f8cff` | primary buttons, user bubble (white text >= 4.5:1) |
| `--ok` / `--bad` | `#0a7a4b` / `#b3122a` | `#3ecf8e` / `#ff6b78` | status |

- Spacing scale (4 px base): 4, 8, 12, 16, 24, 32. Radius: 8 (chips), 12 (cards), 20 (bubbles, composer).
- Type scale: 13 (meta), 15 (secondary), 16 (body, also stops iOS zoom on inputs), 18 (title), line-height 1.55.
  Font: `system-ui, "Segoe UI", Roboto, "Noto Sans Hebrew", Arial, sans-serif`.
- Hebrew first: `dir` follows the language; every rule uses logical properties (`margin-inline-start`, `inset-inline-*`,
  `padding-block`, `border-start-*-radius`). Mixed text: `dir="auto"` + `unicode-bidi: plaintext` per paragraph; code is
  always `dir="ltr"`.
- Safe areas: `viewport-fit=cover`; the bar pads `env(safe-area-inset-top)`, the composer `env(safe-area-inset-bottom)`.
- Tap targets: every control >= 44x44 px (the 390 px audit enforces it).
- Motion: drawer slide and dot pulse only under `prefers-reduced-motion: no-preference`.
- Contrast: every text pair >= 4.5:1 (audit rule `color-contrast`).

## 4. Connection and offline

- The top bar dot + label: connected / computer unreachable (from the existing `api()` wrapper and `/api/m/sync`).
- A banner under the bar when the computer is unreachable; the composer stays usable and the chat view already retries a
  send 3 times with the same id, then shows the per-message Retry.
- The service worker caches the app shell only, never API answers (unchanged rule). New shell files are added to its list.
- When `GET /api/m/companion/info` exists (PR #495) the drawer footer can show the Core version and PC state; until then
  nothing calls it.

## 5. Performance budget

- No framework, no build step, no external request (CSP). Added shell JS + CSS <= 25 KB uncompressed.
- First paint of the shell from the service-worker cache < 1 s on the test phone; chat state visible < 2 s on Wi-Fi.
- One long-poll for chat events (unchanged) + the existing 4 s tick; the drawer history is fetched only when opened.
- No layout shift when the keyboard opens: the app uses `100dvh` and a flex column (log scrolls, composer stays).

## 6. Security and privacy

- Text only via `textContent` / DOM calls, never `innerHTML` (existing test enforces it).
- No token in URLs; the token stays in localStorage on the phone (unchanged).
- Phone approvals keep their rules (OFF by default, typed/pc_only never from a phone; deny always allowed).
- ui_block actions stay PC-only; the phone shows them read-only.
- Screenshots in the repo use a test persona on a temporary Core home (fake model), never the owner's data.

## 7. Testing

- Browser (Playwright, existing `test_mobile_phone_e2e.py` harness): 390x844, EN + HE, light + dark; the existing
  layout audit (no sideways scroll, 44 px targets, contrast, labels) runs on chat, drawer open, and each page.
- `core/tools/phone_redesign_screens.py` writes the screenshots to `docs/reviews/PHONE_REDESIGN_SCREENSHOTS/`.
- Real phone (test device, adb over USB): a temporary test-persona Core is reached through `adb reverse` on localhost
  (a secure context, no certificate warning, no firewall change); screenshots of every screen are compared with this doc.

## 8. Slices

1. **Design system + chat screen** (this branch): `m_theme.css`, `m_shell.js` (top bar, drawer, composer mic, thinking,
   copy, retry, history), chatview hooks, chat as home, test updates, screenshots.
2. **History / tasks / approvals polish**: approvals page as cards, plans/learned/health restyled with the tokens, drawer
   history with dates, pull-to-refresh.
3. Later: companion info in the drawer (after #495), attachments when an upload API exists (needs its own design).

## 9. Owner decisions (safe default first)

- Assistant answers as plain full-width text (ChatGPT style) instead of grey bubbles. Default: yes.
- Voice tab kept in the drawer (big button) besides the composer mic. Default: keep.
- Show smart-chat task cards (ui_blocks) on the phone, read-only? Needs a change to the #363 rule (owner-only text on a
  phone). Default: no, keep #363; tasks are seen on the Plans page.

## 10. Status (26 Oct 2026, slice 1)

- Done on branch `phone-redesign`: tokens (`m_theme.css`), app bar + drawer + history + composer mic + thinking + copy +
  per-message retry (`m_shell.js`, chatview hooks), chat as home, tests moved to the drawer (`go()` helper), guide step
  (mobile_access), Hebrew catalog part, DOC_MAP row. Local: 42 phone e2e + 259 related tests pass, prepush green.
- Screenshots: `docs/reviews/PHONE_REDESIGN_SCREENSHOTS/browser/` (390x844, EN/HE, light/dark, regenerated after the last
  CSS change) and `.../real-phone/` (test device over `adb reverse`, test persona, taken before the final compact-strip /
  composer-focus CSS tweak).
- Open: a re-shoot on the real phone after the final CSS (the PC's adb server from 22 Oct hangs and holds the device;
  it needs a restart by the owner or a permitted agent). Slice 2 (approvals/plans/learned/health as cards, dated history)
  follows. `phone-ui` (#510 PWA icons) merges independently.
