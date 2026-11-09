# UX review: dashboard Home and Settings (clutter, Hebrew/RTL, empty states)

Date: 2026-11-09. Task: JAR-161. Status: proposal only, no product code changed. Source and renders at `origin/main` 86ef09f3.

## Scope and evidence

- **Covered:** the Home page (`#/home`), the Settings page (`#/settings`) and the app bar shown above them.
- **Left to other tasks:** setup wizard (JAR-117), per-page empty/loading/error review (JAR-136), phone app (JAR-78). Finding 5 touches an empty state only because it is the top of Home.
- **Rendered this time.** JAR-57 could not render; this review did: `core/tools/all_pages_check.py --pages home,settings --langs en,he --widths 1440,390`, 8 loads, 0 failed, no rule hits. Screenshots stayed in a temporary folder because they are full of demo data; each finding describes what it shows.
- **Measured** with a throw-away Playwright probe on the same demo data: page heights, table cell widths, banner overflow, and the lost-edit test in finding 3.
- **Confidence labels:** **render** = seen in a fresh screenshot; **measured** = probe output; **source** = read in code, not exercised.
- **UNVERIFIED:** real phone, iOS/Android browsers, screen readers, dark theme, widths between 390 and 1440, and Home with JARVES running (the demo Core is off, so limit cards and the live state were not seen).

## What the pages look like today

Descriptions of the 2026-11-09 renders (light theme, demo data, two pending approvals).

- **Home, Hebrew, 1440 px (1118 px tall).** RTL mirrors correctly: navigation on the right. Top bar: search, profile, a "stopped" chip, a Start chip fading out mid-word, bug reports, two privacy items. Two approval cards. A "JARVES is off" hero with no button. A six-step "What should I do first?" list. Three tiles. Last conversation. A collapsed "More details" row touching the card above it.
- **Home, Hebrew, 390 px (1588 px tall).** Three rows of app-bar controls before any content. The three tiles sit two plus one, leaving a blank slot.
- **Settings, Hebrew, 1440 px (3140 px tall).** Voice card, then 8 cards and 35 rows in three columns: label, control, grey "default 25 · 0-120". Control column positions differ from card to card. Footer holds two long file paths. Sticky Save bar.
- **Settings, Hebrew, 390 px (5214 px tall).** In the AI card the third column is so narrow that "ברירת מחדל" breaks into fragments of two or three letters, stacked down the row, and the label column breaks mid-word.

## Ranked findings

Cost: S = under half a day, M = 1-2 days. All are Tier N as proposed: dashboard presentation only, no approvals, Gate, release or update code.

| # | Finding | Page | Confidence | Benefit | Cost |
|---|---|---|---|---|---|
| 1 | Settings table collapses at phone width | Settings | render + measured | High | S |
| 2 | Privacy mode saved in Settings waits for a restart; the top switch acts at once | Settings | source | High, privacy | S |
| 3 | Unsaved settings are lost silently on navigation | Settings | measured | High | S |
| 4 | Privacy warning is truncated with no way to read it | App bar | measured | High, privacy | S |
| 5 | "JARVES is off" gives no button | Home | render + source | High | S |
| 6 | Nothing says a restart is still needed after Save | Settings | source | Medium | S |
| 7 | 35 equal rows; everyday and expert settings look alike | Settings | render + measured | Medium | M |
| 8 | Raw values on the Hebrew page: `true`, `false`, `local`, seconds only | Settings | render | Medium | S-M |
| 9 | Save and error messages have no Hebrew path | Settings | source | Medium | S |
| 10 | File paths scramble in the Hebrew footer | Settings | render | Low | S |
| 11 | Group titles are not headings; tables have no header cells | Settings | measured | Medium, accessibility | S |
| 12 | "1 conversations" / "1 שיחות" | Home | render | Low | S |
| 13 | Spacing: no gap above "More details"; blank tile slot at 390 px | Home | measured + render | Low | S |
| 14 | Voice is offered in both Settings and Identity & voice | Settings | source | Low | S |

### 1. Settings table at phone width
- Three-column table with no phone layout: `pages.js:479-482`. Smallest cell widths at 390 px Hebrew: label 57 px, default 38 px. English: 108 and 47.
- The tool's rules pass this page, since nothing overflows; the text is only squeezed.
- **Proposal:** two columns (label, control), with the default and range as a small line under the label. This also shortens the page at every width. Add a report-only rule for visible text cells under about 60 px wide.

### 2. Two privacy controls that behave differently
- The top switch applies within a second and also saves to config (`privacy.py:7`, `:78-81`). The Settings row "Privacy mode: never keep what was said" only writes `config.toml` (`dashboard.py` `api_settings_write`, "applies: next start").
- A user who ticks it and presses Save believes privacy is on while the running Core keeps text until restart. The honest state does appear in the top bar, so this is misleading rather than hidden. Not exercised against a live Core.
- **Proposal:** in the Settings row, replace the checkbox with the current state and a link to the real switch. Filter in the page only: leave `config_edit.EDITABLE` alone, because `autonomy.py:88` uses it.

