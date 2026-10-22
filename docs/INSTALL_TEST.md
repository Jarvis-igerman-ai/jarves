# JARVES Windows installer — owner test / בדיקת המתקין

Short checklist for testing `JARVES-Setup-<version>.exe` on a Windows PC (ideally a **second PC** without Python,
uv or Godot). About 15 minutes. / רשימה קצרה לבדיקת המתקין, רצוי במחשב שני בלי Python/Godot. כ-15 דקות.

## 1. What to download / מה להוריד

- **After the merge to `main`:** GitHub → *Releases* → the newest `JARVES <version> (Windows)` →
  `JARVES-Setup-<version>.exe` (+ `.sha256`). Only the newest 3 builds are kept.
- **Before that:** GitHub → *Actions* → the run → *Artifacts* → `jarves-installer-<tree>` (zip; contains
  the same files). The repository is private, so you must be signed in to GitHub. A test build that passed all
  CI checks: https://github.com/igalgerman/JARVIS/actions/runs/39290421823 (artifact kept until 2 Nov 2026).

- **אחרי המיזוג ל-main:** בגיטהאב → Releases → הגרסה העליונה → `JARVES-Setup-<version>.exe`.
- **לפני כן:** Actions → הריצה → Artifacts → `jarves-installer-<tree>`.

Check the file (optional but recommended) / בדיקת קובץ (מומלץ):
```powershell
Get-FileHash .\JARVES-Setup-*.exe     # must equal the SHA256 in the release notes / .sha256 file
```

## 2. Install / התקנה

1. Double-click the file. **The installer is not code-signed yet**, so Windows SmartScreen shows
   *"Windows protected your PC"* → click **More info** → **Run anyway**. (Expected; no admin password is asked.)
   / המתקין עדיין לא חתום: יופיע "Windows protected your PC" → **More info** → **Run anyway**. לא תתבקש סיסמת מנהל.
2. Choose the language, keep the folder (`%LOCALAPPDATA%\Programs\JARVES`), choose tasks:
   *Add jarvis to PATH* (recommended), *Start JARVES when I sign in* (**off by default** since SW-V4 L; tick it only if you want it, or later `jarvis autostart on`), desktop icon (optional). / "הפעלה בכניסה ל-Windows" כבוי כברירת מחדל; מדליקים רק אם רוצים.
3. At the end leave **"Set up JARVES now"** checked: the setup wizard (`jarvis setup`, Hebrew first, English on
   request; Enter = the recommended answer) asks, in order: language → connection (ChatGPT plan / API key /
   both) + paste the key or sign in → the assistant's written and spoken name and how he should address you →
   wake phrase ("Hey Jarvis" is the only built-in one; your own phrase is trained later with `jarvis wake train`)
   → voice → **every add-on ON/OFF**, grouped (Core / Voice / Music / Google / Camera & privacy / Automation /
   Dashboard & phone / Updates & backup), each with what it does, cost/privacy and whether it is available on this
   PC (camera found or not, Pear running or not + download link, Google needs `jarvis google start`) → privacy mode
   → monthly API budget → offer to connect Google now or later → summary and "what to do next".
   Camera, face recognition, camera-when-away, sending mail, calendar writes and the phone (LAN) dashboard stay
   **OFF** unless you type yes. Run `jarvis setup` again any time to change a choice (it keeps the others), or use
   the dashboard page **Setup & add-ons**. If you stop half-way, the next `jarvis setup` continues where it stopped.
   / בסוף השאר מסומן "Set up JARVES now": אשף הגדרה בעברית — חיבור ומפתח, שם, מילת הפעלה, קול, ובחירה של כל
   תוסף (פועל/כבוי) עם הסבר וזמינות. מצלמה, זיהוי פנים, שליחת מיילים, כתיבה ליומן ולוח בקרה ברשת כבויים עד
   שמאשרים במפורש. אפשר להריץ שוב `jarvis setup` בכל זמן.

**Expected / צפוי:** Start menu → *JARVES* folder with JARVES, setup, dashboard, doctor, update check, uninstall.

**Check that every add-on is in the install / לבדוק שכל התוספים הותקנו:** `jarvis setup --check-addons` → one line
per add-on and `ADDON CHECK: PASS` (opens no camera or microphone, downloads nothing; CI runs the same check on
every installer build).

