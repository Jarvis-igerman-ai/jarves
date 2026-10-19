# Release hosting in a public releases repo / אחסון גרסאות ההתקנה במאגר ציבורי

Issue #17 (SW-V2 WP-O). The source repository `igalgerman/JARVIS` is private, so another PC can neither download
its releases nor check them for updates. After every green `main` build, `.github/workflows/package.yml` also
publishes the tested installer as a release of a **separate public repository that holds only release files**
(default `igalgerman/jarvis-releases`, **no source code**), together with a **signed `latest.json`**. JARVES on any
PC downloads that file anonymously over HTTPS, checks the signature with a key built into the app, tells you about
a new version, and installs it only after you approve (SHA256 + size checked, backup first).

המאגר `igalgerman/JARVIS` פרטי, ולכן מחשב אחר לא יכול להוריד ממנו גרסה או לבדוק עדכונים. אחרי כל בנייה ירוקה של
`main`, ה-workflow מפרסם את קובץ ההתקנה שנבדק כגרסה (release) במאגר **ציבורי נפרד שמכיל רק קבצי התקנה**
(ברירת מחדל `igalgerman/jarvis-releases`, **בלי קוד מקור**), יחד עם קובץ `latest.json` חתום. ג'ארוויס בכל מחשב
מוריד את הקובץ ב-HTTPS בלי התחברות, בודק את החתימה עם מפתח שבנוי בתוך התוכנה, מודיע על גרסה חדשה, ומתקין רק
אחרי שאישרתם.

**Cost: free.** Public repositories and their release files are free on GitHub (each file must be under 2 GiB; the
installer is ~170 MB). No card, no other account. / **עלות: חינם.** אין כרטיס אשראי ואין חשבון נוסף.

You create the repo, the token and the key; you type every value yourself. Claude never sees the secrets.
את המאגר, הטוקן והמפתח יוצרים **אתם**, ואת כל הערכים מקלידים בעצמכם. Claude לא רואה את הסודות.

## How it works / איך זה עובד

| Where / איפה | What / מה |
|---|---|
| `igalgerman/JARVIS` (private) | code, CI, the private release (unchanged, stays as the fallback) |
| `igalgerman/jarvis-releases` (public) | releases `win-v<version>`, each with `JARVES-Setup-<v>.exe`, `.exe.sha256`, `latest.json`; newest 3 kept |
| Each PC | once a day: `https://github.com/igalgerman/jarvis-releases/releases/latest/download/latest.json` |

- That address is a plain file download, not the GitHub API, so the anonymous API limit (60 per hour) does not
  apply. JARVES checks at most once a day anyway. / זו הורדת קובץ רגילה ולא ה-API, כך שמגבלת 60 בקשות בשעה לא
  רלוונטית; וג'ארוויס בודק לכל היותר פעם ביום.
- CI first creates the release as a **draft**, uploads the 3 files, and only then publishes it as "latest". So a PC
  never sees a release with missing files. A re-run of an older build never replaces a newer release.
- Retention: the newest 3 `win-v*` releases (and their tags) are kept. A release whose **title or notes contain
  `[pinned]`** is never deleted and does not count towards the 3. Releases with other tag names are never touched.
  שמירה: 3 הגרסאות האחרונות. גרסה שבכותרת או בהערות שלה כתוב `[pinned]` לא נמחקת לעולם.

## Security / אבטחה

- `latest.json` = `{"manifest": {version, commit, built, sha256, size, installer, url, ...}, "signature": {...}}`.
  The signature is **Ed25519** over the canonical manifest. The private key exists only as the GitHub secret
  `JARVES_UPDATE_SIGNING_KEY` (and your offline backup). The public key is built into the app
  (`UPDATE_PUBLIC_KEYS` in `core/src/jarves/updates.py`).
- The app refuses an unsigned or wrongly signed manifest ("unavailable", never "update available"). It then checks
  the downloaded installer against the **SHA256 and size inside the signed manifest**, and re-reads the manifest at
  install time. So even a leaked `RELEASES_TOKEN` cannot make a PC install something else. It needs the signing
  key too. Only newer versions are offered.
