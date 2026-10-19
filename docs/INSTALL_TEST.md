# JARVES Windows installer — owner test / בדיקת המתקין

Short checklist for testing `JARVES-Setup-<version>.exe` on a Windows PC (ideally a **second PC** without Python,
uv or Godot). About 15 minutes. / רשימה קצרה לבדיקת המתקין, רצוי במחשב שני בלי Python/Godot. כ-15 דקות.

## 1. What to download / מה להוריד

- **After the merge to `main`:** GitHub → *Releases* → the newest `JARVES <version> (Windows)` →
  `JARVES-Setup-<version>.exe` (+ `.sha256`). Only the newest 3 builds are kept.
- **Before that:** GitHub → *Actions* → the run → *Artifacts* → `jarves-windows-installer-main` (zip; contains
  the same files). The repository is private, so you must be signed in to GitHub. A test build that passed all
  CI checks: https://github.com/igalgerman/JARVIS/actions/runs/39290421823 (artifact kept until 2 Nov 2026).

- **אחרי המיזוג ל-main:** בגיטהאב → Releases → הגרסה העליונה → `JARVES-Setup-<version>.exe`.
- **לפני כן:** Actions → הריצה → Artifacts → `jarves-windows-installer-main`.

Check the file (optional but recommended) / בדיקת קובץ (מומלץ):
```powershell
Get-FileHash .\JARVES-Setup-*.exe     # must equal the SHA256 in the release notes / .sha256 file
```

## 2. Install / התקנה

1. Double-click the file. **The installer is not code-signed yet**, so Windows SmartScreen shows
   *"Windows protected your PC"* → click **More info** → **Run anyway**. (Expected; no admin password is asked.)
   / המתקין עדיין לא חתום: יופיע "Windows protected your PC" → **More info** → **Run anyway**. לא תתבקש סיסמת מנהל.
2. Choose the language, keep the folder (`%LOCALAPPDATA%\Programs\JARVES`), choose tasks:
   *Add jarvis to PATH* (recommended), *Start JARVES when I sign in* (ticked by default; untick if you do not want it), desktop icon (optional). / "הפעלה בכניסה ל-Windows" מסומן כברירת מחדל, אפשר לבטל.
3. At the end leave **"Set up JARVES now"** checked: a window asks how JARVES connects (ChatGPT plan / API key) and,
   for an API key, lets you paste it (`jarvis key`). / בסוף השאר מסומן "Set up JARVES now": בחירת חיבור והדבקת מפתח.

**Expected / צפוי:** Start menu → *JARVES* folder with JARVES, setup, dashboard, doctor, update check, uninstall.

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
