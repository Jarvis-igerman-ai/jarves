# SW-V3 campaign, Pass D (dashboard): bug table

Branch `v3-campaign-dash` (off `v3-final` d701ef4) · issue [#17](https://github.com/igalgerman/JARVIS/issues/17) · report: [SWV3_CAMPAIGN_DASHBOARD.md](SWV3_CAMPAIGN_DASHBOARD.md). Severity per the plan (P0-P3). Policy: P0-P2 fixed at once, P3 listed.

## Fixed (retested in real headless Edge)

| ID | Sev | Where | Symptom | Fix | Retest |
|---|---|---|---|---|---|
| D-01 | P1 | Home, Skills health, chips | About 2 minutes after the Core started, Home said **"JARVES is off"** and "What to do next: JARVES is not running" while the Core was running (and the Skills page showed a "snapshot N min old"). `health.json` was written only when a component changed; the dashboard calls a snapshot older than 120 s "off". | `Core._health_loop` (every 20 s) now also calls `health.save()` (heartbeat). `core/src/jarves/app.py` | `health.json` mtime advances every 20 s; Home status follows. |
| D-02 | P2 | Dashboard server | Normal use returned **429 Too Many Requests** (static JS/CSS, `/api/*`, a half-loaded page with `text/plain` CSS): every page load fetches ~30 static files that all counted against the 240/min budget. | `GET /static/*` no longer uses the rate budget (API calls still do). `dashboard.py`; test `test_static_files_do_not_use_the_rate_budget` | 12 static GETs at limit 5 = 200, API still 429 after 5. |
| D-03 | P3 | Every page | Console error on every load: **404 /favicon.ico**. | Inline SVG icon in `index.html` and `pair.html` (CSP already allows `data:` images). | No console error on any page. |
| D-04 | P2 | Conversations, Home, Issues, Costs | Clickable table rows (`tr.click`) opened only with the mouse: not focusable, no Enter/Space. | `shell.js` gives every `tr.click` `tabindex=0`, `role=link`, Enter/Space activation, and `shell.css` a focus ring. | Tab to a row, Enter opens `#/session/...`. |
| D-05 | P2 | Settings | After **Save changes** the **Undo last save** button stayed disabled until a reload. | `pages.js`: enable Undo after a successful save. | Save, then Undo restores `config.toml` byte for byte. |
| D-06 | P2 | Controls & runtime (390 px, English + Arabic) | The Windows/Pi capability table was 426 px wide: the whole page scrolled sideways. | Wrapped in `.tscroll` (`app.js`). | scrollWidth 390. |
| D-07 | P3 | Costs (390 px, all languages) | 30-day chart and its date labels were 3 px wider than the screen. | `min-width` 6 -> 3 px for bars and labels (`style.css`). | scrollWidth 390. |
| D-08 | P2 | Scheduled jobs, Self-check (English/Arabic UI) | The Core words schedules and date stamps in Hebrew ("בכל יום ראשון ב-10:00", "next: יום א' 25.10.2026 10:00", "Now יום ג' ..."): Hebrew text and bidi-mangled dates inside the English page; job name "בדיקה עצמית שבועית / weekly self-check" in both languages. | `m3.js` `schedHuman` / `schedStamp` / `biName` produce English from the cron spec and the stamp in non-Hebrew UIs; Hebrew UI keeps the Core text; Hebrew jobs header sentence added. | English Jobs/Self-check: no Hebrew left except the "ימי חול" term in the workdays select (deliberate). |
| D-09 | P3 | Hebrew UI | Context page tabs "Blocks"/"Timeline", "← transcript", token tiles; Browser page rules; Backup/Usage/Calendar/Devices/Workspace labels were English. | 40 catalog entries + 6 patterns in `en.js`/`he.js` (locale tests pass). | Hebrew context page. |

## Open (P3, listed; none blocks the exit criteria)

| ID | Sev | Where | Symptom | Suggested fix |
|---|---|---|---|---|
| D-10 | P3 | Hebrew UI | About 190 multi-word English strings remain on non-log pages (counts per page in the report): runtime Pi evidence notes, Features evidence/Pi notes, Settings intro and "Switches" card (its pieces also order badly in RTL), Skills/health details from the Core, Costs price lines and notes, Usage labels, Workspace file descriptors with "used in:", Setup feature descriptions, Identity phrase table. Most come from the Core in English or are composed from pieces. | Add `reason_he`-style siblings in the Core payloads and catalog patterns. |
| D-11 | P3 | English/Arabic UI | Core messages meant to be spoken stay Hebrew on pages: Calendar "Google not connected...", Features/Guides reason `tell the user: "<Hebrew>"`, Alerts titles/bodies, Autonomy proposal texts. | Show the English sibling by UI language. |
| D-12 | P3 | Arabic, Russian | Partial by owner decision (Arabic 17-430 untranslated strings per page); RTL direction, layout and fallback to English are correct. | Later translation pass. |
| D-13 | P3 | Self-check | Finding cards: uneven columns (cause column squeezed to a narrow strip, "Suggested fix" wraps in 5 lines, Accept buttons at different positions) on 1440 px. | Grid with fixed columns. |
| D-14 | P3 | System log | 54 links all named "conversation": no accessible distinction for screen readers. | `aria-label` with time + id. |
| D-15 | P3 | Files | Granting a non-existent folder answers 403 (console error) instead of 400 with the message. | Validation status 400. |
| D-16 | P3 | Phone, Workspace "Earlier versions" | Version rows are cramped (date wraps over 3 lines). | Card layout like the other tables. |
| D-17 | P3 | Hebrew desktop header | The status-chip row is clipped at the left edge ("לא נשמ") and scrolls horizontally with no visible cue. | Wrap chips or show a fade. |
| D-18 | P3 | Docs / wording | The dashboard link token is called "one-time" but is valid for the whole run (reusable). Costs/Usage "Unknown / Unknown" is truthful but terse. | Reword. |

## Environment notes (not product bugs)

- E-1: at 10:41-10:54 every `jarves.exe` on the machine (this Core, this dashboard and other agents' Cores) disappeared with no log line: killed from outside (no WARN/ERROR, no `CORE_STOPPING`). The next start's self-check correctly reported "JARVES stopped without a clean shutdown 1x". My Core and dashboard were restarted; later the dashboard also had one refused connection (a restart by another agent's cleanup is the likely cause).
- E-2: the test venv has no `--extra voice/tray/camera`, so Self-check shows "speech_out unavailable (winrt)", "Chimes / Wake word missing from this install", "camera: OpenCV not installed" (all truthful for this venv).

## Not testable in this pass (BLOCKED, with reason)

- Live mic/camera strip states and the **What does he see?** preview: camera feature off and OpenCV not installed in the offline TEST profile; the button is correctly disabled, the strip shows Off / No camera / No speaker / No audio sent. Needs Pass E (real hardware).
- Setup-wizard actions that install add-ons, Google/Telegram connections: need accounts (Pass E); pages render and toggles work.
- Per-conversation Context with a real captured trace: used a **synthetic trace** seeded into the test profile (6 tabs open in 3 languages x 2 widths); a real captured conversation is Pass C.
- Autonomy proposal Approve/Reject buttons were viewed, not clicked (they change calibration).
