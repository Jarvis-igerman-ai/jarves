# Release hosting on Cloudflare R2 / אחסון גרסאות ההתקנה ב-Cloudflare R2

Issue #17 (SW-V2 WP-O). The GitHub repository is private, so another PC can neither download a GitHub Release
nor check it for updates. After every green `main` build, `.github/workflows/package.yml` also uploads the tested
installer to a **public Cloudflare R2 bucket** together with a **signed `latest.json`**. JARVES on any PC reads that
file over HTTPS, checks the signature with a key built into the app, tells you about a new version, and installs it
only after you approve (SHA256 + size checked, backup first).

המאגר ב-GitHub פרטי, ולכן מחשב אחר לא יכול להוריד ממנו גרסה או לבדוק עדכונים. אחרי כל בנייה ירוקה של `main`,
ה-workflow מעלה את קובץ ההתקנה שנבדק לדלי (bucket) ציבורי ב-Cloudflare R2, יחד עם קובץ `latest.json` חתום.
ג'ארוויס בכל מחשב קורא את הקובץ ב-HTTPS, מוודא את החתימה עם מפתח ציבורי שבנוי בתוך התוכנה, מודיע על גרסה חדשה,
ומתקין רק אחרי שאישרת.

Nothing here needs Claude to log in anywhere: **you** create the account and type every value. Claude never sees the
keys. / את החשבון ואת כל הערכים מקלידים **אתם**; Claude לא רואה את המפתחות.

## Cost / עלות (checked 19 Oct 2026, developers.cloudflare.com/r2/pricing)

| Free every month / חינם בכל חודש | Our use / השימוש שלנו |
|---|---|
| 10 GB-month storage (Standard class only) | 3 installers x ~170 MB = ~0.5 GB |
| 1 million Class A operations (upload, list, delete) | ~10 per release |
| 10 million Class B operations (download, read) | a few per PC per day |
| Egress (downloads) free, no cap | |

Beyond the free tier: $0.015/GB-month. Cloudflare may ask for a payment method (card or PayPal) the first time you
open R2, even for the free plan; it charges nothing while usage stays inside the free tier.
ייתכן ש-Cloudflare תבקש אמצעי תשלום בפעם הראשונה שפותחים R2, גם בתוכנית החינמית. לא יחויב כלום כל עוד נשארים
בתוך המכסה החינמית.

## Security design / תכנון האבטחה (why it is safe on a public bucket)

- The bucket is **readable by anyone who knows the URL; only CI can write** (API token scoped to this one bucket).
- `latest.json` = `{"manifest": {version, commit, built, sha256, size, installer, url, ...}, "signature": {...}}`.
  The signature is **Ed25519** over the canonical manifest. The private key exists only as the GitHub secret
  `JARVES_UPDATE_SIGNING_KEY` (and your offline backup); the public key is built into the app
  (`UPDATE_PUBLIC_KEYS` in `core/src/jarves/updates.py`).
- The app refuses an unsigned or wrongly signed manifest ("unavailable", never "update available"), then checks the
  downloaded installer against the **SHA256 and size inside the signed manifest**, re-reading the manifest at
  install time. So even a leaked R2 token, a hijacked domain or a wrong `base_url` cannot make a PC install
  something else. Older versions are never offered (version must be newer).
- The path can be made hard to guess (optional repo variable `R2_PREFIX`, e.g. `jarves/windows-8f3k2q`). That only
  hides the download from strangers; it is **not** a secret and not what keeps you safe; the signature is.
- No GitHub token is ever sent to R2. No personal data is sent: the check is one HTTPS GET of `latest.json`.

Why not just "HTTPS is enough"? HTTPS proves you talk to r2.dev, not that the file was made by our CI. Why not
Sigstore/minisign? They need extra tools on the PC; Ed25519 is already in the app (`cryptography`) and in CI
(`cryptography` or OpenSSL 3), one key, one file.

---

## Step by step / שלב אחר שלב

### 1. Cloudflare account / חשבון Cloudflare

1. Open https://dash.cloudflare.com/sign-up → email + password → **Create Account**.
   פתחו https://dash.cloudflare.com/sign-up ← אימייל וסיסמה ← **Create Account**.
2. Click the link in the verification email. / לחצו על הקישור במייל האימות.
3. You do **not** need to add a website/domain. If the dashboard offers "Add a domain", skip it.
   לא צריך להוסיף אתר או דומיין. אם מוצע "Add a domain", דלגו.

### 2. Turn on R2 / הפעלת R2

1. In the left menu: **Storage & databases → R2 Object Storage** (older menus: **R2**).
   בתפריט השמאלי: **Storage & databases ← R2 Object Storage**.
