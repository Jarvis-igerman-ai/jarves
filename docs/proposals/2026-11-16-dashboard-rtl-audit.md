# Dashboard RTL layout audit (JAR-122), proposal only

Read-only review of `core/src/jarves/dashboard_static` (CSS, JS, `index.html`, `mobile/`) for right-to-left
problems: physical left/right, unmirrored arrows, and bidi in numbers and ids. No dashboard code was changed.
Nothing here is authorised work; each fix needs its own ticket. Paths below are relative to
`core/src/jarves/dashboard_static/`, and line numbers are from `origin/main` on 16 Nov 2026.

## Method

1. Grep every CSS file and inline JS style for physical properties: `margin-left/right`,
   `padding-left/right`, `border-left/right`, bare `left:`/`right:`, `text-align: left/right` and `float`.
2. Grep for arrow glyphs (`← → ‹ › ▶ ◀`, `translateX`, `scaleX`), then read `i18n.js` to see how the catalog layer
   handles arrows and English text in a Hebrew page.
3. A runtime check of finding 1 and 2 in headless Chromium (Playwright). The page loads only `locales/en.js`,
   `locales/he.js` and `i18n.js` with `#/x?lang=he`, runs `JL.translate()` on the same strings the pages build,
   and measures which number is drawn on the left (Range rects). The harness is not committed; the script and
   its output are in the PR body.

## What is already right

- No CSS file uses `margin-left/right`, `padding-left/right`, `float` or `left:`/`right:`. Layout uses logical
  properties (`margin-inline`, `inset-inline-start`, `padding-inline-start`, `text-align: start`), for example
  `shell.css:7`, `shell.css:41`, `chatview.css:21`, `guides.css:11`.
- The only physical values are deliberate RTL overrides: `shell.css:62-63` (fade masks), `shell.css:99` (numbers
  align left), `shell.css:101` (key/value column).
- Code, URLs, numbers, chips and charts are LTR islands (`shell.css:96-97`, `shell.css:102`).
- The phone drawer slide-in is mirrored (`mobile/m_theme.css:166-174`), the chat jump button too
  (`mobile/m_theme.css:285-287`), and the strip menu places itself from the right edge in RTL (`strip.js:102`).
- `home.js:136` already wraps a `from → to` version pair in `dir: "ltr"`: the pattern that findings 1 and 2 need.

## Findings, ranked

| # | Severity | Where | Problem |
|---|---|---|---|
| 1 | P2 | `i18n.js:53`, `i18n.js:74`, `i18n.js:101`; strings at `health.js:164`, `context.js:129`, `context.js:236` | "A → B" data arrows point the wrong way in Hebrew |
| 2 | P2 | `health.js:88` | A numbers-only "A → B" has no LTR island and is drawn reversed |
| 3 | P3 | `guides.js:114`, `agents.js:121`, `agents.js:132` | Hebrew "back" links use the forward arrow |
| 4 | P3 | `app.js:51` | Hover tooltip is placed with physical `left`, not mirrored |
| 5 | P3 | `mobile/m_theme.css:154`, `mobile/m_theme.css:156` | Loading shimmer sweeps left to right in RTL |
| 6 | Info | `shell.css:98` | URL, e-mail and number inputs align right in Hebrew (`end` inside an `ltr` box) |

### 1. Data arrows are flipped, then drawn LTR (P2)

`tr()` mirrors every `←`/`→` in English text that has no catalog entry (`i18n.js:53`, applied at `i18n.js:74`).
That suits navigation text ("next →"). But `trNode()` then wraps the same string in an LTR isolate
(`i18n.js:101`), so a data arrow between two values ends up pointing backwards. Runtime result with
`lang=he`:

- `health.js:164` builds `0.9.1 → 1.0.0 (abc123) · 6 h`. It becomes `⁨0.9.1 ← 1.0.0 …⁩` and is drawn
  `0.9.1 ← 1.0.0`, so it reads as "from 1.0.0 to 0.9.1".
- `context.js:129` builds `1200 → 800 characters`. It is drawn `1200 ← 800 characters`: reversed in the same way.
- `context.js:236` (`voice X → backend Y`) goes through the same path (seen in the code, not measured).

Chips are skipped (`i18n.js:76` SKIP has `.chip`), so the `model → backend` chips at `app.js:144` and
`pages.js:116` are not affected.

Proposed fix (smallest): put each value pair in an LTR island the catalog skips, as `home.js:136` does
(`dir: "ltr"` or `class: "mono"`), at the three call sites. A broader alternative: mirror only a leading or
trailing arrow in `flip()` (`/^[←→]\s|\s[←→]$/`), never one between two values. Test: a
`test_dashboard_locales`-style check, or a Playwright case like the one in the PR body, asserting the earlier
value is drawn on the left.

### 2. A numbers-only arrow is not isolated (P2)

`health.js:88` builds `${first} → ${last}` from two scores. It has no Latin letter, so `trNode()` leaves it alone
(`i18n.js:97`), and it inherits the page's RTL direction. Runtime result: `70 → 85` is drawn `85 → 70`, so it reads
as "85 went down to 70". Fix: `dir: "ltr"` on that span, as at `home.js:136`.

### 3. Hebrew back links use the forward arrow (P3)

The catalog's rule is that "back" points right in Hebrew: `he.js` maps `← all conversations` to `→ כל השיחות` and
`← transcript` to `→ תמליל`, while "open/go" uses `←` (`פתיחה ←`, `home.js:90`). These inline `tx()`/`L()`
strings break the rule:

- `guides.js:114` `כל המדריכים ←` ("all guides", a back link)
- `agents.js:121` and `agents.js:132` `כל הסשנים ←` ("all sessions", a back link)

In Hebrew, back and forward links look the same. Fix: change the Hebrew text to `→ כל המדריכים` and
`→ כל הסשנים` (dashboard texts via `core/tools/catalog_add.py` where the string is a catalog key).

### 4. Hover tooltip is placed with physical left (P3)

`app.js:51` sets `tip.style.left = clientX + 14` (clamped to the window). In RTL the tooltip still opens to the
right of the pointer, away from the reading direction. Fix: mirror it the way `strip.js:102` does
(`rtl ? clientX - 14 - tip.offsetWidth : clientX + 14`).

### 5. The shimmer is not mirrored on the phone (P3, cosmetic)

`mobile/m_theme.css:154` and `mobile/m_theme.css:156` animate `translateX(-100%) → translateX(100%)` with no
`[dir="rtl"]` variant, unlike the drawer next to it (`mobile/m_theme.css:167-174`). Fix: add an RTL keyframe
the same way.

### 6. Input alignment needs an owner decision (Info)

`shell.css:96-97` makes URL, e-mail and number inputs `direction: ltr`, and `shell.css:98` then sets
`text-align: end`, which is the right side inside an LTR box. That may be deliberate (lining up with the Hebrew
form column). No change is proposed without an owner decision; recorded so that it is not "fixed" by accident.

## Out of scope (not checked)

- Real Hebrew screen renders of every page; only the strings above were measured (UNVERIFIED elsewhere).
- Server-built texts (`*_he` fields) and the Godot face UI.
- Arabic or other RTL locales: none ship today; the rules key on `html[dir="rtl"]`, so they would get the same results.
