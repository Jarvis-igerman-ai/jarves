# Setup wizard review: Hebrew/RTL, accessibility, clutter (JAR-117)

Date: 2026-11-16. Task: JAR-117 (Track 3, UI/UX round 2). Status: **proposal only, no code changed.**
Source read at `origin/main` b5dc54f. Page: `#/setup` (`dashboard_static/wizard.js`, `wizard.css`, server `wizard.py`),
the #149/#152 design: one short card per step, long text behind "More", EN + HE.

## How far this review goes

- **Rendered fresh:** the demo dashboard (`core/tools/all_pages_check.start_dashboard`, throw-away home, seeded demo
  data with 2 waiting approvals), headless Chromium, EN and HE, 1024 and 390 px wide, 860 px high, reduced motion.
  States: first card, "Did not work" open, "More" open. Wizard state reset before each run.
- **Checks run on that page:** the repo's own rules `core/tools/all_pages_rules.js` (blocking set, plus the JAR-79
  report-only `clipping` set) and DOM probes (button names, `aria-*`, focus, sizes, positions).
- **Not covered:** a real phone, iOS/Android browsers, a screen reader, dark theme, the Done/Failed lists with real
  history, and every topic after the first (only "Voice and wake word" was opened). These are **UNVERIFIED**.
- **Excluded on purpose:** findings already in PR #988 (`2026-11-09-ui-review.md`), the phone fixes of PR #1001 and the
  clipping rules/hits of PR #1002 (`docs/reports/2026-11-09-clipping-hits.md`). The clipped privacy banner showed up
  here too; it is hit 1 of that report and is not repeated.

Confidence: **render** = seen in this run's screenshots/probes; **source** = confirmed in current code.

## What is already good

- `lang`/`dir` switch correctly (`he`/`rtl`); answers mirror (Works is at the inline start in both languages).
- No sideways scroll at 390 px; the three answers are 56-62 px high; a 2 px focus outline on Works.
- Blocking rules: **0 issues** in all four runs (EN/HE x 1024/390). No page errors.
- "More" loads only when opened and announces through `aria-live`; the failure note says nothing is sent.

## Ranked findings

Cost: S = under half a day, M = 1-2 days. Tier follows the factory rules.

| # | Finding | Lang | Confidence | Benefit | Cost | Tier |
|---|---|---|---|---|---|---|
| 1 | Focus starts on a Works button that is off screen on a phone | Both | render + source | High, keyboard/a11y | S | N |
| 2 | Hebrew switches between singular and plural address | HE | render + source | Medium, tone | S | N |
| 3 | Wake phrase spelled two ways on one card | HE | render + source | Medium | S | N |
| 4 | "Turn on" offered next to a switch shown as "(on)" | Both | render + source | Medium, confusing | S | N |
| 5 | Keys 1/2/3 are hidden from assistive tech | Both | render + source | Medium, a11y | S | N |
| 6 | "Did not work" does not say it opened a field | Both | render + source | Low-medium, a11y | S | N |
| 7 | Four counters for one fact | Both | render | Medium, clutter | S | N |
| 8 | "More" and "Tools" mix owner steps with maintainer tools | Both | render | Medium, clutter | M | N, owner call |
| 9 | Guide link and examples do not fit the topic | Both | render + source | Low-medium | S | N |
| 10 | Menu says "Setup guide", page says "Setup and tour" | EN | source | Low | S | N |

### 1. Focus lands on an off-screen button (phone)
- At 390x860 the Works button starts at y=834 (HE) and y=857 (EN): cut by or below the fold. The two approval
  cards above the page take 298 px ([he-390-top.png](2026-11-16-setup-wizard-rtl-a11y/he-390-top.png),
  [en-390-top.png](2026-11-16-setup-wizard-rtl-a11y/en-390-top.png)).
- `show()` focuses Works with `preventScroll: true` (`wizard.js:74`), so a keyboard user's focus is invisible and
  Enter answers a card they may not have read.
- Proposal: after focusing, `scrollIntoView({block: "nearest"})` on the card (not the button). Moving or shrinking the
  approval cards would be Tier R and is not proposed.

### 2. Hebrew register is mixed
- The flow speaks in plural (`אמרו`, `תראו`, `שלכם`), but "Do it for me" is masculine singular `תעשה את זה בשבילי`
  (`wizard.js:161`) and the connection detail is singular `מכוייל לקול שלך` (`connect.py:301`, `:303`), see
  [he-390-more.png](2026-11-16-setup-wizard-rtl-a11y/he-390-more.png). `מכוייל` is also not the standard spelling
  (`מכויל`).