### 3. Lost edits
- Probe: changed a value (bar showed "1 unsaved change(s)"), moved to Home and back. The value returned to 25 with no warning. `changed` lives inside the page function (`pages.js:451`).
- **Proposal:** keep the draft in memory across route changes and restore it with the same "unsaved" counter. No dialog needed. Add `beforeunload` only while the draft is non-empty.

### 4. Truncated privacy warning
- `.priv-banner` is capped at 230 px with an ellipsis (`tree.css:76`), and the probe found it overflowing with an empty `title` at all four sizes. The render reads "Privacy: off — conversation text is ke…", beside a separate "Privacy: off" chip.
- The one message that tells users their words are being stored is the one cut off.
- **Proposal:** merge chip and banner into one short item that fits, such as "Privacy off: text is kept" / "פרטיות כבויה: הטקסט נשמר", linking to the privacy card, with the full sentence as its title. **Never remove the state from view.**

### 5. Home hero when JARVES is off
- The hero prints "Start it with: jarvis" and shows no button because `d.next.link` is empty (`home.js:117`). A working Start control sits in the app bar, fading out at 1440 px.
- **Proposal:** show the existing Start action in the hero when off; keep the command as secondary text. Reuse the current control and its safeguards, no new start path. Empty-state wording stays with JAR-136.

### 6. Restart after Save
- The API returns `"applies": "next start"` for every row (`config_edit.py`), and the page notes it once in the header and in a transient message. A "running now" note appears only in small grey text in the third column.
- **Proposal:** after Save, keep a line in the Save bar, "Saved. Restart JARVES to apply", with a link to `#/runtime`. It clears once running values match.

### 7. Everyday versus expert
- All 8 groups are open and equal; Self-diagnosis thresholds and Pricing carry the same weight as Conversation.
- **Proposal:** add a filter box at the top, and collapse Self-diagnosis, Pricing and AI by default, opening any group holding an unsaved or non-default value. Which groups count as expert is a product call: the owner can change this list on the PR.

### 8. Raw values in Hebrew
- Third column shows `ברירת מחדל true`, `false`, `local`, `priority`, `INFO`. Long durations are seconds only (3600).
- **Proposal:** show on/off words for booleans, add Hebrew glosses for choices via `catalog_add.py`, and a friendly hint such as "שעה אחת" beside large second values. Check against JAR-147 (units) before starting.

### 9. Save and error messages
- `pages.js:470` builds "Saved N value(s) to config.toml…" and `pages.js:502` "config.toml cannot be edited here: ". Neither has an entry in `locales/he.js`. Server validation text from `api_settings_write` is English too.
- **Proposal:** use `tx(he, en)` for the first two. Validation text: show a Hebrew prefix and keep the technical detail in an LTR span.

### 10. Footer paths
- `pages.js:508` puts Windows paths inside Hebrew text; in the render the path's pieces change order across the line break.
- **Proposal:** put each path in its own `dir="ltr"` mono span on its own line.

### 11. Structure for assistive technology
- Probe on the settings body: 0 headings, 0 `th`. Group titles are `<b>` (`pages.js:479`).
- **Proposal:** use `h2` for groups and visually hidden column headers. Check against JAR-127 (accessibility) first. Needs a real screen reader pass.

### 12. Plural
- `home.js:82` always uses the plural; the render shows "1 conversations". Use the singular for 1 in both languages.

### 13. Spacing
- Probe: 0 px between Last conversation and More details (other cards have a visible gap). At 390 px, let the third tile span both columns.

### 14. Voice in two places
- Settings has a voice picker (`pages.js:486`) and Identity & voice has the full table with previews (`m4.js:167`). Whether both write the same value was not traced.
- **Proposal:** after tracing, replace the Settings card with a link.

## JAR-57 findings, rechecked on fresh renders

| JAR-57 # | Then | Now |
|---|---|---|
| 6 Chips cut off | old shot | Still true for the Start chip at 1440 px |
| 7 Home too long | 3551 px at 390 | Largely fixed: 1588 px, chip wall inside "More details". Two privacy items remain (finding 4) |
| 10 Four "unknown" chips | old shot | No longer on Home. Replaced by finding 5 |
| 12 Navigation size | | Unchanged, stays an owner-led task |

## Suggested order

1. One Tier N PR for Settings correctness: 1, 3, 6, 9, 10, 11.
2. One small PR for privacy presentation: 2 and 4, reviewed on their own because they change how privacy state appears.
3. One Home polish PR: 5, 12, 13.
4. After the owner reacts to this doc: 7, 8, 14.

No item is being started by this document.

## Risks

- **Privacy:** 2 and 4 must leave the true state visible, and act no later than today. Do not alter what the switch does.
- **Security:** nothing here touches Gate, approvals or secrets. The approval cards above each page were left out on purpose (Approve/Deny order is JAR-57 finding 1, Tier R).
- **Cost:** none. All checks use demo data and a local browser.
- **Conflicts:** `pages.js` is shared; keep the Settings batch in one PR.

## Needs a real-device check

Findings 1, 7, 11 and any Hebrew layout change: **UNVERIFIED** until checked on a phone and with a screen reader.

## Noted, not proposed here

`pageSettings` at `app.js:488` is unreachable, since `PAGES.settings` is checked first (`app.js:509`). The `question` argument of `oneWay` at `home.js:45` is unused. Both are cleanup only.