- The token is fine-grained: **Contents: write on `jarvis-releases` only**. It cannot read the private source repo.
- No GitHub token is ever sent by the app. The check sends no personal data, only one HTTPS download.
- **Installed builds start only a signed update.** A release's own SHA256 / GitHub digest come from the same
  place as the installer, so they catch corruption but not a malicious release. An installed JARVES therefore
  starts the setup only when it was verified against the signed manifest, and it hashes the file again right
  before starting it. Installed builds also ignore the `JARVES_UPDATE_REPO` / `JARVES_RELEASES_REPO` environment
  overrides and the config `public_key`; only the keys built into the app count (those are for dev checkouts).
  גרסה מותקנת מפעילה רק עדכון חתום, ובודקת את ה-SHA256 שוב רגע לפני ההפעלה.
- Until the public key is built into the app, PCs still check the private repo's releases (needs
  `JARVES_UPDATE_TOKEN`) and tell you about a new version, but an installed build does not install it
  automatically (`UPDATE_UNSIGNED`): download it by hand and check `Get-FileHash`. Nothing is trusted without a key.

Why sign at all if it is GitHub? HTTPS proves the file came from github.com, not that our CI made it. Anyone with
write access to the releases repo (or a leaked token) could upload a different installer. The signature closes that.

---

## Step by step / שלב אחר שלב

### 1. Create the public releases repo / יצירת המאגר הציבורי

1. Open https://github.com/new
2. **Owner**: `igalgerman`. **Repository name**: `jarvis-releases`.
3. **Description** (optional): `JARVES Windows installers (release files only)`.
4. Choose **Public**.
5. Turn on **Add a README file** (a release needs at least one commit to attach its tag to).
6. **Create repository**.

פתחו https://github.com/new ← Owner: `igalgerman` ← שם: `jarvis-releases` ← **Public** ← סמנו **Add a README file**
(גרסה צריכה לפחות commit אחד) ← **Create repository**.

Never push source code to this repo. It holds only what CI uploads. / לא מעלים לשם קוד מקור; רק מה שה-CI מעלה.

### 2. Fine-grained token for CI / טוקן מוגבל ל-CI

1. Open https://github.com/settings/personal-access-tokens/new
   (or: your picture → **Settings** → **Developer settings** → **Personal access tokens** → **Fine-grained tokens**
   → **Generate new token**).
2. **Token name**: `jarves-ci-releases`. **Expiration**: 1 year (put a reminder in your calendar), or
   **No expiration** if offered and you prefer.
3. **Resource owner**: `igalgerman`.
4. **Repository access**: **Only select repositories** → choose **`igalgerman/jarvis-releases`** only.
5. **Permissions** → **Repository permissions** → **Contents**: **Read and write**. (**Metadata: Read-only** is
   added automatically.) Leave everything else at **No access**.
6. **Generate token**. Copy it now; GitHub shows it **once**. This value is **RELEASES_TOKEN**.

פתחו https://github.com/settings/personal-access-tokens/new ← שם: `jarves-ci-releases` ← תוקף: שנה ←
**Only select repositories** ← רק `jarvis-releases` ← **Contents: Read and write** ← **Generate token**.
העתיקו מיד; הטוקן מוצג **פעם אחת בלבד**.

### 3. Signing key / מפתח חתימה (on your PC, in the repo folder)

PowerShell, in `C:\work\jarvis`:

```powershell
core\.venv\Scripts\python.exe packaging\release.py keygen --out "$env:USERPROFILE\jarves-update-signing.pem"
```

It writes the **private** key to that file (it refuses to overwrite an existing key) and prints the **public** key
and its key id. / הפקודה כותבת את המפתח **הפרטי** לקובץ ומדפיסה את המפתח **הציבורי**.

- Save the `.pem` file in your password manager. If it is lost, PCs need a new app version to trust a new key.
  שמרו את קובץ ה-`.pem` במנהל הסיסמאות.
- The printed public key is **not secret**. Give it to Claude (or paste it into `UPDATE_PUBLIC_KEYS` in
  `core/src/jarves/updates.py`) and merge, so the next installer trusts it and checks the public repo by default.
  המפתח הציבורי **אינו סודי**: מסרו אותו ל-Claude כדי שייכנס לגרסה הבאה.

### 4. GitHub secrets on the private repo / סודות במאגר הפרטי (values typed by you)

PowerShell, logged in with `gh auth login`. Each `gh secret set` asks "Paste your secret". Paste the value and press
Enter. The values never appear on screen or in the repo.
כל פקודה מבקשת "Paste your secret". הדביקו את הערך ולחצו Enter.