**Silent install with your choices / התקנה שקטה עם הבחירות שלך:**
```powershell
JARVES-Setup-<version>.exe /VERYSILENT /SETUPANSWERS=C:\path\answers.json
```
`answers.json` (no keys or passwords in it; an API key is taken from the `OPENAI_API_KEY` environment variable
only with `"api_key_from_env": true`; Google and ChatGPT sign-in always need you at the keyboard):
```json
{"language": "he", "connection": "combined", "display_name": "JARVES", "spoken_name": "ג'ארוויס",
 "address_me": "איגל, בלשון זכר", "wake_model": "hey_jarvis", "voice": "live:vesper",
 "features": {"music": true, "camera": false, "gmail_send": false}, "privacy_mode": false,
 "monthly_budget_usd": 20}
```
Every key is optional (missing = recommended/current). The same file works with `jarvis setup --answers FILE`;
`jarvis setup --defaults` takes every recommended answer without questions.

## 3. Use it / שימוש

| Do / פעולה | Expected / תוצאה צפויה |
|---|---|
| Start menu → **JARVES** | console with the log, then the face/room window opens; "Hey Jarvis" works |
| Start menu → **JARVES doctor** | list of `[ OK ]` / `[FAIL]` lines with what to fix |
| Start menu → **JARVES dashboard** | browser opens the local dashboard (this PC only) |
| new terminal: `jarvis --version` | `JARVES <version> (commit …, built …, channel main)` |
| Start menu → **Check for JARVES updates** | "Up to date" or the new version; installs only if you type `yes` |
| music | needs **Pear Desktop** (separate app, not bundled): `jarvis music open` |

Note: with the private repository the update check says *"releases not reachable"* on a PC without access —
that is the honest answer, not an error of the installer.

## 4. Uninstall / הסרה

Settings → Apps → JARVES → Uninstall (or Start menu → Uninstall JARVES). It asks whether to also delete your data
(memory, settings, saved key) — **No** is the default and keeps it for a reinstall. /
ההסרה שואלת אם למחוק גם את הנתונים; ברירת המחדל "לא" שומרת אותם.

## 5. How to report / איך לדווח

Open a comment on GitHub issue **#17** (or tell the agent) with:
1. Windows version, version line from `jarvis --version`.
2. Which step failed and what you saw (a phone photo of the screen is fine).
3. The output of `jarvis doctor` and the support bundle from `jarvis diag` (redacted, safe to share).
4. Installer log if setup itself failed: `%TEMP%\Setup Log *.txt`.

לדווח בתגובה ב-issue ‎#17: גרסה, איזה שלב נכשל, צילום מסך, פלט `jarvis doctor`, וקובץ `jarvis diag`.

## 6. Updates, rollback, upgrade (SW-V4 L) / עדכון וחזרה לאחור

Automatic (CI, `packaging/smoke_windows.ps1`): clean install with the real defaults -> autostart OFF, no leftovers after uninstall, data kept unless `/PURGE=1`; `-UpgradeFrom <old installer> -Rollback` upgrades from the old numbering with seeded data and rolls back. Your part on the second PC (OL-I1..OL-I8):

| Row | Do | Expected |
|---|---|---|
| OL-I1 | Install the newest installer on a PC that never had JARVES | SmartScreen "More info > Run anyway" (unsigned by decision), no admin prompt; Start menu entry; **no** start at sign-in unless you ticked it |
| OL-I2 | `jarvis setup`, then Start/stop JARVES 5 times | face opens, stops cleanly each time |
| OL-I3 | Dashboard > Updates | shows the version, "Up to date" or the new one |
| OL-I4 | When a newer build exists: **Install update** | signature/SHA256 checked, setup runs silently, JARVES comes back on the new version, memory/settings/conversations present |
| OL-I5 | Dashboard > Updates > **Roll back** (approve on the screen) | previous version returns, data from before the update restored |
| OL-I6 | Uninstall, choose **No** to "delete my data" | no Start-menu entry, no start at sign-in, no `jarvis` on PATH; the data folder stays |
| OL-I7 | Reinstall | your data is picked up again |
| OL-I8 | Uninstall, choose **Yes** | the data folder is gone |

/ עדכון חתום, חזרה לגרסה הקודמת (`jarvis update --rollback`), והסרה שלא משאירה כלום.
