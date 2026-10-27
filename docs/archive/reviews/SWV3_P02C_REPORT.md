# SW-V3 P0.2c: multi-language dashboard (English default, correct RTL)

**Branch:** `v3-p02c-multilang` (origin/main 882ae27 + origin/v3-p02-dashboard merged in) · **Issue:** #17 · **Date:** 20 Oct 2026 · **Model:** Claude Opus 5.5

## Owner requirement
The dashboard must support several languages, and **English must be the default**. RTL must be correct.

## What changed

| Area | Before (P0.2/P0.2b) | After |
|---|---|---|
| Default | Hebrew. `<html lang="he" dir="rtl">`, and Hebrew whenever nothing was saved | **English.** `<html lang="en" dir="ltr">`. The first load uses an explicit saved choice (`localStorage jarves-lang`, written only by the picker), else English. The browser language is never used: the Playwright check loads the page with a `he-IL` browser locale and gets English. `#/page?lang=xx` still forces a language for captures. |
| Locales | Two, hard-wired (`lang === "he"` in 6 scripts) | **N locales from a registry.** `en` (source), `he` (complete), `ar` (RTL proof, partial), `ru` (LTR proof, partial). Each is one catalog file `dashboard_static/locales/<code>.js` with its meta (name, dir, bcp47) and a dict keyed by the English text. The registry entry is its `<script>` tag in `index.html`, and the script order is the picker order. |
| Switch | English/עברית toggle button in the app bar, plus copies in Guides and Setup | **Language picker:** a native `<select>` in the app bar (`#langSlot`), with each language in its own name and `lang`/`dir` on every option. The in-page toggles are removed: there is one control. |
| Server texts | `<field>_he` from `hebrew.py` | **`jarves/locales.py`** holds the registry (`TRANSLATORS = {"he": hebrew.he}`) and the generic `attach` / `siblings` / `list_siblings` / `issue`, which add `<field>_<locale>`. `hebrew.py` stays the first translator. Callers (features, settings, usage, backup, identity, guides, dashboard) go through `locales`. The client helpers `txh` / `txo` pick `<field>_<lang>`, else the English field passed through the client catalog. |
| Inline texts | `tx(he, en)` | Unchanged API. For he it returns the Hebrew; for en, the English; for any other locale, the English translated by that locale's catalog. All 280 inline English literals are now keys in `en.js`/`he.js` (test-enforced). |

## RTL correctness (he and ar; every rule keys on `html[dir="rtl"]`, never on one language)
- **Logical CSS:** `margin-inline`, `padding-inline`, `inset-inline`, `border-inline-start`, `text-align: start`. The last physical paddings are gone: the privacy mini-chips, and inline `margin-right` in `m2.js`. A test scans every CSS and JS file for physical left/right.
- **Mirrored automatically:** the drawer slides in from the inline-start edge. The search results anchor under the search box at inline-start. The status chips and the usage strip flow in the reading direction. All of these are measured in the browser check (drawer side, results side).
- **Direction-implying arrows:** they are written mirrored in the RTL catalogs ("Open →" becomes "فتح ←" / "פתיחה ←"; a test checks this). An untranslated English fallback in an RTL page gets its arrows flipped.
- **English inside RTL pages:**
  - Each untranslated English text node is isolated: FSI…PDI (U+2068/2069). Before: "?What should I do first". After: "What should I do first?".
  - An element whose text has no RTL letter at all (a sentence split over text, link, text) becomes one `dir="ltr"` paragraph. Isolating each piece would line the pieces up right-to-left.
- **Server detail texts** get `dir = JL.textDir(text)` instead of `dir="auto"`. Bug fixed: "Pear Desktop לא רץ (…)" used to run LTR because its first letter is Latin. The global `unicode-bidi: plaintext` rule caused the same bug, so it was removed.
- **LTR islands** (`unicode-bidi: isolate; direction: ltr`): code, kbd, samp, `.mono`, `.kv` cells, `.chip`, `.num` / `td.num`, URL / e-mail / number / date / time inputs, charts.
  - Arabic uses `ar-u-nu-latn`, so dates use the same Latin digits as the server numbers.
