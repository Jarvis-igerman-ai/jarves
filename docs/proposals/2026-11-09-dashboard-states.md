# Dashboard empty / loading / error states review (EN + HE)

Date: 2026-11-09. Task: JAR-136. Status: proposal only, no code changed. Source read at `origin/main` fb3cc523.

## How far this review goes

- **Read:** `core/src/jarves/dashboard_static/*.js` (router, `api()`, `fail()`, per-page empty and catch branches).
- **Not run:** no page was rendered, so there are **no screenshots**. Evidence is a selector or `file:line`. Whether a state is really seen by the owner is UNVERIFIED until rendered with the Core stopped and with a slow API.
- Real phone, screen reader, car and ear checks: UNVERIFIED.

Confidence: **source** = confirmed in the current code.

## Findings (ranked)

| # | Page / selector | State | Problem (source) | Cost |
|---|---|---|---|---|
| 1 | Every routed page, `#main > .card.empty` from `fail()` (`app.js:44`) | error | Text is English only ("Could not load: ...") in a Hebrew UI, and shows the raw `e.message`. | S |
| 2 | `api()` (`app.js:22-28`) | error | When the Core is down `fetch` throws the browser text ("Failed to fetch" / "NetworkError"); that reaches the owner through #1. A 401/403 shows the server `error` text raw. No retry button. | S |
| 3 | `route()` (`app.js:94`, `app.js:520`) | loading | Between click and API reply the old page stays, or `#main` is empty. Only 4 pages draw a placeholder (`app.js:354`, `pages.js:415`, `m3.js:136`, `approvals.js:121`). No spinner-forever, but also no sign that work is happening. | M |
| 4 | Background refresh `route(true)` (`app.js:520`, `approvals.js:72`, `context.js:278`) | error | Failed refresh is swallowed (`catch { return; }`). If the Core dies the page stays frozen on old data, with no "stale" note. | S |
| 5 | `delegation.js:41` | error | `box` shows raw `e.message` as an `.empty` block. | S |
| 6 | `usage.js:46` | error | "Usage: not available" is English only and gives no reason or next step. | S |
| 7 | `app.js:124, 247, 316, 329, 345, 347, 357, 368` (Conversations, Costs, Issues, Memory, Skills, Workspace) | empty | English only; Hebrew UI shows English. Other pages (`m3.js`, `approvals.js`) already use `tx()`. | S |
| 8 | `m2.js:95-96`, `m4.js:229, 266, 268`, `context.js:77, 117, 145, 202, 221`, `browser.js:174`, `pages.js:96, 263, 303` | empty | Same: English only. `m2.js:95` also embeds a Hebrew example inside English text. | S |
| 9 | `m2.js:23, 40`, `app.js:365, 379`, `m4.js:113`, `context.js:27, 213` | error | "Not saved / Not done / Copy failed / Export failed" English only; `e.message` raw. | S |
| 10 | `agents.js:30, 84`, `browser.js:31-80`, `dialing.js:17`, `guides.js:73-97`, `home.js:146-224` | error | `msg.textContent = e.message` with no prefix: raw server or browser text, no context what failed. | S-M |
| 11 | `agents.js:54`, `dialing.js:22` | empty (feature off) | Ends with `d.reason` raw. If `reason` is empty the sentence ends with a colon and nothing. | S |
| 12 | `agents.js:147` | empty | A bare "—" with no explanation. | S |
| 13 | `bugs.js:58` | error | "Could not load" popup text: no cause, no retry. | S |

## Proposed copy

One shared set (add via `core/tools/catalog_add.py`, use `tx()`):

| Key | EN | HE |
|---|---|---|
| load_failed | Could not load this page. Check that JARVES is running, then try again. | לא הצלחנו לטעון את הדף. בדקו ש-JARVES פועל ונסו שוב. |
| core_down | JARVES is not answering. | JARVES לא עונה כרגע. |
| session_ended | Your session ended. Open the dashboard again from JARVES. | ההפעלה הסתיימה. פתחו את הלוח מחדש מתוך JARVES. |
| retry | Try again | נסו שוב |
| loading | Loading... | טוען... |
| stale | Showing the last data. Could not refresh. | מוצגים הנתונים האחרונים. הרענון נכשל. |
| not_saved | Not saved. Nothing was changed. | לא נשמר. שום דבר לא השתנה. |
| copy_failed | Copy failed. Select the text and copy by hand. | ההעתקה נכשלה. סמנו את הטקסט והעתיקו ידנית. |
| feature_off | This feature is off. Turn it on in Settings. | התכונה כבויה. אפשר להפעיל אותה בהגדרות. |
| empty_conv | No conversations yet. They appear when you talk to JARVES. | עוד אין שיחות. הן יופיעו אחרי שתדברו עם JARVES. |
| empty_costs | No costs recorded yet. | עוד לא נרשמו עלויות. |
| empty_issues | No open issues. | אין תקלות פתוחות. |
| empty_memory | Nothing remembered yet. | עוד אין זיכרונות. |
| empty_skills | No skills yet. They are listed when JARVES starts. | עוד אין כישורים. הם יופיעו כש-JARVES יעלה. |

Raw `e.message` stays available as a small details line (`dir="auto"`) under the friendly sentence, for support.

## Suggested fix order (separate PRs, not part of this one)

1. **Shared helpers (findings 1, 2, 4):** one `fail()` with `tx()` copy, a retry button, a friendly network/401 mapping inside `api()`, and a "stale" note on swallowed refresh. Tier N. Test: node/jsdom or string-level check that `fail` has HE text; hermetic.
2. **Loading placeholder in `route()` (3):** one `Loading...` card, cleared by `render()`.
3. **Translate empty and error strings (5-13):** per file, with `catalog_add.py` and the guides check.

Guides rule applies to those code PRs (`test_guides`, `test_cli_parity`); this doc PR adds no feature, so no guides entry.

## Not covered / UNVERIFIED

- Pages generated from `pages.d/*.toml` and `home.js` cards were only scanned for the same patterns.
- Real rendering, RTL layout of the proposed copy, and keyboard / screen reader behaviour of the retry button.