- Proposal: `לבדוק בשבילי (בדיקה בטוחה)`; `מכויל לקול שלכם` / `לא מכויל לקול שלכם`.

### 3. Two spellings of the wake phrase
- Card line "Try it": `היי ג'רביס` (`wizard.py:133`); "More examples" on the same card: `היי ג'ארביס` (guide data);
  line 1 uses English `Hey Jarvis`. In `core/src/jarves` (.py/.toml/.js) `ג'ארביס` appears 231 times, `ג'רביס` 7 times; one of those is the speech-alias list in `recall_text.py:219`, which must keep every spelling.
- Proposal: use `ג'ארביס` in the other 6 places; keep the English wake phrase only where the user must say it in English.

### 4. "Turn on" next to "(on)"
- "More" shows `Switches: Wake word "Hey Jarvis" (on)` and a **Turn on** button
  ([en-1024-more.png](2026-11-16-setup-wizard-rtl-a11y/en-1024-more.png)). `all_on` needs enabled **and** available
  (`wizard.py:420`); the text shows only `enabled`. On this demo home the feature is on but not available.
- Proposal: when enabled but not available, show `on, not available here: <reason>` (EN + HE) and no button.

### 5. Shortcuts are invisible to screen readers
- The `<kbd>` hints are `aria-hidden` and the buttons have no `aria-keyshortcuts` (probe: `null`), `wizard.js:90-96`.
  The names also start with a glyph (`✓ Works`, `✗ Did not work`), which some readers say as "check mark".
- Proposal: `aria-keyshortcuts="1"` / `"2"` / `"3"` (and `Enter` on Works); wrap the glyph in `aria-hidden`.

### 6. "Did not work" state is not exposed
- Opening the note field sets no `aria-expanded`/`aria-controls` on the button (probe: `null`), `wizard.js:114-125`;
  [he-390-fail.png](2026-11-16-setup-wizard-rtl-a11y/he-390-fail.png).
- Proposal: `aria-expanded` true/false and `aria-controls` pointing to the field box.

### 7. Four counters for one fact
- Header `16 left · version 4.0.1`, card `Topic 1 of 16`, progress bar `0 of 16`, list `Still left (15)`
  ([he-1024-card.png](2026-11-16-setup-wizard-rtl-a11y/he-1024-card.png)). 15 vs 16 is correct (the list skips the
  open card) but reads as a mismatch.
- Proposal: keep the card line and the bar; header shows only the version; list label `Next (15)` / `הבאים (15)`.

### 8. Owner steps and maintainer tools share the page
- "More" holds up to 10 items (connection, switches, Turn on, safe check, examples, page link, CLI, guide, key note).
  "Tools" offers the sign-off sheet, automated checks and Start over to every user.
- Proposal (owner design call): "More" keeps safe check, the guide and the CLI line; Tools appears only in
  `baseline` mode or moves under Development. No behaviour change until approved.

### 9. Guide and examples off-topic
- The "Voice and wake word" card links the guide "Connect the AI (voice and answers)" (`first = gs[0]`,
  `wizard.py:404`) and the examples repeat a bare `Hey Jarvis` after `Hey Jarvis, what time is it?`.
- Proposal: prefer the guide whose feature matches the topic; drop examples contained in an earlier one.

### 10. Name mismatch (EN only)
- Menu `Setup guide` (`index.html:69`, `dashboard_pages.py:20`), page heading `Setup and tour`; Hebrew uses
  `הגדרה וסיור` in both.
- Proposal: `Setup and tour` in the menu too.

## Suggested order

One small Tier N PR for 1, 5, 6 (a11y, `wizard.js` only, with a browser test each) and one for 2, 3, 10 (text,
through the catalog and Hebrew parts). 4 and 9 next. 7 and 8 wait for the owner.

## Evidence

Screenshots in [2026-11-16-setup-wizard-rtl-a11y/](2026-11-16-setup-wizard-rtl-a11y/) (1x, light theme):
`he-390-top`, `en-390-top` (fold), `he-390-fail`, `he-390-more`, `en-1024-more`, `he-1024-card`.
Reproduce: start the demo dashboard as `core/tests/test_wizard_browser.py` does, open `#/setup` in EN/HE at 1024/390,
reset the wizard (`POST /api/wizard/control {"op":"reset"}`), and evaluate `apc.RULES` with
`{"clipping": true}` after opening every `details`.
