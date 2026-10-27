# SW-V4 package K: dashboard completion (20 Oct 2026)

Branch `v4-k-dashboard` (off `origin/v4-final` a226def) · issue [#17](https://github.com/igalgerman/JARVIS/issues/17) ·
plan: `docs/JARVES_SW_V4_WORK_PACKAGE.md` section K · V3 leftovers: [SWV3_CAMPAIGN_BUG_TABLE_DASH.md](SWV3_CAMPAIGN_BUG_TABLE_DASH.md) D-10..D-18 ·
screenshots (DEMO profile only): [swv4_k/](swv4_k).

## 1. What changed

### Languages: English (default) and Hebrew only (owner decision)
- `locales/ar.js` and `locales/ru.js` are deleted (the repository history keeps them). Their `<script>` tags, the
  Arabic font rules in `shell.css`, the `check-addons` bundle entries (`setup_wizard.py`), the sweep tools and
  `docs/APPEARANCE.md` no longer name them. The generic mechanism stays: one catalog file + one script tag per
  locale, `<key>_<locale>` server siblings from `locales.TRANSLATORS`.
- A saved `ar` / `ru` choice falls back to English (`LOC[s]` check in `i18n.js`), tested in
  `test_owner_languages_are_english_and_hebrew_only`.
- The popup and the CLI keep English as the default (unchanged: `clilang.py`, `approval_popup.py`).

### D-10..D-18
| ID | Fix | Where | Test / evidence |
|---|---|---|---|
| D-10 | ~190 English phrases in the Hebrew UI: every page written with `tx(he, en)` (M3 pages, runtime, approvals, features matrix, settings labels), server siblings for the runtime platform table and session issues, ~260 new catalog keys + 20 patterns, page names in Hebrew guide texts use the Hebrew navigation names | `m3.js`, `app.js`, `approvals.js`, `m4.js`, `pages.js`, `usage.js`, `guides.js`, `hebrew.py`, `runtime/__init__.py`, `dashboard.py`, `guides/data/*.toml` | sweep rule `untranslated` = 0 on every Hebrew page |
| D-11 | Spoken Hebrew hints in English reasons (`tell the user: "<Hebrew>"`) get an English display sibling `<key>_en` ("...; run: jarvis google start"); `txh` prefers it; the CLI uses the same function | `locales.display_en`, `i18n.js`, `clilang.py` | `test_english_display_form_drops_the_spoken_hebrew_hint`; sweep rule `hebrew-on-english` = 0 |
| D-12 | Arabic/Russian partial coverage | removed by the owner decision | `codes() == ["en", "he"]` |
| D-13 | Self-check finding cards: one fixed layout (title row, meta, detail, a two-column cause / fix grid, buttons at the start) | `selfcheck.js`, `.finding*` in `style.css` | screenshots `selfcheck_*` |
| D-14 | Log-page conversation links: `aria-label` "Open conversation <id> · <time>", text translated; same for "why?" links (reply number), "Open →" (connection name), "Open in Google Cloud" (step), "Guide" (feature) | `pages.js`, `context.js`, `home.js`, `setup.js` | sweep rule `identical-links` = 0 |
| D-15 | Granting a missing / relative folder answers **400** `SANDBOX_BAD_PATH` with the message (was 403) | `sandbox.py` | `test_m3_sandbox.py::test_dashboard_route` |
| D-16 | Workspace "Earlier versions" is a `table.stack`: one card per version on phones | `pages.js`, `style.css` | screenshot `workspace_user_md_he_390` |
| D-17 | Header chips: a fade at the edge that hides more chips (direction-aware, follows scrolling); the usage strip got the same cue | `shell.js` `fadeCue`, `shell.css`, `usage.js` | sweep rule `hidden-scroll` = 0 |
| D-18 | The dashboard link token is described as a private key valid until the dashboard stops (not "one-time"); "Unknown / Unknown" reads "לא ידוע / לא ידוע (הספק לא מדווח)" in Hebrew | `cli.py`, `dashboard.py`, `dashboard_auth.py`, `app.py`, catalog | — |

### Bugs found by the sweep and fixed
| ID | Sev | Symptom | Fix | Test |
|---|---|---|---|---|
| K-01 | P1 | `/api/features` answered **500** ("dictionary changed size during iteration") when another request imported a plugin module that registers a feature | `Features.list()` and the other catalog loops iterate a snapshot | `test_features.py::test_list_survives_a_feature_registered_meanwhile` |
| K-02 | P2 | Approvals banner was `position: fixed`: with two requests it covered the page (on phones the whole first screen) | banner sits in the page flow above the content, grid of cards | screenshots `home_*`, `approvals_*` |
| K-03 | P2 | Text contrast below AA: `--muted` 3.8-4.4:1, accent links 4.0-4.4:1, white on the green switch 3.35:1, white on dark-mode accent 3.6:1 | tokens `--muted #686762` / `#9a9992`, `--accent #1d68c4` / `#4a93ea`, `--accent-fill`, `--good-fill`, `--good-text`; dark `--critical #e5534b` | sweep rule `color-contrast` = 0 |
| K-04 | P2 | 176 form controls without an accessible name (settings table, jobs, files, alerts, autonomy, notes, workspace, approvals) | labels / `aria-label` on every control | sweep rule `label` = 0 |
| K-05 | P3 | Approvals: auto-deny shown as "3600 s"; history showed raw codes (`timeout`, `voice`, `tool`) and privacy-masked titles as `<21 chars>` | minutes, translated decision/channel/kind, "(hidden by privacy, 21 characters)" | screenshots `approvals_*` |
| K-06 | P3 | A stale "Next scheduled jobs ... once the scheduler is installed" card on Controls & runtime | removed (the scheduler exists) | — |
| K-07 | P3 | Checklist count "6 / 3" read backwards in RTL | "3 of 6 done" / "3 מתוך 6 בוצעו" | — |
| K-08 | P3 | Guide "What to say" showed both languages on every page | one language per page | — |
| K-09 | P2 | At 1024 px the app bar was wider than the window: the language picker and the theme button were off screen (sideways page scroll) | at 901-1279 px the status chips take their own row | sweep 2 |
| K-10 | P3 | Dark mode: white on the red count badges 3.7:1 | `--count-bg` token (#b3302b in dark) | sweep 2 |
| K-11 | P2 | Timers/approvals phone cards: label and value overlapped (flex) and number cells flipped the card in RTL | `table.stack` cells are a label/value grid; numeric cells follow the page direction in card mode | screenshots `timers_he_390`, `runtime_he_390` |

### New in K (plan scope 4)
- **Approvals page:** pending requests with the question he may say aloud (`spoken_en` / `spoken_he` from
  `approvals.spoken_kind`, kind only, never the details), details as a key/value list, decision history with a
  decision filter and a text search, phone cards, keyboard reachable (native controls only).
- **Timers & alarms:** ringing state ("Ringing now", highlighted row), **Snooze 5 min** (running: later; rang or
  missed: a new timer with the same label), **Edit** (label, minutes, alarm time). Same operations in the CLI:
  `jarvis timers snooze ID [MINUTES]`. Test: `test_cli_parity.py::test_timer_snooze_and_edit_by_dashboard_and_cli`.
- **Lists:** items as labelled check boxes, "N open" counts, known list names in Hebrew, clear through the full
  approval popup (unchanged server path).

### Design system cleanup (plan scope 4, owner request)
- Tokens on `:root` in `style.css`: spacing `--space-1..5`, type `--fs-xs..l`, `--accent-fill`, `--on-accent`,
  `--good-fill`, `--good-text`, `--focus`; light and dark both AA.
- One component set: `.card`; `.pill` (states) / `.chip` (code, ids) / `.schip` (app bar); one table, with
  `table.stack` turning rows into cards on phones (cells carry `data-label`, translated into `data-label-t`);
  `.kvlist` / `.finding-kv` key/value grids; `.empty`; `.field` labels; `.check` check boxes.
- `approvals.css` uses the tokens (no hard-coded colours, dark mode correct).
- A global `prefers-reduced-motion` rule stops animations and transitions.

## 2. Tests

### Real-browser sweep: `core/tools/all_pages_check.py` (renamed from `v3_all_pages_check.py`)
Every navigation page plus 7 detail views (session, context, raw, logs of one conversation, workspace file, a guide,
features filter) on a throw-away DEMO profile (synthetic conversations, timers, lists, two pending approvals and an
approval history), real headless Edge, the browser closed after each page group. Fails on:
JS errors, console errors, HTTP >= 400, failed requests, a page that is not registered (router fallback), wrong
`lang`/`dir`, empty page, sideways page scroll, and the DOM rules of `core/tools/all_pages_rules.js`:

| Rule | What |
|---|---|
| `control-name`, `label`, `icon-button-name`, `image-alt` | every button, link, input, select, textarea, summary has an accessible name; icon-only buttons have `aria-label` |
| `duplicate-id`, `tabindex`, `aria-hidden-focus` | no duplicate ids, no positive tabindex, nothing focusable inside `aria-hidden` |
| `color-contrast` | WCAG AA (4.5:1, 3:1 for large text) for every visible text, real computed colours and composited backgrounds |
| `mouse-only` | anything with `cursor: pointer` is natively focusable or has `tabindex` |
| `target-size` | buttons, selects and inputs at least 24x24 px on phone widths |
| `identical-links` | links in the page with the same name that go to different places |
| `direction` | Hebrew blocks laid out right-to-left and not left-aligned; English blocks not right-to-left |
| `off-screen`, `hidden-scroll` | elements that stick out of the viewport without a scroller; sideways scrollers without a cue |
| `reduced-motion` | no running animation under `prefers-reduced-motion: reduce` |
| `loading-stuck`, `bad-text` | no page left on "Loading…", no `undefined` / `NaN` / `null` / `[object Object]` in reading text |
| `untranslated`, `hebrew-on-english` | English phrases on Hebrew pages (3+ words inside Hebrew sentences, 2+ alone), Hebrew on English pages; logs, code, user content (`dir=auto`, `data-user`), CLI commands, quoted UI labels and quoted speech excluded |
| `focus-ring` (`--focus`) | Tab through the page: every reached control shows a focus ring |

**Results (real headless Edge 155.0.4283.62 through Playwright (python), Windows 11, DEMO profile, 20 Oct 2026):**

| Sweep | Matrix | Loads | Problems | Evidence |
|---|---|---|---|---|
| 1 | 42 pages x en/he x 1440/390 px, light, screenshots | 168 | **0** | [sweep_1440_390_light.json](swv4_k/sweep_1440_390_light.json), [screenshots](swv4_k) |
| 2 | 42 pages x en/he x 1024 px, dark, Tab through every page (focus rings) | 84 | **0** | [sweep_1024_dark_focus.json](swv4_k/sweep_1024_dark_focus.json) |
| 3 | 42 pages x en/he x 1440/390 px, dark | 168 | **0** | [sweep_1440_390_dark.json](swv4_k/sweep_1440_390_dark.json) |

Before the fixes the first sweep had 150 of 168 loads with problems: 2835 contrast hits, 678 English phrases on
Hebrew pages, 64 Hebrew strings on English pages, 176 unlabeled controls, 16 identical-link groups, 85 hidden
scrollers, a 500 on /api/features, a page that did not load (a syntax error caught by the `wrong-page` check), and
`[object HTMLDivElement]` / `null` texts. Sweep 2 found the 1024 px app-bar overflow and the dark badge contrast.
Not run: the headed Edge pass and the Ubuntu/Chromium CI job (package M owns the CI `dashboard` job; the tool runs
with Playwright Chromium when `--channel` is empty).

### pytest
- `core/tests/test_dashboard_a11y.py` (new): the rules catch each planted problem; Hebrew on an English page is
  caught but user content is not; the full page sweep (Hebrew at 390 px, English at 1440 px) has 0 problems.
- `test_dashboard_locales.py` (en/he only, new tests for the language set and D-11), `test_hebrew_texts.py`,
  `test_m3_sandbox.py` (D-15), `test_features.py` (K-01), `test_cli_parity.py` (snooze/edit).

Run locally (one pytest at a time, `core/.venv`, Windows 11):
- 19 files around the dashboard (a11y, locales, hebrew texts, dashboard, LAN, phone, features, CLI parity, sandbox,
  guides, runtime, setup wizard, CLI English, connect, log lint, V3 P1, R-01): **335 passed, 0 skipped**
  (4 linux_only deselected), 5 min 14 s (the real-browser page sweep inside it).
- 14 more touched areas (appearance, approval popup, campaign features, context trace, Google, Google setup,
  helper approvals, identity, minor fixes, profiles, recall surfaces, self-check, usage, browser): **333 passed**.
- Not run: the full suite and CI (RAM limit; several agents).

## 3. UX review, page by page

Opus 5.5 review of the screenshots (DEMO profile, real headless Edge, en and he, 1440 and 390 px) plus the rule
results. Findings by page group; all fixed unless marked open.

| Group | Pages | Findings (fixed) | Open (P3) |
|---|---|---|---|
| Every page | shell, app bar, banner, usage strip | the approvals banner covered the page (K-02); on phones it is now short (title + buttons; the full request is on Approvals); header chips and the usage strip scroll sideways with a fade (D-17); muted text and links below AA (K-03); `[object HTMLDivElement]` from nested node lists (generic `el()` fix); straight quotes in Hebrew (the low-9 „ “ pair is German style) | the fixed usage strip is cut at the edge on phones (it is a swipe row with a fade); a collapsible strip is a design choice for the owner |
| Overview | Home, Controls & runtime, Connections, Devices | the runtime page is written in both languages; the platform table becomes phone cards with Hebrew blockers; the stale scheduler card is removed (K-06); the checklist count reads correctly in RTL (K-07); Google console steps: Hebrew verb + quoted English label; "Open →" links are named | — |
| Activity | Approvals, Conversations, Recall, Issues, Self-check, Background tasks, System log | Approvals page (scope 4); D-13 finding layout; D-14 link names; issue titles and evidence keys in Hebrew; "why?" links are named | — |
| Everyday | Calendar, Jobs, Timers, Lists, Reports, Alerts, Notes, Files, Browser | every M3 page is written per language, with labels on every control (K-04); Timers snooze/edit/ringing; Lists check boxes with labels; D-15; server error messages through the catalog | — |
| Assistant | Identity & voice, Appearance, Memory, Persona files | composed sentences translated as whole pieces; D-16 version cards; spoken examples in the page language | — |
| Capabilities | Setup & add-ons, Features & switches, Skills & health, New Skills, Autonomy | features matrix headers, health, permissions and risk in Hebrew; the green switch passes AA; "Guide" links are named; autonomy levels in words | — |
| Usage & system | Usage, Costs, Guides, Settings, Backup, Profiles | settings controls are labelled by their row; usage numbers ("0.0 דק׳ / 30.0 דק׳"); costs notes; backup labels; the profiles "never copied" list | — |
| Detail views | session, context, raw, conversation log, persona file, guide, features filter | session issue titles from the server Hebrew rules; the context empty state | — |

Consistency rules now applied everywhere: one `h1` per page (`header()`), section titles are `h2`, Hebrew buttons
are nouns, every empty list uses `.empty` with one plain sentence, async message areas have `role="status"`, and
every table that can be wide sits in `.tscroll` and becomes cards on phones.

## 4. Hebrew wording: glossary and the 20-line sample for the owner (OK-08)

One word per concept on every page, in the guides and in the server texts:

| English | Hebrew | Not |
|---|---|---|
| Core | ליבה | מנוע, שרת |
| dashboard | לוח הבקרה | דשבורד |
| feature / switch | יכולת / מתג | פיצ'ר |
| skill (tool) | כלי | מיומנות, כישור (nav: "כלים חדשים") |
| approve / deny | אישור / דחייה (buttons: nouns) | אשר / דחה |
| conversation | שיחה | סשן |
| timer / alarm | טיימר / שעון מעורר | |
| snooze | עוד 5 דקות | נודניק |
| wake phrase | מילת ההפעלה | |
| backend model | המודל | בקאנד |
| estimate | הערכה | |
| page names | the Hebrew navigation names in quotes ("חיבורים", "שימוש ומגבלות") | the English page names |

Product and service names (JARVES, Google, Telegram, Pear Desktop, Windows, Raspberry Pi, OpenAI, ChatGPT), CLI
commands and English UI labels of third-party sites (the Google Cloud console buttons) stay as they are.
Buttons are nouns (שמירה, הוספה, ביטול, אישור); instructions use the plural imperative (מריצים / פתחו), the house
style of the existing Hebrew texts.

**Sample for the owner (OK-08):** 20 lines as they appear now (spoken lines from the Core, dashboard texts).
Please answer "approved" or list the lines to change.

| # | Where | Hebrew |
|---|---|---|
| 1 | spoken approval | לשלוח הודעת טלגרם לדנה? |
| 2 | spoken approval | להריץ פקודה במחשב? |
| 3 | spoken approval | למחוק משהו? |
| 4 | timer rings (spoken) | הטיימר של פסטה הסתיים |
| 5 | alarm rings (spoken) | שעון מעורר: לקום |
| 6 | approvals banner | JARVES מבקש אישור · נדחה אוטומטית בעוד 60 דק׳ |
| 7 | approvals page | מה הוא יגיד בקול: לשלוח הודעת טלגרם אל Dana? |
| 8 | approvals history | פג תוקף (נדחה) · בקול · שליחה |
| 9 | timers page | הליבה מצלצלת בקול, גם כשהחלון סגור. |
| 10 | timers buttons | עוד 5 דקות · ביטול · עריכה · מצלצל עכשיו |
| 11 | lists page | קניות ומשימות: מוסיפים, מסמנים, מסירים. ניקוי מבקש אישור. |
| 12 | home | מה לעשות קודם? · 3 מתוך 6 בוצעו |
| 13 | home, Google | בטרמינל הקלידו jarvis google start. הוא מוביל אתכם צעד אחר צעד (או פתחו את עמוד "חיבורים"). |
| 14 | runtime | JARVES רץ ברקע; החלון לא חובה. |
| 15 | runtime | סגירה רק מסתירה את JARVES |
| 16 | features | צריך להפעיל קודם את "מצלמה" |
| 17 | files (error) | נכשל: התיקייה הזאת לא קיימת |
| 18 | alerts | התראות יזומות: פועלות. חלון שקט = שורה שקטה על הפנים; דיבור = נאמר בקול. |
| 19 | autonomy | האבחון מציע שינויי כיול. "מציע ושואל" תמיד שואל; "סיכון נמוך" מחיל רק את השינויים שסימנתם. |
| 20 | Google step | "Application type": בוחרים "Desktop app" |

## 5. Open items

| Item | Why open | Next |
|---|---|---|
| OK-01..OK-08 (owner hands-on review, 7.18) | needs the owner | 30 minutes; OK-08 = the sample above |
| Headed Edge pass, Ubuntu Chromium in CI | RAM limit here; the CI job belongs to package M | M wires `python tools/all_pages_check.py --channel ""` into the `dashboard` job |
| Health/Self-check 2.0, Coding agents, WhatsApp, Wake and Delegation pages of other packages | not on `origin/v4-final` yet | the sweep picks up every new nav page by itself; their strings go through `tools/catalog_sync.py` |
| The `jarvis files` CLI gap (P0.3) and the profile-switch decision D13 / group names D14 | owner decisions OD-14b / D13 | unchanged; listed in the plan |
| Usage strip on phones | design choice (P3) | owner question 2 in HANDOFF |
| Hebrew wording beyond the dashboard (voice prompts, guides in full) | the sample covers the main surfaces; the voice prompts belong to package C/A scenarios | re-run the voice greeting/approval scenarios after the owner approves the wording |