2. If asked, choose the free plan and add a payment method (see "Cost"). / אם מתבקש, בחרו בתוכנית החינמית והוסיפו
   אמצעי תשלום (ראו "עלות").

### 3. Create the bucket / יצירת הדלי

1. **Create bucket**. Name: `jarves-releases` (lowercase). Location: **Automatic**.
   **Default storage class: Standard** (the free tier does not cover Infrequent Access) → **Create bucket**.
   **Create bucket** ← שם: `jarves-releases` ← מיקום: **Automatic** ← סוג אחסון: **Standard** ← **Create bucket**.

### 4. Public read access / גישת קריאה ציבורית

Pick one / בחרו אחת:

- **r2.dev (simplest, no domain needed)**: bucket → **Settings** → **Public Development URL** → **Enable** → type
  `allow` → **Allow**. Copy the URL shown, like `https://pub-0123abcd....r2.dev`.
  It is rate-limited and meant for light use; fine for a few home PCs.
  דלי ← **Settings** ← **Public Development URL** ← **Enable** ← הקלידו `allow` ← **Allow**. העתיקו את הכתובת
  `https://pub-....r2.dev`. מתאים לכמה מחשבים בבית.
- **Custom domain (if you have a domain on Cloudflare)**: bucket → **Settings** → **Custom Domains** → **Add** →
  e.g. `releases.example.com` → **Continue** → **Connect Domain**; wait for **Active**. Use `https://releases.example.com`.
  דומיין משלכם (אם יש דומיין ב-Cloudflare): **Settings ← Custom Domains ← Add**, חכו ל-**Active**.

This URL (without a trailing `/`) is **R2_PUBLIC_BASE**. / הכתובת הזאת (בלי `/` בסוף) היא **R2_PUBLIC_BASE**.

### 5. Account ID / מזהה החשבון

R2 overview page → right side **Account Details** → **Account ID** → copy. (It is also the first part of the S3 API
address `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`.) This is **R2_ACCOUNT_ID**.
בעמוד הראשי של R2, בצד ימין **Account Details ← Account ID** ← העתיקו. זה **R2_ACCOUNT_ID**.

### 6. API token for CI (this bucket only) / טוקן API ל-CI (רק לדלי הזה)

1. R2 overview → **Account Details** → **API Tokens: Manage** → **Create Account API token**
   (or **Create User API token**).
