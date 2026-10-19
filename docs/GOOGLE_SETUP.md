# Connect Google Calendar + Gmail to JARVES (one-time, ~10 minutes)

## The easy way: one command

```
jarvis google start
```

**עברית:** מקלידים `jarvis google start` ועונים Enter. הוא בודק מה כבר מחובר, ואז עובר איתך שלב אחד בכל פעם:
פותח בדפדפן את הדף המדויק בגוגל קלאוד, מעתיק ללוח את מה שצריך להדביק (שם האפליקציה, המייל שלך, ההרשאות),
ואומר על מה ללחוץ. בשלב האחרון לוחצים **Download JSON**: הוא מוצא את הקובץ בתיקיית ההורדות לבד, מעביר אותו
לתיקייה פרטית, מתחבר, בודק שהיומן והמייל עונים, מדליק את היכולות ואומר מה אפשר לבקש.
אפשר לעצור (`q` או Ctrl+C) ולהמשיך אחר כך מאותו מקום. **פעם בשבוע** (מצב Testing): אותה פקודה, והוא רק מתחבר מחדש.

**English:** it checks the current state, then walks one step at a time: opens the exact Google Cloud page, copies
what to paste to the clipboard, lists what to click, watches your Downloads folder for `client_secret*.json` and
takes it (moved to the private folder, never printed), signs you in, runs a read-only live check (calendar list,
Gmail profile), switches on **Google Calendar** and **Read Gmail** (**Send Gmail stays off** unless you add
`--send` or switch it on the Features page) and ends with example phrases. Resumable; safe to run again any time.

| Option | What it does |
|---|---|
| `--calendar-only` | only the Calendar (no Gmail step, no Gmail permission) |
| `--client <file>` | use this client file instead of watching Downloads |
| `--downloads <folder>` | your browser saves somewhere else |
| `--send` | also switch on sending mail (always read back + your approval) |
| `--restart` | forget the saved progress |
| `jarvis google test` | live check only · `jarvis google status` · `jarvis google logout` |

If **gcloud** is already installed, it offers (only after your yes) to create the project and enable both APIs.
The consent screen and the Desktop client have no gcloud command for personal accounts, so those stay in the browser.

**Dashboard:** `jarvis dashboard` → **Connections**: the same steps as a checklist with buttons that open each page,
live status (client file, signed in, calendar/mail check, last problem + how to fix, days until the weekly
reconnect) and **Start / Reconnect / Test / Disconnect**.

**Common problems** (the command prints the fix and opens the right page):

| You see | Fix |
|---|---|
| "Access blocked" / `access_denied` / sign-in never finishes | your address is not a **test user**: Audience → Test users → Add users |
| `redirect_uri_mismatch`, "not a Desktop app client" | create the client with type **Desktop app**, download again |
| `invalid_grant`, "צריך לחבר מחדש" | the weekly Testing-mode expiry: run `jarvis google start` |
| Calendar/Gmail API "not enabled" in the check | the command opens the Enable page for that API |
| some permissions missing | sign in again and tick every box |

The steps below are what the command walks you through, for reference.

JARVES uses **your own** free Google Cloud app. Nobody else gets access; the keys stay on your computer in
`%LOCALAPPDATA%\jarves\google\` (Pi: `~/.local/state/jarves/google/`), never in the repository.
Google console menus checked 19 Oct 2026; if a name moved, search for it in the console's top search bar.

What JARVES may do after this: read **all** your calendars; search and read mail (it asks before reading a mail
aloud); write a **draft** and send it **only** after reading it back to you and your approval on the screen.
It cannot delete mail, change labels or edit your calendar.

## By hand: עברית בקצרה

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
וגם דף הניהול יראה את זה. מריצים שוב `jarvis google start` ומאשרים.
**ניתוק:** `jarvis google logout` (או "Disconnect" בדף הניהול) מבטל את ההרשאה אצל גוגל ומוחק את המפתחות מהמחשב.

## By hand: English, short

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
"צריך לחבר מחדש את גוגל" and the dashboard (`/api/google`) shows `needs_reconnect: true`. Run `jarvis google start`
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
<https://developers.google.com/identity/protocols/oauth2#expiration>; console deep links used by `jarvis google start`
(flows/enableapi, auth/branding, auth/audience, auth/scopes, auth/clients)
<https://developers.google.com/workspace/calendar/api/quickstart/python>.
