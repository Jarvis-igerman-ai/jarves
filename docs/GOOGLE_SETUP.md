# Connect Google Calendar + Gmail to JARVES (one-time, ~10 minutes)

JARVES uses **your own** free Google Cloud app. Nobody else gets access; the keys stay on your computer in
`%LOCALAPPDATA%\jarves\google\` (Pi: `~/.local/state/jarves/google/`), never in the repository.
Google console menus checked 19 Oct 2026; if a name moved, search for it in the console's top search bar.

What JARVES may do after this: read **all** your calendars; search and read mail (it asks before reading a mail
aloud); write a **draft** and send it **only** after reading it back to you and your approval on the screen.
It cannot delete mail, change labels or edit your calendar.

## עברית בקצרה

1. נכנסים ל-<https://console.cloud.google.com> עם חשבון הג'ימייל שלך. למעלה: **Select a project → New project**,
   שם: `JARVES home` → **Create**. (חינם, לא צריך כרטיס אשראי.)
2. בשורת החיפוש למעלה: **Google Calendar API** → **Enable**. אחר כך **Gmail API** → **Enable**.
3. תפריט (☰) → **Google Auth platform** → **Branding** → **Get started**: שם האפליקציה `JARVES`, אימייל התמיכה = שלך
   → **Next**. ב-**Audience** בוחרים **External** → **Next**. אימייל ליצירת קשר = שלך → **Next** → מסמנים הסכמה →
   **Create**.
4. **Audience** → **Test users** → **Add users** → כותבים את כתובת הג'ימייל שלך → **Save**.
5. **Data Access** → **Add or Remove Scopes** → מסמנים את ארבע ההרשאות (אפשר להדביק בתיבה "Manually add scopes"):
   `calendar.events.readonly`, `calendar.calendarlist.readonly`, `gmail.readonly`, `gmail.compose` → **Update** → **Save**.
6. **Clients** → **Create client** → Application type: **Desktop app**, שם: `JARVES` → **Create**.
   בחלון שנפתח לוחצים **Download JSON** מיד (ייתכן שגוגל לא יציג את הסוד שוב אחר כך). הקובץ נקרא
   `client_secret_....json`.
7. בטרמינל: `jarvis google login --client "%USERPROFILE%\Downloads\client_secret_....json"`
   הדפדפן נפתח: בוחרים את החשבון. גוגל יזהיר **"Google hasn't verified this app"** — זו האפליקציה שלך:
   **Continue**. מסמנים את **כל** ההרשאות → **Continue**. מופיע "Google connected".
8. מפעילים מחדש את JARVES (`jarvis stop` ואז `jarvis`). בדיקה: `jarvis google status`.
   אומרים: "מה יש לי מחר ביומן?", "יש מיילים חשובים?", "תשלח מייל לדנה ש...".

**פעם בשבוע:** כל עוד האפליקציה במצב Testing, גוגל מנתק אחרי 7 ימים. JARVES יגיד "צריך לחבר מחדש את גוגל",
וגם דף הניהול יראה את זה. מריצים שוב `jarvis google login` (בלי `--client`) ומאשרים.
**ניתוק:** `jarvis google logout` (או "Disconnect" בדף הניהול) מבטל את ההרשאה אצל גוגל ומוחק את המפתחות מהמחשב.

## English, short

1. <https://console.cloud.google.com> (your Gmail account) → **Select a project → New project** → `JARVES home` → **Create**.
2. Top search bar → **Google Calendar API** → **Enable**; then **Gmail API** → **Enable**.
3. ☰ Menu → **Google Auth platform** → **Branding** → **Get started**: app name `JARVES`, support email = yours →
   **Audience: External** → contact email → accept the policy → **Create**.
4. **Audience** → **Test users** → **Add users** → your Gmail address → **Save**.
5. **Data Access** → **Add or Remove Scopes** → add `https://www.googleapis.com/auth/calendar.events.readonly`,
   `.../calendar.calendarlist.readonly`, `.../gmail.readonly`, `.../gmail.compose` → **Update** → **Save**.
6. **Clients** → **Create client** → **Desktop app**, name `JARVES` → **Create** → **Download JSON** right away.
   (Do **not** tick "Use this client for an AI-powered agent": that is for Google's hosted MCP servers, not this.)
7. `jarvis google login --client <path to client_secret_....json>` → choose your account → "Google hasn't verified this
   app" → **Continue** (it is your own app) → tick **every** permission → **Continue** → "Google connected".
   Calendar only: add `--calendar-only`.
8. Restart JARVES; check with `jarvis google status` (JSON: `connected`, `scopes`, `last_error`, `message_he`).

**Weekly reconnect:** an app in **Testing** gets refresh tokens that expire after 7 days. JARVES then says
"צריך לחבר מחדש את גוגל" and the dashboard (`/api/google`) shows `needs_reconnect: true`. Run `jarvis google login`
again. Publishing the app ("Publish app", unverified) may remove the weekly expiry, but Google does not clearly
document this for restricted Gmail scopes: UNVERIFIED, try it later with Calendar first if the weekly step annoys you.

**Disconnect:** `jarvis google logout` revokes at Google and deletes the local files. You can also remove JARVES at
<https://myaccount.google.com/connections>.

## Pi

The sign-in needs a browser on the same machine (Google's loopback redirect to `127.0.0.1`; device codes are not
allowed for Gmail/Calendar). Either sign in on the Pi's own screen, or sign in on the PC and copy
`%LOCALAPPDATA%\jarves\google\` (both `client.json` and `token.json`) to `~/.local/state/jarves/google/` on the Pi
(`chmod 600` the files).

## Privacy rules built into the code

- Mail and calendar text is treated as **untrusted**: HTML and hidden text removed, links replaced by "[קישור]",
  length capped; after such text was read, JARVES refuses any action in the same turn (code `TOOL_UNTRUSTED_TURN`).
- Searching mail only lists how many and from whom; JARVES asks before reading a mail.
- Sending: draft first → read back → your approval on the screen → send. Only drafts JARVES wrote in this session.
- Logs hold only codes and permission names, never tokens, addresses, subjects or bodies.

Sources (19 Oct 2026): Gmail scopes <https://developers.google.com/workspace/gmail/api/auth/scopes>;
Calendar scopes <https://developers.google.com/workspace/calendar/api/auth>; desktop OAuth (loopback + PKCE)
<https://developers.google.com/identity/protocols/oauth2/native-app>; consent screen
<https://developers.google.com/workspace/guides/configure-oauth-consent>; Desktop client
<https://developers.google.com/workspace/guides/create-credentials>; 7-day Testing tokens
<https://developers.google.com/identity/protocols/oauth2#expiration>.