```powershell
gh secret set RELEASES_TOKEN --repo igalgerman/JARVIS
Get-Content -Raw "$env:USERPROFILE\jarves-update-signing.pem" | gh secret set JARVES_UPDATE_SIGNING_KEY --repo igalgerman/JARVIS
gh secret list --repo igalgerman/JARVIS          # names only; both should be listed
```

Optional, only if the releases repo has a different name (public variable, not a secret):

```powershell
gh variable set RELEASES_REPO --repo igalgerman/JARVIS --body "igalgerman/jarvis-releases"
```

Without the secrets, the step logs `Public releases: SKIPPED ...` and succeeds. The private release is still
published, as before. With only one of the two it shows a yellow warning naming the missing one, and also skips.
בלי הסודות השלב כותב `SKIPPED` ומצליח; הגרסה במאגר הפרטי ממשיכה להתפרסם כרגיל.

### 5. Test / בדיקה

1. Merge a PR to `main` (or re-run the last **jarves-package** run: Actions → jarves-package → Re-run all jobs).
2. Open the run → step **Publish to the public releases repo**. Expected log:
   `Public releases: published win-v<v> in igalgerman/jarvis-releases (... + signed latest.json, key <id>) -> ...`
3. Open https://github.com/igalgerman/jarvis-releases/releases: the release with 3 files, marked **Latest**.
4. Open https://github.com/igalgerman/jarvis-releases/releases/latest/download/latest.json in a browser: it
   downloads JSON with `version`, `sha256`, `url`.

### 6. Another PC / מחשב אחר

1. Download `JARVES-Setup-<v>.exe` from https://github.com/igalgerman/jarvis-releases/releases/latest
   (no GitHub account needed). Check it in PowerShell: `Get-FileHash .\JARVES-Setup-<v>.exe` must equal the SHA256
   in the release notes. Install it (SmartScreen: More info → Run anyway; the installer is not code-signed yet).
2. `jarvis update check` → "Up to date" or "New version ...". `jarvis update install` asks "yes" first.
   Builds made after step 3's public key was added check the public repo with no config change.
3. Custom hosting (optional): any HTTPS folder that serves the same signed `latest.json` works:
   `%LOCALAPPDATA%\jarves\config.toml`:
   ```toml
   [update]
   base_url = "https://example.org/jarves"      # the folder that holds latest.json
   ```

הורידו מ-https://github.com/igalgerman/jarvis-releases/releases/latest (לא צריך חשבון GitHub), בדקו
`Get-FileHash`, התקינו. אחר כך `jarvis update check`. ההתקנה תמיד שואלת קודם.

## Keep / pin a version / שמירת גרסה

Open the release in `jarvis-releases` → **Edit** (pencil) → add `[pinned]` to the title or the notes → **Update
release**. Pinned releases are never deleted and do not count towards the 3.
פתחו את הגרסה ← **Edit** ← הוסיפו `[pinned]` לכותרת או להערות ← **Update release**.

## Rotate or revoke / החלפה או ביטול

- Token leaked or expired: https://github.com/settings/personal-access-tokens → the token → **Regenerate** (or
  **Delete** and create a new one, step 2) → `gh secret set RELEASES_TOKEN --repo igalgerman/JARVIS` again. The
  signature still protects PCs meanwhile.
- Signing key leaked: run `keygen` again, put the new public key in `UPDATE_PUBLIC_KEYS` **and remove the old one**,
  replace the secret `JARVES_UPDATE_SIGNING_KEY`, merge. PCs on an older build trust the old key until they install
  the new build, so install it on each PC by hand once (download + `Get-FileHash`, step 6).
- Planned rotation (no leak): keep both keys in `UPDATE_PUBLIC_KEYS` for one release, then switch the secret.

## Files / קבצים

`.github/workflows/package.yml` (step "Publish to the public releases repo"), `packaging/release.py` (`keygen`,
`public-publish`, Ed25519 signing, retention), `core/src/jarves/updates.py` (public source, signature + SHA256 +
size), `core/src/jarves/config.py` (`[update] base_url`, `public_key`), `core/tests/test_release_hosting.py`
(local fake GitHub API, signing round trip, CI-to-app end to end; no network).