- **Tables / scroll:** the system log table overflowed the page by 50–100 px in every language. It now scrolls inside its card (`.tscroll`), which also scrolls in the RTL reading direction. On phones, the Features cards keep clear of the 3 px attention edge.
- **Never translated:** module names in the log (the catalog was turning `live` into "חי"). Placeholders of `dir="auto"` inputs are ours and are now translated ("Search what was said…").
- **Arabic typography:** the font stack includes Segoe UI / Noto Sans Arabic, `line-height: 1.6`, and no letter-spacing on RTL headings.

## Coverage (reported by `test_dashboard_locales.py`)
| Locale | Keys | Note |
|---|---|---|
| en | 818 | source; values = English wording |
| he | 818 / 818 (100 %) | a missing key **fails** the test |
| ar | 212 / 818 (26 %) | shell, navigation, Home, privacy strip, guide states, common words; the rest shows English (isolated, readable) |
| ru | 212 / 818 (26 %) | same scope |

Server texts exist in Hebrew only (`*_he`). In ar/ru, server detail texts show in English.

## Verification
- **Tests** (run one file at a time on a loaded machine): `core/.venv/Scripts/python.exe -m pytest -o pythonpath=src`
  - `tests/test_dashboard_locales.py`: **17 passed**. It checks the registry order, meta and dir per locale, that en is the identity source, he complete, ar/ru coverage and no stray keys, the RTL arrows, that inline English is in the catalog, default English (no `navigator.language`), the picker and generic helpers, the logical-CSS scan, that catalogs are served as JS, the generic server registry with a fake third locale, and that `/api/features` carries `note_he`.
  - `test_dashboard_p02.py` + `test_hebrew_texts.py`: 20 passed.
  - `test_dashboard.py` + `test_guides.py` + `test_usage.py`: 46 passed.
  - `test_dashboard_phone.py` + `test_setup_wizard.py` + `test_identity.py` + `test_backup.py` + `test_feature_setup.py`: 75 passed.
- **Full suite**, one earlier run before the last two bidi fixes and the coordinator's RAM limit: **1196 passed, 3 skipped, 1 failed**. The failure is `test_launcher_help.py`: `jarvis.cmd --help` timed out after 30 s because this worktree has no own `.venv`, so the launcher bootstraps one. This is environmental and unrelated.
- **Fixed on the way:** this branch merges main (P1 channels) with v3-p02-dashboard, and `test_hebrew_texts` then failed for the new Telegram / briefing / WhatsApp / Home Assistant feature texts. Hebrew was added to `hebrew.py`.
- **Real browser:** `core/tools/v3_locale_screens.py --channel msedge` (Playwright 1.64, headless Edge, demo data, 127.0.0.1, no network).
  - **38 captures, 0 problems**: en / he / ar × 7 pages at 1440 px, 3 pages at 390 px, drawer at 390 px, search open, plus the first-load default and picker + reload persistence.
  - Each capture is checked automatically: html lang/dir, no horizontal page scroll, picker value, LTR code islands, drawer side and results side.
  - PNGs and `checks.json` are in [`swv3_p02c/`](../../reviews/swv3_p02c).
  - The first runs found and drove the fixes above: log-table page scroll, misplaced "?", the Hebrew sentence running LTR, module names translated, a split English paragraph reversed, the untranslated placeholder.
  - These are automatic captures, not an owner hands-on check.

## Open
- ar/ru are proofs. Full translations need a translator; the coverage test shows what is missing.
- Server-side translations exist for he only. Adding `ar` server texts = an `arabic.py` + one `TRANSLATORS` line.
- Hand-composed Hebrew reasons in `guides/__init__.py` (`reason_he` combinations) and `setup_wizard.py` stay Hebrew-specific.
- The Godot face / CLI language (`config.language`, default `he`) is separate from the dashboard locale and was not changed.

## Questions for the user
1. Which further dashboard languages, if any, should get full catalogs (ar / ru are partial proofs)? **USER DECISION REQUIRED**
2. Should the dashboard language also drive the spoken/CLI language, or stay separate (current: separate)? **USER DECISION REQUIRED**