2. Name: `jarves-ci`. **Permissions: Object Read & Write**. **Specify bucket(s): Apply to specific buckets only →
   `jarves-releases`**. TTL: Forever (or a date you will remember to renew). Client IP filtering: leave empty
   (GitHub's runners change IPs). → **Create API Token**.
3. The next page shows the values **once**. Copy:
   - **Access Key ID** → **R2_ACCESS_KEY_ID**
   - **Secret Access Key** → **R2_SECRET_ACCESS_KEY**
   (Ignore "Token value" and the endpoint; they are not needed.)

שם: `jarves-ci` ← הרשאות: **Object Read & Write** ← **Apply to specific buckets only** ← `jarves-releases` ←
**Create API Token**. בעמוד הבא הערכים מוצגים **פעם אחת בלבד**: העתיקו **Access Key ID** ו-**Secret Access Key**.

### 7. Signing key / מפתח חתימה (on your PC, in the repo folder)

PowerShell, in `C:\work\jarvis`:

```powershell
core\.venv\Scripts\python.exe packaging\release.py keygen --out "$env:USERPROFILE\jarves-update-signing.pem"
```

It writes the **private** key to that file (it refuses to overwrite an existing key) and prints the **public** key
and its key id. / הפקודה כותבת את המפתח **הפרטי** לקובץ ומדפיסה את המפתח **הציבורי**.

- Save the `.pem` file in your password manager (if it is lost, PCs need a new app version to trust a new key).
  שמרו את קובץ ה-`.pem` במנהל הסיסמאות.
- The printed public key is **not secret**: give it to Claude (or paste it into `UPDATE_PUBLIC_KEYS` in
  `core/src/jarves/updates.py`) and commit, so the next installer trusts it. Until then hosted updates report
  "unavailable" on purpose. / המפתח הציבורי **אינו סודי**: מסרו אותו ל-Claude כדי שייכנס לגרסה הבאה.

### 8. GitHub secrets / סודות ב-GitHub (values typed by you)

PowerShell, logged in with `gh auth login`. Each `gh secret set` asks "Paste your secret" — paste the value and press
Enter. The values never appear on screen or in the repo.
כל פקודה מבקשת "Paste your secret" — הדביקו את הערך ולחצו Enter.

```powershell
gh secret set R2_ACCOUNT_ID        --repo igalgerman/JARVIS
gh secret set R2_ACCESS_KEY_ID     --repo igalgerman/JARVIS
gh secret set R2_SECRET_ACCESS_KEY --repo igalgerman/JARVIS
gh secret set R2_BUCKET            --repo igalgerman/JARVIS     # jarves-releases
gh secret set R2_PUBLIC_BASE       --repo igalgerman/JARVIS     # https://pub-....r2.dev (no trailing /)
Get-Content -Raw "$env:USERPROFILE\jarves-update-signing.pem" | gh secret set JARVES_UPDATE_SIGNING_KEY --repo igalgerman/JARVIS
gh secret list --repo igalgerman/JARVIS                        # names only; all 6 should be listed
```

Optional, public (not secret) repo variables / משתנים אופציונליים, לא סודיים:

```powershell
gh variable set R2_PREFIX --repo igalgerman/JARVIS --body "jarves/windows"        # or a hard-to-guess folder
gh variable set JARVES_UPDATE_BASE_URL --repo igalgerman/JARVIS --body "https://pub-....r2.dev/jarves/windows"
```

`JARVES_UPDATE_BASE_URL` = `R2_PUBLIC_BASE` + `/` + prefix (default prefix `jarves/windows`). When set, every new
installer checks R2 by default, so other PCs need no config edit.
כשמוגדר, כל מתקין חדש בודק עדכונים ב-R2 מעצמו, בלי לערוך הגדרות במחשבים האחרים.

Without the secrets the R2 step logs `R2 hosting: SKIPPED` and the GitHub Release is still published (fallback).
With only some of them it shows a yellow warning naming the missing ones and also skips.

### 9. Test / בדיקה

1. Merge a PR to `main` (or re-run the last **jarves-package** run: Actions → jarves-package → Re-run all jobs).
2. Open the run → step **Host on Cloudflare R2**. Expected log:
   `R2 hosting: uploaded JARVES-Setup-<v>.exe (...) + signed latest.json (key <id>) -> https://.../latest.json`.
3. Open `https://<R2_PUBLIC_BASE>/jarves/windows/latest.json` in a browser: JSON with `version`, `sha256`, `url`.
4. In Cloudflare → R2 → `jarves-releases` → **Objects**: the installer, its `.sha256`, `latest.json`.

### 10. Another PC / מחשב אחר

1. Download the installer from the `url` inside `latest.json`. Check it in PowerShell:
   `Get-FileHash .\JARVES-Setup-<v>.exe` must equal `sha256` in `latest.json`. Install (SmartScreen: More info → Run
   anyway; the installer is not code-signed yet).
2. If the build has no default source (variable `JARVES_UPDATE_BASE_URL` was not set), add to
   `%LOCALAPPDATA%\jarves\config.toml`:
   ```toml
   [update]
   base_url = "https://pub-....r2.dev/jarves/windows"
   ```
3. `jarvis update check` → "Up to date" or "New version ...". `jarvis update install` asks "yes" first.
   הורידו מהקישור שב-`latest.json`, בדקו `Get-FileHash`, התקינו. אחר כך `jarvis update check`.

## Keep / pin a version / שמירת גרסה

R2 keeps the newest 3 installers. To keep an older one as a rollback point, create a text file `pinned.txt` with one
version per line (e.g. `0.1.0.57`) and upload it to the same folder (bucket → Objects → `jarves/windows/` →
**Upload**). Pinned versions are never deleted and do not count towards the 3. Nothing outside
`<prefix>/JARVES-Setup-*` is ever deleted.
הדלי שומר את 3 הגרסאות האחרונות. כדי לשמור גרסה ישנה, העלו `pinned.txt` עם מספר גרסה בכל שורה.

## Rotate or revoke / החלפה או ביטול

- R2 token leaked: Cloudflare → R2 → Manage API tokens → **Delete** → create a new one (step 6) → `gh secret set` again.
  The signature still protects PCs meanwhile.
- Signing key leaked: run `keygen` again, put the new public key in `UPDATE_PUBLIC_KEYS` **and remove the old one**,
  replace the secret `JARVES_UPDATE_SIGNING_KEY`, merge. PCs still on an older build trust the old key until they
  install the new build, so install it on each PC by hand once (download + `Get-FileHash`, step 10).
- Planned rotation (no leak): keep both keys in `UPDATE_PUBLIC_KEYS` for one release, then switch the secret.

## Files / קבצים

`.github/workflows/package.yml` (step "Host on Cloudflare R2"), `packaging/release.py` (`keygen`, `r2-publish`,
SigV4 S3 client, Ed25519 signing, retention), `core/src/jarves/updates.py` (hosted source, signature + SHA256 +
size), `core/src/jarves/config.py` (`[update] base_url`, `public_key`), `core/tests/test_release_hosting.py`
(AWS SigV4 test vectors, local S3 stub, signing round trip; no Cloudflare).
