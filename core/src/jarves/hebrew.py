"""Hebrew texts for the SERVER-supplied detail strings the dashboard shows (SW-V3 P0.2b, issue #17).

The first additional server locale (registered in locales.py, which adds the `<field>_he` siblings). English stays
the source of truth and the default: every module keeps its English text and the API adds `<field>_he` next to it
(about_he, label_he, note_he, title_he ...). The dashboard picks `_he` in Hebrew mode and falls back to the English
field when there is none (`txh` / `txo` in dashboard_static/i18n.js). Another language = a module like this one
with `he()` -> its own function, plus one line in locales.TRANSLATORS.

Only OUR OWN fixed texts are translated here (feature descriptions, platform evidence and blockers, probe reasons,
settings labels, usage meter descriptions, the rule-based issue titles and details, voice descriptions). User
content, quoted speech, logs, file names, commands and code are never translated: dynamic rules keep them as they are.
"""
from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------------- exact texts (English -> Hebrew)
TEXT: dict[str, str] = {
    "one wizard in the dashboard (#/setup) that sets up, tests and teaches every topic; your 'works' answers become the per-feature 'verified by owner' record and the release sign-off sheet":
        "אשף אחד בלוח הבקרה (#/setup) להגדרה, בדיקה והיכרות עם כל נושא. התשובות שלכם מתעדות מה אומת על ידכם ומעדכנות את גיליון אישור הגרסה.",
    "core/tests/test_wizard.py, test_wizard_browser.py (done per release, diff, secrets, bug report, sign-off, real browser)":
        "core/tests/test_wizard.py, test_wizard_browser.py (סיום לפי גרסה, שינויים, סודות, דיווח באג, אישור, דפדפן אמיתי)",
    # --- automatic updates (issue #17)
    "installs a newer signed JARVES by itself, only at a quiet moment (never in a conversation, an approval or a "
    "coding-agent task), after a notice and a backup; same signed path as `jarvis update install`; stable users get "
    "stable, beta users get beta; one command goes back to the previous version":
        "מתקין לבד ג'רביס חדש וחתום, רק ברגע שקט (לא באמצע שיחה, אישור או משימת קוד), אחרי הודעה וגיבוי; אותו מסלול "
        "חתום כמו `jarvis update install`; משתמשי יציב מקבלים יציב ומשתמשי בטא מקבלים בטא; פקודה אחת חוזרת לגרסה הקודמת",
    "core/tests/test_auto_update.py (fake signed release server, fake installer: on/off, quiet moment, notices, signed "
    "only, older refused, failure keeps the old version, back-off, channel); never run against a real installer or a "
    "published release":
        "core/tests/test_auto_update.py (שרת מהדורות חתום מדומה ומתקין מדומה: הפעלה/כיבוי, רגע שקט, הודעות, חתום בלבד, "
        "גרסה ישנה נדחית, כישלון משאיר את הגרסה הקודמת, השהיה בין ניסיונות, ערוץ); לא הורץ מול מתקין אמיתי או גרסה שפורסמה",
    # --- beta channel and release sign-off (continuous release)
    "gets every green main build automatically as a signed beta (latest-beta.json) instead of waiting for a tested "
    "stable release; one command or button goes back to stable, and the previous version can always be restored":
        "מקבל אוטומטית כל בנייה ירוקה של main כבטא חתומה (latest-beta.json) במקום לחכות לגרסה יציבה שנבדקה; "
        "פקודה אחת או כפתור אחד חוזרים ליציב, ותמיד אפשר לשחזר את הגרסה הקודמת",
    "the owner's checklist for a stable release: test the latest beta by hand, record the results, sign off; a stable tag is refused without it":
        "רשימת הבדיקה של הבעלים לגרסה יציבה: בודקים את הבטא האחרונה ידנית, רושמים תוצאות וחותמים; בלי זה תג יציב נדחה",
    "core/tests/test_release_channels.py (channel selection, signed beta manifest, beta label); the first live beta needs the owner to enable ENABLE_BETA_PUBLISH":
        "core/tests/test_release_channels.py (בחירת ערוץ, מניפסט בטא חתום, תווית בטא); הבטא החיה הראשונה דורשת מהבעלים להפעיל את ENABLE_BETA_PUBLISH",
    "core/tests/test_release_signoff.py (sheet round trip, sign-off, gate)":
        "core/tests/test_release_signoff.py (מחזור גיליון, חתימה, שער)",
    "there is no Pi package to install": "אין עדיין חבילה להתקנה על ה-Pi",
    "release tooling runs on the owner's PC": "כלי השחרור רצים על המחשב של הבעלים",
    # --- self-management (self_manage.py)
    "I can show my own status (connections, features, skills, memory, files) and, only with your approval on screen, "
    "change my files, memory, features, skills and settings, with undo; secrets and safety switches are out of reach":
        "אני יכול להראות את המצב שלי (חיבורים, יכולות, כלים, זיכרון, קבצים) ורק באישור שלך על המסך לשנות את הקבצים, "
        "הזיכרון, היכולות, הכלים וההגדרות שלי, עם ביטול; סודות ומתגי בטיחות מחוץ להישג ידי",
    "jarvis manage status|file|memory|feature|skill|setting|connect|history|undo, jarvis undo, "
    "dashboard Self-management page":
        "jarvis manage status|file|memory|feature|skill|setting|connect|history|undo, jarvis undo, עמוד ניהול עצמי בלוח הבקרה",
    "core/tests/test_self_manage.py (fake approvals, no network, no browser)":
        "core/tests/test_self_manage.py (אישורים מדומים, בלי רשת, בלי דפדפן)",
    "pure Python; the guided connection window is Windows only so far":
        "פייתון טהור; חלון החיבור המודרך עובד כרגע רק ב-Windows",
    # --- feature descriptions (features.py and the modules that register features)
    "play / pause / volume in Pear Desktop (YouTube Music)": "ניגון, השהיה ועוצמת קול ב-Pear Desktop (YouTube Music)",
    "answers with fresh facts from the web": "עונה עם מידע עדכני מהאינטרנט",
    "longer research runs while the conversation goes on": "מחקר ארוך שרץ ברקע בזמן שהשיחה ממשיכה",
    "keeps durable facts after a conversation (local rules, no network)":
        "שומר עובדות קבועות אחרי שיחה (כללים מקומיים, בלי רשת)",
    "also asks the backend model to extract facts after a conversation (one paid request)":
        "מבקש גם מהמודל לחלץ עובדות אחרי שיחה (בקשה אחת בתשלום)",
    "after each conversation one small model request looks for problems the rules miss":
        "אחרי כל שיחה בקשה קטנה למודל מחפשת בעיות שהכללים מפספסים",
    "read and add calendar events": "קריאה והוספה של אירועים ביומן",
    "search contact names and email addresses, read-only; separate Contacts consent and People API required":
        "חיפוש שמות וכתובות מייל באנשי הקשר, לקריאה בלבד; דורש הסכמה נפרדת ל-Contacts והפעלת People API",
    "read your e-mail": "קריאת המיילים שלך",
    "send e-mail in your name": "שליחת מייל בשמך",
    "look through the camera when asked": "מסתכל דרך המצלמה כשמבקשים",
    "legacy setting; camera opens only on request": "הגדרה ישנה; המצלמה נפתחת רק לפי בקשה",
    "recognise who is in the room": "מזהה מי נמצא בחדר",
    "watch the room while nobody is home": "משגיח על החדר כשאף אחד לא בבית",
    "shows the camera picture on this screen while you keep it open; local only, nothing saved or sent":
        "מציג את תמונת המצלמה במסך הזה כל עוד התצוגה פתוחה; מקומי בלבד, שום דבר לא נשמר ולא נשלח",
    "listens on this device for \"Hey Jarvis\"; no audio leaves before it":
        "מאזין במכשיר הזה ל\"Hey Jarvis\"; שום שמע לא יוצא לפני כן",
    "says hello when the face starts": "אומר שלום כשהפנים עולות",
    "soft sound when \"Hey Jarvis\" is heard and when the conversation is ready":
        "צליל עדין כששומעים \"Hey Jarvis\" וכשהשיחה מוכנה",
    "local web dashboard (`jarvis dashboard`)": "לוח הבקרה המקומי בדפדפן (`jarvis dashboard`)",
    "change SOUL / IDENTITY / USER / MEMORY when you ask (always asks you first)":
        "משנה את SOUL / IDENTITY / USER / MEMORY כשמבקשים (תמיד שואל קודם)",
    "change the speaking voice when you ask (\"תחליף קול\")": "מחליף את קול הדיבור כשמבקשים (\"תחליף קול\")",
    "create and change events after reading them back to you; never deletes (needs a new Google sign-in with the events permission)":
        "יוצר ומשנה אירועים אחרי שהוא מקריא אותם לך; לא מוחק אף פעם (דורש התחברות חדשה לגוגל עם הרשאת אירועים)",
    "open the dashboard from a paired phone on your home Wi-Fi (pairing code + login; never on the internet). Off = this computer only":
        "פותח את לוח הבקרה מטלפון מותאם ברשת הביתית (קוד התאמה והתחברות; אף פעם לא באינטרנט). כבוי = המחשב הזה בלבד",
    "change my written and spoken name (\"קוראים לך...\"); the wake phrase is separate (Identity & voice page)":
        "משנה את השם הכתוב והמדובר שלי (\"קוראים לך...\"); מילת ההפעלה נפרדת (עמוד זהות וקול)",
    "\"show yourself\" / \"hide yourself\" open or close the window": "\"תראה את עצמך\" ו\"תתחבא\" פותחים וסוגרים את החלון",
    "ChatGPT plan and OpenAI API meters; 75/90/100% alerts; Unknown when not reported":
        "מדי התוכנית של ChatGPT וה-API של OpenAI; התראות ב-75/90/100 אחוז; \"לא ידוע\" כשאין דיווח",
    "JARVES may draft a new Skill; you review, approve, grant and enable it here (installs off)":
        "JARVES יכול להציע יכולת חדשה; אתה בודק, מאשר, נותן הרשאות ומפעיל כאן (מותקנת כבויה)",
    "closing the window only hides JARVES; \"Hey Jarvis\" keeps working (Quit in the tray stops him)":
        "סגירת החלון רק מסתירה את JARVES; \"Hey Jarvis\" ממשיך לעבוד (יציאה מהמגש עוצרת אותו)",
    "daily local backup of settings, memory, notes and history (no keys); restore drill":
        "גיבוי מקומי יומי של הגדרות, זיכרון, הערות והיסטוריה (בלי מפתחות); כולל בדיקת שחזור",
    "diagnostics propose calibration changes; applied only with your approval or explicit grant":
        "האבחון מציע שינויי כיול; מופעלים רק באישור שלך או בהרשאה מפורשת",
    "documents JARVES writes, only in its private folder; your folders only after you approve":
        "מסמכים ש-JARVES כותב, רק בתיקייה הפרטית שלו; התיקיות שלך רק אחרי אישור",
    "face / 2D avatar / 3D avatar / hidden, by voice or here; only the picture changes":
        "פנים / דמות דו-ממדית / דמות תלת-ממדית / מוסתר, בקול או כאן; רק התמונה משתנה",
    "keeps what JARVES was given in each conversation (instructions, memory, tools, notes) for the dashboard; only sizes and names while privacy is on":
        "שומר מה ש-JARVES קיבל בכל שיחה (הוראות, זיכרון, כלים, הערות) לתצוגה בלוח הבקרה; במצב פרטיות רק גדלים ושמות",
    "looks for a newer JARVES once a day (GitHub, or the signed release folder in update.base_url; read-only); installs only after you approve, and one command or button goes back to the previous version":
        "בודק פעם ביום אם יש גרסה חדשה (GitHub או תיקיית ההפצה החתומה ב-update.base_url; לקריאה בלבד); מתקין רק באישורך, ופקודה אחת או כפתור אחד חוזרים לגרסה הקודמת",
    "notification-area icon: dashboard, show/hide, mute, pause alerts, status, quit":
        "סמל באזור ההתראות: לוח בקרה, הצגה והסתרה, השתקה, השהיית התראות, מצב ויציאה",
    "now and then suggests one setup step in a short sentence (\"תפסיק להציע\" turns it off)":
        "מדי פעם מציע צעד התקנה אחד במשפט קצר (\"תפסיק להציע\" מכבה)",
    "reminders and news at the right time, with quiet hours and limits":
        "תזכורות וחדשות בזמן הנכון, עם שעות שקט ומגבלות",
    "reminders and recurring reports; missed jobs run once after start-up (asks before activating)":
        "תזכורות ודוחות קבועים; משימות שהוחמצו רצות פעם אחת אחרי ההפעלה (שואל לפני הפעלה)",
    "results of scheduled jobs, read back on request": "תוצאות של משימות מתוזמנות, מוקראות לפי בקשה",
    "starts JARVES invisibly when you log in (Windows: Startup apps; Pi: systemd user service)":
        "מפעיל את JARVES בשקט כשנכנסים למחשב (Windows: אפליקציות הפעלה; Pi: שירות systemd)",
    "your notes: add, search, edit, export as Markdown": "ההערות שלך: הוספה, חיפוש, עריכה וייצוא ל-Markdown",
    "send a message to your own Telegram chat, only after you approve the exact text (you create the bot; the token stays on this computer)":
        "שולח הודעה לצ'אט הטלגרם שלך, רק אחרי שאישרת את הנוסח המדויק (את הבוט יוצרים בעצמכם; האסימון נשאר במחשב הזה)",
    "UNIT test_channels_telegram.py (local fake Bot API server)": "בדיקות יחידה: test_channels_telegram.py (שרת Bot API מדומה מקומי)",
    "never sent through a real bot: needs the owner's BotFather token": "מעולם לא נשלח דרך בוט אמיתי: דרוש אסימון BotFather של הבעלים",
    "plain Python + httpx, no Windows-only code; never run on a Raspberry Pi (no device in the test setup)":
        "Python ו-httpx בלבד, בלי קוד ייעודי ל-Windows; טרם הורץ על Raspberry Pi (אין מכשיר במערך הבדיקה)",
    "calendar today, important unread mail, weather and reminders in one go; parts that are not connected are skipped with a note":
        "היומן של היום, מיילים חשובים שלא נקראו, מזג אוויר ותזכורות בבת אחת; חלקים שלא מחוברים מדולגים עם הערה",
    "UNIT test_channels_briefing.py (local fake Open-Meteo server, real Skill registry)":
        "בדיקות יחידה: test_channels_briefing.py (שרת Open-Meteo מדומה מקומי, רישום יכולות אמיתי)",
    "never run against the real Open-Meteo service or a connected Google account":
        "מעולם לא הורץ מול שירות Open-Meteo האמיתי או חשבון גוגל מחובר",
    "control only the devices you allowed in Home Assistant; locks, garage doors and scripts always ask you first (long-lived token stays on this computer)":
        "שליטה רק במכשירים שאישרת ב-Home Assistant; מנעולים, דלתות חניה וסקריפטים תמיד שואלים קודם (האסימון נשאר במחשב הזה)",
    "UNIT test_channels_home.py (local fake Home Assistant server)": "בדיקות יחידה: test_channels_home.py (שרת Home Assistant מדומה מקומי)",
    "never connected to a real Home Assistant": "מעולם לא חובר ל-Home Assistant אמיתי",
    "drafts a WhatsApp message and opens the chat for you; you press send. He never sends or reads WhatsApp himself":
        "מנסח הודעת WhatsApp ופותח לך את הצ'אט; את השליחה לוחצים בעצמכם. הוא אף פעם לא שולח או קורא WhatsApp בעצמו",
    "UNIT test_channels_whatsapp.py (link building, approval, fake opener)":
        "בדיקות יחידה: test_channels_whatsapp.py (בניית קישור, אישור, פותח מדומה)",
    "never opened with the real WhatsApp app/Web": "מעולם לא נפתח עם אפליקציית WhatsApp או WhatsApp Web האמיתיות",
    # SW-V4 F: real WhatsApp access through the bridge
    "reads your WhatsApp through a separate linked-device bridge on this computer (unofficial; breaks WhatsApp's terms; ban risk you accepted). Read-only first; message text is treated as untrusted and goes to the AI only for messages you ask about":
        "קורא את הוואטסאפ שלך דרך גשר נפרד במחשב הזה שמתחבר כמכשיר מקושר (לא רשמי; מפר את תנאי השימוש של וואטסאפ; "
        "סיכון חסימה שקיבלת). קודם קריאה בלבד; טקסט ההודעות נחשב לא אמין ונשלח ל-AI רק להודעות שביקשת",
    "UNIT/INTEGRATION test_whatsapp_*.py against the local fake bridge (same protocol and policy table as the Go bridge)":
        "בדיקות יחידה ושילוב: test_whatsapp_*.py מול גשר מדומה מקומי (אותו פרוטוקול ואותה טבלת מדיניות כמו גשר ה-Go)",
    "BLOCKED: the real bridge was never linked to the owner's phone (owner step)":
        "חסום: הגשר האמיתי עוד לא קושר לטלפון של הבעלים (שלב של הבעלים)",
    "sends a WhatsApp message you dictated to an allow-listed person, only after you approve the recipient, number and full text on the screen; only in send mode (after 14 quiet days)":
        "שולח הודעת וואטסאפ שהכתבת לאדם שברשימת השליחה, רק אחרי שאישרת על המסך את הנמען, המספר והטקסט המלא; "
        "רק במצב שליחה (אחרי 14 ימים שקטים)",
    "UNIT test_whatsapp_send.py (fake bridge: popup, signed single-use approval, limits)":
        "בדיקות יחידה: test_whatsapp_send.py (גשר מדומה: חלון אישור, אישור חתום חד-פעמי, מגבלות)",
    "BLOCKED: send mode needs the 14-day read-only watch and the owner's go":
        "חסום: מצב שליחה דורש 14 ימי קריאה בלבד ואישור של הבעלים",
    # WhatsApp encrypted archive and search (Issue #17)
    "keeps your WhatsApp messages on this computer for as long as you choose (90 days, a year or forever; 14 days is the default without it) in an encrypted file and finds them fast in Hebrew and English. Only chats you made readable are copied. Trade-off: it is a second copy of your private messages; anyone who is signed in to this Windows account can open it. WhatsApp only syncs limited history to a linked device, so 'forever' means from now on plus what arrived. Nothing goes to the AI except messages you ask about":
        "שומר את הודעות הוואטסאפ שלך במחשב הזה כמה זמן שתבחר (90 יום, שנה או לתמיד; בלעדיו ברירת המחדל 14 ימים) "
        "בקובץ מוצפן, ומוצא אותן מהר בעברית ובאנגלית. מועתקים רק צ'אטים שהגדרת כקריאים. המחיר: זה עותק שני של "
        "ההודעות הפרטיות שלך; מי שמחובר למשתמש ה-Windows הזה יכול לפתוח אותו. וואטסאפ מסנכרן למכשיר מקושר רק "
        "היסטוריה מוגבלת, לכן \"לתמיד\" אומר מעכשיו ועוד מה שהגיע. שום דבר לא נשלח ל-AI חוץ מהודעות שביקשת",
    "UNIT test_whatsapp_archive.py against the local fake bridge (encrypted at rest, Hebrew+English search, retention, wipe on keep-nothing, delete and unlink)":
        "בדיקות יחידה: test_whatsapp_archive.py מול גשר מדומה מקומי (הצפנה בדיסק, חיפוש עברית ואנגלית, שמירה, "
        "מחיקה ב\"לא לשמור כלום\", במחיקת הודעות ובניתוק)",
    "BLOCKED: never run against the real bridge, a real phone history or Windows DPAPI":
        "חסום: מעולם לא הורץ מול הגשר האמיתי, היסטוריית טלפון אמיתית או DPAPI של Windows",
    "this Python has no SQLite full-text search (FTS5) or no cryptography package":
        "ל-Python הזה אין חיפוש טקסט מלא של SQLite (FTS5) או שחסרה חבילת cryptography",
    "the WhatsApp archive cannot be opened": "אי אפשר לפתוח את ארכיון הוואטסאפ",
    "the bridge is not in send mode (read-only: the wa.me handoff sends)":
        "הגשר לא במצב שליחה (קריאה בלבד: שולחים דרך פתיחת הצ'אט ב-wa.me)",
    "the WhatsApp bridge is not running (jarvis whatsapp start)": "גשר הוואטסאפ לא פועל (jarvis whatsapp start)",
    "bridge not running": "הגשר לא פועל",
    "not linked to a phone yet": "עוד לא מקושר לטלפון",
    "mode off": "מצב כבוי",
    "logged out (unlinked on the phone?)": "התנתק (נותק בטלפון?)",
    "send rate limit reached": "הגיע למגבלת השליחה",
    "the bridge store does not decrypt for this Windows user or PC": "המידע של הגשר לא נפתח במשתמש או במחשב הזה",
    "read your Instagram Business/Creator direct messages aloud and reply, only after you approve the exact text (official API; the token stays on this computer)":
        "מקריא בקול הודעות פרטיות באינסטגרם העסקי/יוצר שלך ועונה, רק אחרי שאישרת את הנוסח המדויק (API רשמי; האסימון נשאר במחשב הזה)",
    "UNIT test_instagram.py (local fake Graph API server)": "בדיקות יחידה: test_instagram.py (שרת Graph API מדומה מקומי)",
    "never run against real Instagram: needs the owner's Meta app, account and token":
        "מעולם לא הורץ מול אינסטגרם אמיתי: דרושים אפליקציית Meta, חשבון ואסימון של הבעלים",
    "the Chat page of the dashboard and `jarvis web`: the same silent text conversation as `jarvis chat`, with streaming answers, slash commands, inline approvals and one shared history; paired phones use it too when phone access is on":
        "עמוד השיחה בלוח הבקרה ו-`jarvis web`: אותה שיחת טקסט שקטה כמו `jarvis chat`, עם תשובות שנכתבות בזמן אמת, פקודות /, אישורים בתוך השיחה והיסטוריה אחת משותפת; טלפונים מצומדים משתמשים בה גם כשהגישה מהטלפון פועלת",
    "dashboard Chat page, `jarvis web`, dashboard Phone page (pages per phone)":
        "עמוד השיחה בלוח הבקרה, `jarvis web`, עמוד הטלפון בלוח הבקרה (דפים לכל טלפון)",
    "UNIT test_web_chat.py (fake Core and model, real dashboard and phone HTTP, first-answer-wins approvals, forbidden pages refused server-side, Hebrew round trip)":
        "בדיקות יחידה: test_web_chat.py (ליבה ומודל מדומים, לוח בקרה וטלפון אמיתיים ב-HTTP, התשובה הראשונה לאישור קובעת, דפים אסורים נדחים בשרת, הלוך-חזור בעברית)",
    "never opened in a real browser on the owner's PC or on a real phone": "מעולם לא נפתח בדפדפן אמיתי במחשב של הבעלים או בטלפון אמיתי",
    "pure Python and plain JavaScript; not run on a Raspberry Pi yet (no device in the test setup)":
        "Python ו-JavaScript רגיל בלבד; טרם הורץ על Raspberry Pi (אין מכשיר במערך הבדיקה)",
    "checks his own installation (daily) and his speed (weekly), tells you what is slow or broken and whether it got worse than before; free, read-only, never at night or during a conversation":
        "בודק את ההתקנה שלו (כל יום) ואת המהירות שלו (פעם בשבוע), אומר מה איטי או שבור והאם זה החמיר לעומת בעבר; חינם, קריאה בלבד, אף פעם לא בלילה או באמצע שיחה",
    "dashboard Health & speed page, jarvis selftest, jarvis speedtest, Jobs page (times)":
        "עמוד תקינות ומהירות בלוח הבקרה, jarvis selftest, jarvis speedtest, עמוד המשימות הקבועות (שעות)",
    "UNIT test_selfspeed.py (planted faults, fake clock, fake Core, real dashboard HTTP)":
        "בדיקות יחידה: test_selfspeed.py (תקלות מדומות, שעון מדומה, ליבה מדומה, לוח בקרה אמיתי ב-HTTP)",
    "not yet run on the owner's PC for a week": "טרם הורץ שבוע שלם על המחשב של הבעלים",
    "collects the problems he finds in himself into one list and, only after you approve the exact text, reports them to the developer so the next version fixes them":
        "אוסף את הבעיות שהוא מוצא בעצמו לרשימה אחת, ורק אחרי שאישרת את הנוסח המדויק מדווח עליהן למפתח כדי שהגרסה הבאה תתקן אותן",
    "dashboard Bug reports page, jarvis issues, header badge": "עמוד דיווחי תקלות בלוח הבקרה, jarvis issues, תג בכותרת",
    "UNIT test_issue_registry.py (fake Core, fake GitHub endpoint, planted faults, real dashboard HTTP)":
        "בדיקות יחידה: test_issue_registry.py (ליבה מדומה, שרת GitHub מדומה, תקלות מדומות, לוח בקרה אמיתי ב-HTTP)",
    "no real report filed yet with an owner token": "עוד לא נשלח דיווח אמיתי עם אסימון של הבעלים",
    "pure Python; never run on a Raspberry Pi (no device in the test setup)": "Python טהור; מעולם לא הורץ על Raspberry Pi (אין מכשיר בסביבת הבדיקות)",
    "pure Python; /proc counters; never run on a Raspberry Pi (no device in the test setup)":
        "Python בלבד; מונים מ-/proc; מעולם לא הורץ על Raspberry Pi (אין מכשיר בסביבת הבדיקות)",
    "checks health, recent logs, settings and features on demand and weekly, then suggests ranked fixes; settings change only after you approve":
        "בודק תקינות, יומנים אחרונים, הגדרות ויכולות לפי בקשה ופעם בשבוע, ומציע תיקונים לפי סדר חשיבות; הגדרות משתנות רק אחרי שאישרת",
    "UNIT test_selfcheck.py (real store/log/scheduler/autonomy in tmp dirs, synthetic log files)":
        "בדיקות יחידה: test_selfcheck.py (אחסון, יומן, מתזמן ועצמאות אמיתיים בתיקיות זמניות, קובצי יומן סינתטיים)",
    "not yet run on the owner's real logs and Core": "טרם הורץ על היומנים וה-Core האמיתיים של הבעלים",
    # SW-V4 O: Self-Diagnostics 2.0
    "watches his own health all the time (free), finds anomalies and regressions with the exact code and log lines, runs nightly checks and a selective AI review every 2 days within a strict cap; repairs only after you approve":
        "עוקב אחרי התקינות שלו כל הזמן (בחינם), מוצא חריגות ונסיגות עם הקוד ושורות היומן המדויקים, מריץ בדיקות לילה ובדיקת AI סלקטיבית כל יומיים בתקציב קשיח; מתקן רק אחרי שאישרת",
    "UNIT test_diag_*.py, test_preflight.py, test_remediation.py, test_ai_review_selection.py, test_canary.py, test_health_api.py (real SQLite, replay harness, fake review client)":
        "בדיקות יחידה: test_diag_*.py, test_preflight.py, test_remediation.py, test_ai_review_selection.py, test_canary.py, test_health_api.py (SQLite אמיתי, הרצה חוזרת של נתונים, בודק מדומה)",
    "not yet soaked 24 h nor run 5 nights on the owner's PC": "טרם רץ 24 שעות ברצף או 5 לילות במחשב של הבעלים",
    "pure Python + SQLite (/proc for process stats); never run on a Raspberry Pi (no device in the test setup)":
        "Python ו-SQLite בלבד (/proc לנתוני התהליך); טרם הורץ על Raspberry Pi (אין מכשיר במערך הבדיקה)",
    "one command, jarvis connect, checks every account (Google, Telegram, Pear, wake word) and walks you through only what is missing; reminds you once a day while Google is not connected":
        "פקודה אחת, jarvis connect, בודקת כל חשבון (גוגל, טלגרם, Pear, מילת ההפעלה) ומדריכה אותך רק במה שחסר; מזכירה פעם ביום כל עוד גוגל לא מחובר",
    "UNIT test_connect.py (real HTTP fake Telegram with getUpdates pairing, fake Pear, fake Google, fake clipboard, tmp Downloads)":
        "בדיקות יחידה: test_connect.py (טלגרם מדומה ב-HTTP אמיתי עם התאמה דרך getUpdates, Pear מדומה, גוגל מדומה, לוח מדומה, תיקיית הורדות זמנית)",
    "never run against the owner's real Telegram, Google or Pear accounts":
        "מעולם לא הורץ מול חשבונות הטלגרם, הגוגל וה-Pear האמיתיים של הבעלים",
    "pure Python + SQLite (/proc/meminfo for memory); never run on a Raspberry Pi (no device in the test setup)":
        "Python ו-SQLite בלבד (/proc/meminfo לזיכרון); טרם הורץ על Raspberry Pi (אין מכשיר במערך הבדיקה)",
    "countdown timers and alarms; the Core rings them aloud even when the window is closed":
        "טיימרים לספירה לאחור ושעונים מעוררים; ה-Core מצלצל בקול גם כשהחלון סגור",
    "pure Python + SQLite + tzdata, no Windows-only code; not run on a Raspberry Pi yet (no device in the test setup); ringing needs the Pi speaker":
        "Python, SQLite ו-tzdata בלבד, בלי קוד ייעודי ל-Windows; טרם הורץ על Raspberry Pi (אין מכשיר במערך הבדיקה); הצלצול צריך את הרמקול של ה-Pi",
    "your goals with steps and progress; a check-in only when you ask; nothing is done or sent for you":
        "היעדים שלך עם צעדים והתקדמות; סיכום התקדמות רק כשאתה מבקש; שום דבר לא נעשה או נשלח בשבילך",
    "preferences you state are remembered only after you confirm; all visible, editable, removable":
        "העדפות שאתה מציין נשמרות רק אחרי שאישרת; הכול גלוי, ניתן לעריכה ולהסרה",
    "core/tests/test_v5_followups.py (unit tests: real SQLite, fake clock, no network)":
        "core/tests/test_v5_followups.py (בדיקות יחידה: SQLite אמיתי, שעון מדומה, בלי רשת)",
    "pure Python + SQLite, no platform code; not run on a Raspberry Pi yet":
        "Python ו-SQLite בלבד, בלי קוד ייעודי לפלטפורמה; טרם הורץ על Raspberry Pi",
    "your confirmed preferences (only those you said yes to) are added to the instructions of every conversation within a small size limit; switch off to stop":
        "ההעדפות שאישרת (רק אלה שאמרת להן כן) מצורפות להוראות של כל שיחה בגודל קטן ומוגבל; כבה כדי להפסיק",
    "when you finish a goal JARVES says one short calm line, only while you are at the PC and never between 22:00 and 07:00; switch off for silence":
        "כשאתה מסיים יעד, JARVES אומר משפט קצר ורגוע אחד, רק כשאתה ליד המחשב ולעולם לא בין 22:00 ל-07:00; כבה לשקט מוחלט",
    "by default JARVES answers only when asked; you can let it offer one item inside a conversation you already started, never outside one and never between 22:00 and 07:00":
        "כברירת מחדל JARVES עונה רק כשמבקשים; אפשר לאפשר לו להציע פריט אחד בתוך שיחה שכבר התחלת, אף פעם לא מחוץ לשיחה ולא בין 22:00 ל-07:00",
    "ideas and tickets for JARVES's own development are kept as local drafts; nothing is posted to GitHub unless you approve it on the screen":
        "רעיונות וכרטיסים לפיתוח של JARVES נשמרים כטיוטות מקומיות; שום דבר לא נשלח ל-GitHub בלי שתאשר על המסך",
    "shows what the coding agents, pull requests and CI of JARVES are doing and suggests the next step; it only reads, and any change on GitHub asks on the screen first":
        "מראה מה הסוכנים, בקשות המשיכה וה-CI של JARVES עושים ומציע את הצעד הבא; הוא רק קורא, וכל שינוי ב-GitHub מבקש קודם אישור על המסך",
    "off by default: when ON, an event (a goal stalling, a calendar event, a reminder) may queue ONE short note for the device you are using; never 22:00-07:00, with snooze, stop, hourly and daily limits; it only tells you, it never does anything and never calls the AI on its own":
        "כבוי כברירת מחדל: כשדלוק, אירוע (יעד שנתקע, אירוע ביומן, תזכורת) יכול להשאיר הודעה קצרה אחת במכשיר שבו אתה משתמש; אף פעם לא בין 22:00 ל-07:00, עם השהיה, עצירה ומגבלות לשעה וליום; היא רק מודיעה, לא עושה דבר ולא קוראת ל-AI מעצמה",
    "turns a goal into a short plan of existing tools; runs only after you approve it, asks before every step that changes something, and can be paused, cancelled or resumed":
        "הופך יעד לתוכנית קצרה של כלים קיימים; רץ רק אחרי שאישרת, שואל לפני כל צעד ששינה משהו, ואפשר להשהות, לבטל ולהמשיך",
    "shopping and to-do lists by voice: add, read back, tick off, clear":
        "רשימות קניות ומשימות בקול: הוספה, הקראה, סימון וניקוי",
    "opens web pages for you: full control only on your allow-list (empty at first), other sites read-only after you approve; purchases, payments and passwords always ask on the screen":
        "פותח בשבילך דפי אינטרנט: שליטה מלאה רק באתרים שברשימת ההיתרים שלך (ריקה בהתחלה), אתרים אחרים לקריאה בלבד אחרי שאישרת; רכישות, תשלומים וסיסמאות תמיד שואלים על המסך",
    "core/tests/test_browser.py drives the installed Edge (headless) against a local test page: allow-list, read-only ask, confirmations":
        "core/tests/test_browser.py מפעיל את Edge המותקן (בלי חלון) מול דף בדיקה מקומי: רשימת היתרים, בקשת קריאה בלבד, אישורים",
    "not yet used in a real spoken conversation": "עוד לא שומש בשיחה קולית אמיתית",
    "Playwright linux-aarch64 wheel + Chromium never run on a Pi 5; RAM/CPU of a headless page not measured there":
        "חבילת Playwright ל-linux-aarch64 ו-Chromium מעולם לא רצו על Pi 5; צריכת הזיכרון והמעבד של דף בלי חלון לא נמדדה שם",
    "runs one command or installs software (winget / pip / npm) only after you approve the exact command on the popup; dangerous ones need a typed code; never as administrator; output is logged":
        "מריץ פקודה אחת או מתקין תוכנה (winget / pip / npm) רק אחרי שאישרת את הפקודה המדויקת בחלון הקופץ; פקודות מסוכנות דורשות הקלדת קוד; אף פעם לא כמנהל מערכת; הפלט נרשם ביומן",
    "UNIT test_helper_approvals.py (real harmless processes in temp folders, real approvals service)":
        "בדיקות יחידה: test_helper_approvals.py (תהליכים אמיתיים ובלתי מזיקים בתיקיות זמניות, שירות אישורים אמיתי)",
    "the approval popup and a real install were not tried by the owner on the desktop":
        "חלון האישור והתקנה אמיתית עוד לא נוסו על ידי הבעלים במחשב",
    "plain Python subprocess + Tk popup; never run on a Raspberry Pi (no device in the test setup)":
        "תהליך משנה של Python וחלון Tk בלבד; טרם הורץ על Raspberry Pi (אין מכשיר במערך הבדיקה)",
    "lets a short list of read-only commands (dir, git status, python --version) run without the approval popup; still logged; everything else still asks":
        "מאפשר לרשימה קצרה של פקודות קריאה בלבד (dir, git status, python --version) לרוץ בלי חלון האישור; עדיין נרשם ביומן; כל השאר עדיין שואל",
    # --- platform blockers
    "never run on a Raspberry Pi 5 (no device yet)": "טרם הורץ על Raspberry Pi 5 (אין עדיין מכשיר)",
    "Pi audio output not tested": "פלט השמע ב-Pi לא נבדק",
    "no real background run checked": "לא נבדקה הרצה אמיתית ברקע",
    "not checked on real sessions": "לא נבדק בשיחות אמיתיות",
    "no real paid extraction checked": "לא נבדק חילוץ אמיתי בתשלום",
    "no real paid review checked": "לא נבדקה סקירה אמיתית בתשלום",
    "owner has not connected Google yet; offline tests with API-shaped fakes only (test_google.py)":
        "הבעלים עוד לא חיבר גוגל; נבדק רק בלי רשת עם תחליפים בצורת ה-API (test_google.py)",
    "owner has not connected Google yet; offline tests with API-shaped fakes only (test_google.py); People API + Contacts consent not checked live":
        "הבעלים עוד לא חיבר גוגל; נבדק רק בלי רשת עם תחליפים בצורת ה-API (test_google.py); People API והסכמת Contacts לא נבדקו בפועל",
    "Google sign-in redirects to a browser on the same device; the Pi needs sign-in from a paired PC or phone (spec section 8), not built":
        "ההתחברות לגוגל פותחת דפדפן באותו מכשיר; ב-Pi צריך להתחבר ממחשב או טלפון מותאם (מפרט, סעיף 8); עוד לא נבנה",
    "picamera2 backend never run on Pi Camera hardware": "המנוע picamera2 מעולם לא רץ על מצלמת Pi אמיתית",
    "no hands-on camera check recorded": "לא תועדה בדיקת מצלמה בפועל",
    "no faces Skill yet": "עוד אין יכולת זיהוי פנים",
    "no away-watch Skill yet": "עוד אין יכולת השגחה כשאין אף אחד בבית",
    "never shown with a real webcam yet": "מעולם לא הוצג עם מצלמת רשת אמיתית",
    "picamera2 preview frames and the round-screen strip layout never run on Pi hardware":
        "תמונות התצוגה של picamera2 ופריסת הפס במסך העגול מעולם לא רצו על חומרת Pi",
    "owner has not reviewed it hands-on yet": "הבעלים עוד לא עבר עליו בפועל",
    "stdlib server; never run as a Pi service": "שרת מספריית התקן; מעולם לא רץ כשירות ב-Pi",
    "never paired with a real phone": "מעולם לא הותאם לטלפון אמיתי",
    "never paired with a real phone on the Pi": "מעולם לא הותאם לטלפון אמיתי ב-Pi",
    "Pear Desktop ARM64 build + API Server plugin playback not yet verified on a Pi":
        "גרסת ARM64 של Pear Desktop עם תוסף ה-API Server עוד לא נבדקה בניגון ב-Pi",
    "pyopen-wakeword linux-aarch64 wheel; Pi mic array/acoustics/CPU not measured":
        "חבילת pyopen-wakeword ל-linux-aarch64; מערך המיקרופונים, האקוסטיקה והעומס על המעבד ב-Pi לא נמדדו",
    "a 19 Oct search stalled ~90 s; not re-verified live":
        "חיפוש ב-19 באוקטובר נתקע כ-90 שניות; לא נבדק שוב בפועל",
    "owner has not compared voices by ear": "הבעלים עוד לא השווה קולות באוזן",
    "rename not yet tried in a real spoken conversation": "שינוי השם עוד לא נוסה בשיחה מדוברת אמיתית",
    "not checked in a real conversation": "לא נבדק בשיחה אמיתית",
    "Godot performance on the Pi is unmeasured (renderer portability, spec 4.2)":
        "הביצועים של Godot ב-Pi לא נמדדו (תאימות המציג, מפרט 4.2)",
    "HKCU Run key written through a fake registry in tests; a real Windows login was not exercised":
        "מפתח ההפעלה ב-HKCU נבדק רק מול רישום מדומה; לא נבדקה כניסה אמיתית ל-Windows",
    # SW-V4 K: the rest of the always-on runtime platform table (Controls & runtime page)
    "fcntl lock tested on CI-style unit tests only, not on a Pi": "נעילת fcntl נבדקה רק בבדיקות יחידה, לא על Pi",
    "dashboard Home page (show/hide, mute, pause alerts, status, quit)":
        "עמוד הבית בלוח הבקרה (הצגה/הסתרה, השתקה, השהיית התראות, מצב, יציאה)",
    "sleep and headset unplug simulated (clock jump, stalled mic); a real sleep/unplug cycle is pending":
        "שינה וניתוק אוזניות נבדקו בסימולציה (קפיצת שעון, מיקרופון תקוע); מחזור שינה/ניתוק אמיתי עוד ממתין",
    "Pi USB mic re-plug untested": "חיבור מחדש של מיקרופון USB ב-Pi לא נבדק",
    "contract test: no voice session or speech while idle in hidden mode; the real wake mic is the user's QA":
        "בדיקת חוזה: אין שיחה קולית ואין דיבור במצב מוסתר ושקט; את מיקרופון ההפעלה האמיתי בודק המשתמש",
    "Pi mic/acoustics untested": "המיקרופון והאקוסטיקה ב-Pi לא נבדקו",
    "no Pi device yet; same code path, systemd user service untested":
        "אין עדיין מכשיר Pi; אותו קוד, שירות systemd לא נבדק",
    "not run on a Raspberry Pi 5 yet: Godot ARM64 fps/RAM on the round display unmeasured":
        "טרם הורץ על Raspberry Pi 5: קצב הפריימים והזיכרון של Godot ARM64 במסך העגול לא נמדדו",
    "popup + speech portable; no OS-notification adapter on Linux/Pi yet (desktop notifications / headless equivalent not chosen)":
        "החלון הקופץ והדיבור ניתנים להעברה; אין עדיין מתאם להתראות מערכת ב-Linux/Pi (עוד לא נבחר פתרון)",
    "pure Python + SQLite + tzdata, no Windows-only code; not run on a Raspberry Pi yet (no device in the test setup)":
        "Python ו-SQLite בלבד, בלי קוד ייעודי ל-Windows; טרם הורץ על Raspberry Pi (אין מכשיר במערך הבדיקה)",
    "pure Python + SQLite + tzdata, no Windows-only code; not run on a Raspberry Pi yet (no device in the test setup); Linux symlink/permission guards covered by tests only on CI":
        "Python ו-SQLite בלבד, בלי קוד ייעודי ל-Windows; טרם הורץ על Raspberry Pi (אין מכשיר במערך הבדיקה); ההגנות של Linux על קישורים והרשאות נבדקות רק ב-CI",
    "pure Python + SQLite, no platform code; not run on a Pi yet": "Python ו-SQLite בלבד, בלי קוד ייעודי לפלטפורמה; טרם הורץ על Pi",
    "pure Python + SQLite, no platform code; not yet run on a Pi": "Python ו-SQLite בלבד, בלי קוד ייעודי לפלטפורמה; טרם הורץ על Pi",
    "silent text chat with JARVES in the terminal (`jarvis chat`): no microphone, no speech, approvals answered in the terminal":
        "שיחת טקסט שקטה עם JARVES בטרמינל (`jarvis chat`): בלי מיקרופון, בלי קול, ואישורים נענים בטרמינל",
    "core/tests/test_chat.py (scripted terminal, fake Core and model); no hands-on run in a real Windows console yet":
        "core/tests/test_chat.py (טרמינל מתוסרט, ליבה ומודל מדומים); טרם הורץ ידנית בקונסולת Windows אמיתית",
    "pure Python over the local link; not run on a Raspberry Pi yet (no device in the test setup)":
        "Python בלבד מעל הקישור המקומי; טרם הורץ על Raspberry Pi (אין מכשיר במערך הבדיקה)",
    "pure Python, no platform code; not run on a Pi yet": "Python בלבד, בלי קוד ייעודי לפלטפורמה; טרם הורץ על Pi",
    "sandbox runner not run on Pi OS; OS-level sandbox (bubblewrap/seccomp) open":
        "מריץ ה-sandbox לא רץ על Pi OS; בידוד ברמת מערכת ההפעלה (bubblewrap/seccomp) עוד פתוח",
    "stdlib zip + SQLite backup API only; SD/SSD restore drill on a Pi not done":
        "רק zip של ספריית התקן ו-API הגיבוי של SQLite; בדיקת שחזור מכרטיס SD או SSD ב-Pi לא בוצעה",
    "systemd user unit generated and tested as a file only": "יחידת systemd נוצרה ונבדקה כקובץ בלבד",
    "the check works, but there is no Pi package to install": "הבדיקה עובדת, אבל אין עדיין חבילה להתקנה ב-Pi",
    # --- platform evidence
    "talk to this computer's JARVES from a paired phone on your home Wi-Fi or over Tailscale (detected, never installed by JARVES): chat, push-to-talk voice (needs TLS), approvals, status. Pairing by QR + one-time code, per-phone tokens you can revoke. Off = this computer only":
        "מדברים עם JARVES של המחשב הזה מטלפון מצומד ברשת הביתית או דרך Tailscale (JARVES רק מזהה אותו ולא מתקין): שיחה, דיבור בלחיצה (דורש TLS), אישורים ומצב. צימוד בקוד QR וקוד חד-פעמי, ואסימון נפרד לכל טלפון שאפשר לבטל. כבוי = המחשב הזה בלבד",
    "talk to this computer's JARVES from a paired phone on your home Wi-Fi: chat, push-to-talk voice (needs TLS), approvals list, status. Pairing by QR + one-time code, per-phone tokens you can revoke. Off = this computer only":
        "מדברים עם JARVES של המחשב הזה מטלפון מצומד ברשת הביתית: שיחה, דיבור בלחיצה (דורש TLS), רשימת אישורים ומצב. צימוד בקוד QR וקוד חד-פעמי, ואסימון נפרד לכל טלפון שאפשר לבטל. כבוי = המחשב הזה בלבד",
    "dashboard Phone page / `jarvis dashboard --mobile`": "עמוד טלפון בלוח הבקרה / `jarvis dashboard --mobile`",
    "UNIT test_mobile_*.py + test_tailnet.py (pairing, tokens, scopes, rate limits, audit, PWA files, TLS, Tailscale with fake interfaces); QR decoded once with OpenCV":
        "בדיקות יחידה: test_mobile_*.py ו-test_tailnet.py (צימוד, אסימונים, הרשאות, הגבלת קצב, ביקורת, קבצי PWA, TLS, Tailscale עם ממשקים מדומים); קוד QR פוענח פעם אחת עם OpenCV",
    "UNIT test_mobile_*.py + test_tailnet.py (pairing, tokens, scopes, rate limits, audit, PWA files, TLS, Tailscale with fake interfaces); QR decoded once with OpenCV; Learned-preferences page (view/confirm/correct/forget, scope prefs) in the emulated phone (test_mobile_prefs.py)":
        "בדיקות יחידה: test_mobile_*.py ו-test_tailnet.py (צימוד, אסימונים, הרשאות, הגבלת קצב, ביקורת, קבצי PWA, TLS, Tailscale עם ממשקים מדומים); קוד QR פוענח פעם אחת עם OpenCV; עמוד ההעדפות הנלמדות (צפייה, אישור, תיקון, שכחה, הרשאת prefs) בטלפון מדומה (test_mobile_prefs.py)",
    "UNIT test_mobile_*.py (pairing, tokens, scopes, rate limits, audit, PWA files, TLS); QR decoded once with OpenCV":
        "בדיקות יחידה: test_mobile_*.py (צימוד, אסימונים, הרשאות, הגבלת קצב, ביקורת, קבצי PWA, TLS); קוד QR פוענח פעם אחת עם OpenCV",
    "V5 is PC-hosted; the Pi is out of scope": "V5 רץ על המחשב; ה-Pi מחוץ להיקף",
    "UNIT": "בדיקות יחידה",
    "UNIT test_dashboard_lan.py (spoofed host/origin, CSRF, rate limit)":
        "בדיקות יחידה: test_dashboard_lan.py (זיוף host/origin, CSRF, הגבלת קצב)",
    "UNIT test_dashboard_wph.py (diagnose.py LLM review)": "בדיקות יחידה: test_dashboard_wph.py (סקירת המודל ב-diagnose.py)",
    "UNIT test_privacy_state.py (fake camera) + headless face screenshots docs/reviews/swv2_privacy/":
        "בדיקות יחידה: test_privacy_state.py (מצלמה מדומה) וצילומי מסך של הפנים בלי חלון: docs/reviews/swv2_privacy/",
    "HANDOFF 'Real YouTube Music playback verified - 18 Oct 2026 (night)'":
        "HANDOFF: ניגון אמיתי ב-YouTube Music אומת ב-18 באוקטובר 2026 (בלילה)",
    "HANDOFF 'Wake word through the air': speakers -> webcam mic, 4/4, CPU 14-25 ms/frame (owner's own voice peaked ~0.17: threshold calibrated)":
        "HANDOFF, \"מילת הפעלה דרך האוויר\": רמקולים אל מיקרופון המצלמה, 4 מתוך 4, מעבד 14-25 מילישניות לפריים (קולו של הבעלים הגיע לכ-0.17: הסף כוייל)",
    "HANDOFF acoustic path: reconnect chime 0.5 s after 'Hey Jarvis'":
        "HANDOFF, נתיב אקוסטי: צליל החיבור נשמע 0.5 שניות אחרי \"Hey Jarvis\"",
    "HANDOFF: owner's real runs 19 Oct heard the greeting": "HANDOFF: בהרצות האמיתיות של הבעלים ב-19 באוקטובר הברכה נשמעה",
    "core/tests/test_backup.py (fault injection, isolated restore); real-profile restore drill by the user pending":
        "core/tests/test_backup.py (הזרקת תקלות, שחזור מבודד); בדיקת שחזור על הפרופיל האמיתי עוד לא בוצעה",
    "core/tests/test_context_trace.py (capture, privacy, retention, routes) + a real silent GPT-Live run (docs/reviews/SWV2_CONTEXT_TRACE.md)":
        "core/tests/test_context_trace.py (לכידה, פרטיות, שמירה, נתיבים) והרצת GPT-Live אמיתית בשקט (docs/reviews/SWV2_CONTEXT_TRACE.md)",
    "core/tests/test_guides.py (scripted conversations, offline)": "core/tests/test_guides.py (שיחות מתוסרטות, בלי רשת)",
    "core/tests/test_m3_*.py (unit + fault injection on Windows); no real-restart run yet":
        "core/tests/test_m3_*.py (יחידה והזרקת תקלות ב-Windows); עוד לא בוצעה הפעלה מחדש אמיתית",
    "core/tests/test_runtime.py": "בדיקות: core/tests/test_runtime.py",
    "core/tests/test_updates.py (fake GitHub), core/tests/test_update_flow.py (local signed release server: ask first, SHA256 + size, failure keeps the old version, downgrade refused, rollback); needs a published release to verify live":
        "core/tests/test_updates.py (GitHub מדומה), core/tests/test_update_flow.py (שרת הפצה חתום מקומי: שואל קודם, SHA256 וגודל, כשל משאיר את הגרסה הקודמת, ירידת גרסה נדחית, חזרה לאחור); כדי לאמת בפועל צריך גרסה שפורסמה",
    "core/tests/test_usage.py (unit + dashboard HTTP); no live plan-limit event observed yet":
        "core/tests/test_usage.py (יחידה ו-HTTP של לוח הבקרה); עוד לא נצפה אירוע מגבלת תוכנית אמיתי",
    "docs/reviews/SWV2_WP-M1.md (real hidden-mode run, pythonw, no window)":
        "docs/reviews/SWV2_WP-M1.md (הרצה אמיתית במצב מוסתר, pythonw, בלי חלון)",
    "headless screenshots docs/reviews/swv2_dashboard/ (not hands-on)":
        "צילומי מסך אוטומטיים docs/reviews/swv2_dashboard/ (לא בדיקה בפועל)",
    "owner hands-on show/hide/close check on the Windows PC, 19 Oct 2026; core/tests/test_runtime.py, core/tests/test_face_protocol_godot.py":
        "הבעלים בדק בפועל הצגה, הסתרה וסגירה במחשב ה-Windows ב-19 באוקטובר 2026; core/tests/test_runtime.py, core/tests/test_face_protocol_godot.py",
    "owner hands-on tray icon and menu check on the Windows PC, 19 Oct 2026; core/tests/test_runtime.py":
        "הבעלים בדק בפועל את סמל המגש והתפריט במחשב ה-Windows ב-19 באוקטובר 2026; core/tests/test_runtime.py",
    "pytest test_appearance + headless Godot --check": "pytest test_appearance ובדיקת Godot --check בלי חלון",
    "pytest test_skill_creation (incl. malicious drafts incl. the string.Formatter getattr escape); child runs in a Job Object (memory + 1-process cap, kill-on-close); code is NOT OS-sandboxed (shown on the approval page); UX not owner-reviewed":
        "pytest test_skill_creation (כולל טיוטות זדוניות והבריחה דרך string.Formatter); התהליך הבן רץ ב-Job Object (תקרת זיכרון ותהליך אחד, נסגר עם האב); הקוד לא מבודד ברמת מערכת ההפעלה (כתוב בעמוד האישור); חוויית השימוש לא נבדקה על ידי הבעלים",
    "user's live session 19 Oct used it (docs/reviews/CONVERSATION_LOG_REVIEW_19OCT.md)":
        "בשיחה החיה של המשתמש ב-19 באוקטובר הוא שימש (docs/reviews/CONVERSATION_LOG_REVIEW_19OCT.md)",
    # --- probe reasons
    "no configuration": "אין הגדרות",
    "AI is offline (ai.provider = offline)": "ה-AI כבוי (ai.provider = offline)",
    "needs the OpenAI API key (connection = ChatGPT plan only)": "דורש מפתח API של OpenAI (החיבור הוא תוכנית ChatGPT בלבד)",
    "no OpenAI API key (run `jarvis key`)": "אין מפתח API של OpenAI (הרץ `jarvis key`)",
    "not checked yet: checking now (look again in a moment)": "עוד לא נבדק: בודק עכשיו (הסתכלו שוב בעוד רגע)",
    "Pear Desktop is not running (start it with the API Server plugin)": "Pear Desktop לא רץ (הפעל אותו עם תוסף ה-API Server)",
    "voice is off (voice.mode = off)": "הקול כבוי (voice.mode = off)",
    "wake word detector unavailable": "מזהה מילת ההפעלה לא זמין",
    "wake-word package not installed (uv sync --extra voice)": "חבילת מילת ההפעלה לא מותקנת (uv sync --extra voice)",
    "no microphone": "אין מיקרופון",
    "no speaker": "אין רמקול",
    "voices can only be changed with live cloud voice (voice.mode = live)": "אפשר להחליף קול רק בקול חי בענן (voice.mode = live)",
    "no camera connected": "אין מצלמה מחוברת",
    "not installed yet (no connector in this version)": "עוד לא מותקן (אין מחבר בגרסה הזו)",
    "provider": "הספק",
    # --- custom wake phrase (capabilities.WAKE_PHRASE)
    "trained on this PC 2026-10-19 from 4 local Windows voices in 435 s (held-out voice: 24/24 heard, 0 false wakes in 20 min of synthetic speech/noise at threshold 0.9): docs/reviews/SWV2_WP-M4.md":
        "אומן במחשב הזה ב-19.10.2026 מ-4 קולות Windows מקומיים תוך 435 שניות (קול שלא שימש לאימון: 24 מתוך 24 זוהו, אפס הפעלות שווא ב-20 דקות של דיבור ורעש סינתטיים, סף 0.9): docs/reviews/SWV2_WP-M4.md",
    "never tried with a real voice and mic: run `jarvis wake test --model ...` and `--false-wakes 30`":
        "מעולם לא נוסה עם קול ומיקרופון אמיתיים: הרץ `jarvis wake test --model ...` ו-`--false-wakes 30`",
    "numpy head + espeak-ng voices; never trained or run on a Pi": "ראש numpy וקולות espeak-ng; מעולם לא אומן ולא הורץ על Pi",
    # --- guides: live state, extra probes, fallback platform notes
    "turned off": "כבוי",
    "not in this version yet": "עוד לא קיים בגרסה הזו",
    "privacy mode is off: what you say is kept in the local history (never uploaded)":
        "מצב הפרטיות כבוי: מה שנאמר נשמר בהיסטוריה המקומית (אף פעם לא מועלה)",
    "no OpenAI admin key saved": "לא נשמר מפתח Admin של OpenAI",
    "optional read-only OpenAI admin key: real monthly cost for the spend display and usage checks; used only for OpenAI's costs endpoint":
        "מפתח Admin אופציונלי של OpenAI לקריאה בלבד: העלות החודשית האמיתית לתצוגת ההוצאות ולבדיקות שימוש; משמש רק לנקודת העלויות של OpenAI",
    "core/tests/test_usage_admin_key.py with a fake costs endpoint; no real admin key tested yet (owner decision OD-05)":
        "core/tests/test_usage_admin_key.py עם נקודת עלויות מדומה; עוד לא נבדק מפתח Admin אמיתי (החלטת בעלים OD-05)",
    "no data folder": "אין תיקיית נתונים",
    "no monthly API budget set": "לא נקבע תקציב חודשי ל-API",
    "no backup made yet": "עוד לא נעשה גיבוי",
    "core/tests/test_usage.py with a fake Costs API; no real admin key tested yet":
        "core/tests/test_usage.py עם Costs API מדומה; עוד לא נבדק מפתח Admin אמיתי",
    "pure Python HTTPS GET; not run on a Pi yet": "בקשת HTTPS ב-Python בלבד; טרם הורץ על Pi",
    "core/tests/test_usage.py; no real 75% alert observed yet": "core/tests/test_usage.py; עוד לא נצפתה התראת 75 אחוז אמיתית",
    "pure Python; not run on a Pi yet": "Python בלבד; טרם הורץ על Pi",
    "needs a trained custom wake-word model (docs/FUTURE_IDEAS.md)": "דורש מודל מילת הפעלה מותאם שאומן (docs/FUTURE_IDEAS.md)",
    "same plan as Windows": "אותה תוכנית כמו ב-Windows",
    "privacy mode tested in core/tests (history and logs)": "מצב פרטיות נבדק ב-core/tests (היסטוריה ויומנים)",
    "same code; not run on a Pi yet": "אותו קוד; טרם הורץ על Pi",
    "CI publish + signed manifest tested against a local fake GitHub API; needs the owner's public repo, token and key":
        "פרסום ב-CI ומניפסט חתום נבדקו מול GitHub מדומה מקומי; דרוש מאגר ציבורי, אסימון ומפתח של הבעלים",
    "no Pi package yet (#19)": "אין עדיין חבילה ל-Pi (#19)",
    # --- settings (config_edit.EDITABLE): groups and labels
    "Conversation": "שיחה", "Wake word": "מילת הפעלה", "Cost limits": "מגבלות עלות", "AI": "בינה מלאכותית", "Music": "מוזיקה",
    "Privacy & logs": "פרטיות ויומנים", "Self-diagnosis": "אבחון עצמי", "Pricing": "מחירים",
    "Give up on a stuck lookup after (s), then retry once": "לוותר על חיפוש תקוע אחרי (שנ׳) ולנסות שוב פעם אחת",
    "End a quiet conversation after (s)": "לסיים שיחה שקטה אחרי (שנ׳)",
    "Pause before the hello (s)": "השהיה לפני הברכה (שנ׳)",
    "Say \"still checking\" after (s) of a silent lookup (0 = never)": "לומר \"עוד בודק\" אחרי (שנ׳) של חיפוש שקט (0 = אף פעם)",
    "Longest single conversation (s)": "האורך המרבי של שיחה אחת (שנ׳)",
    "Mute the mic while JARVES speaks (laptop speakers)": "להשתיק את המיקרופון בזמן ש-JARVES מדבר (רמקולי מחשב נייד)",
    "Wake-word sensitivity threshold (lower = more sensitive)": "סף הרגישות של מילת ההפעלה (נמוך = רגיש יותר)",
    "Close a wake with no speech after (s)": "לסגור הפעלה בלי דיבור אחרי (שנ׳)",
    "AI requests per day (hard ceiling)": "בקשות AI ליום (תקרה קשיחה)",
    "Voice minutes per day (hard ceiling)": "דקות קול ליום (תקרה קשיחה)",
    "Backend speed tier for spoken lookups (priority = Fast, 2x price)": "רמת מהירות לחיפושים קוליים (priority = מהיר, פי 2 במחיר)",
    "Backend reasoning effort for spoken lookups": "רמת החשיבה של המודל בחיפושים קוליים",
    "Background task time limit (s)": "מגבלת זמן למשימת רקע (שנ׳)",
    "Maximum music volume (%)": "עוצמת המוזיקה המרבית (%)",
    "Louder / quieter step (%)": "גודל הצעד של חזק יותר או שקט יותר (%)",
    "Remove played songs from Pear's queue (its history) to keep it fast":
        "למחוק שירים שכבר נוגנו מהתור של Pear (ההיסטוריה שלו) כדי שיישאר מהיר",
    "Privacy mode: never keep what was said (logs, history)": "מצב פרטיות: לא לשמור מה שנאמר (יומנים, היסטוריה)",
    "Log file level": "רמת פירוט של קובץ היומן",
    "Keep conversation history for (days)": "לשמור היסטוריית שיחות (ימים)",
    "AI reviews per day at most": "מספר מרבי של סקירות AI ביום",
    "Flag silence longer than (s)": "לסמן שתיקה ארוכה מ-(שנ׳)",
    "Flag a request without an answer after (s)": "לסמן בקשה בלי תשובה אחרי (שנ׳)",
    "Flag a tool slower than (s)": "לסמן כלי איטי מ-(שנ׳)",
    "Flag a backend answer slower than (s)": "לסמן תשובת מודל איטית מ-(שנ׳)",
    "Web search price per call (USD)": "מחיר חיפוש ברשת לקריאה (דולר)",
    "Flag a conversation costing more than (USD)": "לסמן שיחה שעלתה יותר מ-(דולר)",
    "Flag a day costing more than (USD)": "לסמן יום שעלה יותר מ-(דולר)",
    # --- usage meters (usage.py)
    "ChatGPT plan usage": "שימוש בתוכנית ChatGPT",
    "plan usage window": "חלון השימוש בתוכנית",
    "plan usage limit": "מגבלת השימוש בתוכנית",
    "plan quota remaining": "מה שנשאר בתוכנית",
    "response headers of JARVES's plan requests": "כותרות התשובה של בקשות JARVES בתוכנית",
    "the plan route's 'usage limit reached' error": "שגיאת \"הגעת למגבלת השימוש\" של נתיב התוכנית",
    "not exposed to apps by the ChatGPT plan route": "התוכנית של ChatGPT לא חושפת את זה לאפליקציות",
    "undocumented headers of the plan route; ChatGPT Settings > Usage is the truth":
        "כותרות לא מתועדות של נתיב התוכנית; האמת נמצאת ב-ChatGPT: הגדרות > שימוש",
    "JARVES paused plan requests; reset time not reported. It does NOT switch to the paid API by itself.":
        "JARVES עצר בקשות בתוכנית; זמן האיפוס לא דווח. הוא לא עובר ל-API בתשלום מעצמו.",
    "OpenAI does not report remaining plan quota or its reset to JARVES. See ChatGPT Settings > Usage.":
        "OpenAI לא מדווחת ל-JARVES כמה נשאר בתוכנית ומתי היא מתאפסת. ראה ChatGPT: הגדרות > שימוש.",
    "JARVES requests on the plan today": "בקשות JARVES בתוכנית היום",
    "model requests sent by this JARVES on your plan": "בקשות למודל ש-JARVES שלח בתוכנית שלך",
    "store.db (typed answers / tasks on the plan)": "store.db (תשובות מוקלדות ומשימות בתוכנית)",
    "today (local)": "היום (שעון מקומי)",
    "only this install's requests, not your whole ChatGPT account": "רק הבקשות של ההתקנה הזו, לא של כל חשבון ChatGPT",
    "OpenAI API spend this month": "הוצאות ה-API של OpenAI החודש",
    "OpenAI API spend this month (estimate)": "הוצאות ה-API של OpenAI החודש (הערכה)",
    "organization cost": "עלות הארגון",
    "JARVES's voice + backend cost, estimated": "עלות הקול והמודל של JARVES, בהערכה",
    "OpenAI Costs API (Admin key, read-only)": "Costs API של OpenAI (מפתח Admin, לקריאה בלבד)",
    "store.db x [pricing]": "store.db כפול [pricing]",
    "your setting": "ההגדרה שלך",
    "calendar month (UTC)": "חודש קלנדרי (UTC)",
    "calendar month (local)": "חודש קלנדרי (שעון מקומי)",
    "daily buckets; today may lag. The project budget set on platform.openai.com is not readable by API":
        "נתונים לפי יום; היום עשוי לפגר. את תקציב הפרויקט מ-platform.openai.com אי אפשר לקרוא ב-API",
    "estimate of this install only; your OpenAI bill is the truth. Add a read-only OPENAI_ADMIN_KEY for the real organization cost.":
        "הערכה להתקנה הזו בלבד; החשבון של OpenAI הוא האמת. אפשר להוסיף OPENAI_ADMIN_KEY לקריאה בלבד לעלות האמיתית של הארגון.",
    "Live voice minutes today": "דקות קול חי היום",
    "GPT-Live session time": "זמן שיחות GPT-Live",
    "JARVES budget counter (memory.db)": "מונה התקציב של JARVES (memory.db)",
    "JARVES daily cap (ai.daily_realtime_minutes)": "התקרה היומית של JARVES (ai.daily_realtime_minutes)",
    "JARVES daily cap (ai.daily_request_limit)": "התקרה היומית של JARVES (ai.daily_request_limit)",
    "rate-limit window (about a minute)": "חלון מגבלת קצב (כדקה)",
    "x-ratelimit-* response headers": "כותרות התשובה x-ratelimit-*",
    "Realtime rate_limits.updated event": "אירוע rate_limits.updated של Realtime",
    "a speed limit that refills within about a minute, not a spending limit": "מגבלת קצב שמתמלאת תוך כדקה, לא מגבלת הוצאה",
    "JARVES model requests today": "בקשות JARVES למודל היום",
    "model requests (plan + API)": "בקשות למודל (תוכנית ו-API)",
    "Unknown means the provider does not report it; JARVES never guesses a percent.":
        "\"לא ידוע\" אומר שהספק לא מדווח; JARVES לא מנחש אחוזים.",
    "Tokens, requests, voice minutes, dollars and plan quota are tracked separately.":
        "טוקנים, בקשות, דקות קול, דולרים ומכסת התוכנית נספרים בנפרד.",
    "When the ChatGPT plan limit is reached JARVES pauses; it never switches to the paid API by itself.":
        "כשמגיעים למגבלת התוכנית של ChatGPT, JARVES עוצר; הוא לא עובר ל-API בתשלום מעצמו.",
    # --- page notes
    "Switches apply live where possible; others at the next conversation or Core start (see 'applies').":
        "המתגים פועלים מיד כשאפשר; האחרים בשיחה הבאה או בהפעלה הבאה של הליבה (ראה \"חל\").",
    "config.toml values apply when the Core starts next; the runtime switches apply at once.":
        "ערכי config.toml חלים בהפעלה הבאה של הליבה; מתגי זמן הריצה חלים מיד.",
    "Typed answers on the ChatGPT plan cost $0 API. AI reviews of conversations are included in their conversation.":
        "תשובות מוקלדות בתוכנית ChatGPT לא עולות כלום ב-API. סקירות AI של שיחות נכללות בעלות השיחה.",
    # --- backup guidance
    "Backups are in <data folder>/backups. They never contain your API key or sign-in tokens.":
        "הגיבויים נמצאים ב-<תיקיית הנתונים>/backups. הם אף פעם לא כוללים מפתח API או אסימוני התחברות.",
    "To check one: `jarvis backup verify NAME` or the dashboard's Test restore (restores into a throw-away profile).":
        "לבדיקה: `jarvis backup verify NAME` או \"בדיקת שחזור\" בלוח הבקרה (משחזר לפרופיל זמני).",
    "To restore: stop JARVES (`jarvis stop`), then `jarvis restore NAME`. Your current data is saved first as a pre-restore backup and put back automatically if anything fails.":
        "לשחזור: עצור את JARVES (`jarvis stop`) ואז `jarvis restore NAME`. הנתונים הנוכחיים נשמרים קודם בגיבוי ויוחזרו אוטומטית אם משהו נכשל.",
    "After restoring on another computer sign in again: `jarvis key`, `jarvis chatgpt login`, `jarvis google login`.":
        "אחרי שחזור במחשב אחר צריך להתחבר מחדש: `jarvis key`, `jarvis chatgpt login`, `jarvis google login`.",
    "If JARVES does not start after an update, restore the newest pre-migration backup the same way.":
        "אם JARVES לא עולה אחרי עדכון, שחזר באותה דרך את גיבוי \"לפני המעבר\" האחרון.",
    # --- issue details (fixed ones)
    "Nothing was said or done while a request was pending; say what is happening or give up with an honest answer.":
        "לא נאמר ולא נעשה דבר בזמן שבקשה חיכתה; צריך לומר מה קורה, או לוותר בתשובה כנה.",
    "Time from the end of the user's speech to JARVES's first audio.": "הזמן מסוף הדיבור של המשתמש ועד השמע הראשון של JARVES.",
    "The user said he did not understand / asked what was said.": "המשתמש אמר שהוא לא הבין או שאל מה נאמר.",
    "Two nearly identical requests in a row: the first was misheard or not handled.":
        "שתי בקשות כמעט זהות ברצף: הראשונה נשמעה לא נכון או לא טופלה.",
    "See the result codes; the user may have been told it worked.": "ראה את קודי התוצאה; ייתכן שנאמר למשתמש שזה הצליח.",
    "LIVE_TOOL_CALL without a TOOL_RESULT.": "LIVE_TOOL_CALL בלי TOOL_RESULT.",
    "Repeated calls usually mean the first result was not understood.": "קריאות חוזרות בדרך כלל אומרות שהתוצאה הראשונה לא הובנה.",
    "response.failed / response.incomplete from the delegated backend.": "response.failed או response.incomplete מהמודל שקיבל את המשימה.",
    "GPT-Live started replies before the user finished; JARVES dropped them (LIVE_EARLY_RESPONSE). Frequent drops cut answers or make the user wait.":
        "GPT-Live התחיל לענות לפני שהמשתמש סיים; JARVES זרק את התשובות (LIVE_EARLY_RESPONSE). זריקות תכופות חותכות תשובות או גורמות להמתנה.",
    "Raise voice.wake_threshold (`jarvis wake test --apply`) if this repeats.":
        "אם זה חוזר, העלה את voice.wake_threshold (`jarvis wake test --apply`).",
    # --- issue titles (fixed ones)
    "The user did not understand JARVES": "המשתמש לא הבין את JARVES",
    "The user had to repeat himself": "המשתמש נאלץ לחזור על עצמו",
    "False wake: conversation opened but nobody spoke": "הפעלת שווא: השיחה נפתחה ואף אחד לא דיבר",
    "Daily AI limit reached mid-conversation": "מגבלת ה-AI היומית הושגה באמצע שיחה",
    # --- voices
    "recommended (best quality)": "מומלץ (איכות הכי טובה)",
    "recommended (best quality), current default": "מומלץ (איכות הכי טובה), ברירת המחדל כרגע",
    "neutral": "ניטרלי", "masculine": "קול גברי", "masculine, expressive": "קול גברי, אקספרסיבי",
    "feminine": "קול נשי",
    "API default voice": "קול ברירת המחדל של ה-API",
    "British English, masculine (current default)": "אנגלית בריטית, קול גברי (ברירת המחדל כרגע)",
    "North American, masculine": "אנגלית אמריקאית, קול גברי", "Irish, masculine": "אנגלית אירית, קול גברי",
    "Australian, masculine": "אנגלית אוסטרלית, קול גברי", "Southern US, masculine": "אנגלית דרום-אמריקאית, קול גברי",
    "Filipino English, masculine": "אנגלית פיליפינית, קול גברי", "North American, feminine": "אנגלית אמריקאית, קול נשי",
    "Irish, feminine": "אנגלית אירית, קול נשי", "Australian, feminine": "אנגלית אוסטרלית, קול נשי",
    "Southern US, feminine": "אנגלית דרום-אמריקאית, קול נשי",
    "Brazilian Portuguese, masculine": "פורטוגזית ברזילאית, קול גברי", "Brazilian Portuguese, feminine": "פורטוגזית ברזילאית, קול נשי",
    "German, masculine": "גרמנית, קול גברי", "German, feminine": "גרמנית, קול נשי",
    "French, masculine": "צרפתית, קול גברי", "French, feminine": "צרפתית, קול נשי",
    "Hindi, feminine": "הינדית, קול נשי", "Japanese, masculine": "יפנית, קול גברי", "Japanese, feminine": "יפנית, קול נשי",
    "Korean, feminine": "קוריאנית, קול נשי",
    "speaks every language the model speaks (Hebrew included); the description is its accent":
        "מדבר בכל שפה שהמודל מדבר (כולל עברית); התיאור הוא המבטא שלו",
    "No preview: OpenAI publishes no sample clips through the API, and JARVES does not fake one. Hearing a voice needs a short real session (about $0.05 per minute on GPT-Live).":
        "אין תצוגה מקדימה: OpenAI לא מפרסמת דוגמאות דרך ה-API, ו-JARVES לא מזייף. כדי לשמוע קול צריך שיחה אמיתית קצרה (כ-0.05 דולר לדקה ב-GPT-Live).",
    # --- SW-V4 D (#14): client-mode delegation (feature, capability row, settings, probe reason)
    "JARVES itself runs every lookup GPT-Live hands over, and reserves its cost before sending it, so the daily and "
    "per-question limits are never exceeded; with ChatGPT + API key the reasoning runs on your plan. Adds a little "
    "latency":
        "ג'ארביס מריץ בעצמו כל חיפוש ש-GPT-Live מעביר, ושומר את העלות שלו לפני השליחה, כך שהתקרה היומית והתקרה לשאלה "
        "אף פעם לא נחצות; עם ChatGPT + מפתח API החשיבה רצה על המנוי שלך. מוסיף מעט השהיה",
    "core/tests/test_delegation_client.py (strict cap property test, fake Live and Responses servers); live scenarios "
    "51-62 in docs/reviews/SWV4_D_DELEGATION.md":
        "core/tests/test_delegation_client.py (בדיקת מאפיין של תקרה קשיחה, שרתי Live ו-Responses מדומים); תרחישים חיים "
        "51-62 ב-docs/reviews/SWV4_D_DELEGATION.md",
    "Pi voice is out of V4 scope": "קול על ה-Pi מחוץ לתחום של V4",
    "client delegation needs live cloud voice (voice.mode = live)": "תקציב קשיח לחיפושים צריך קול חי בענן (voice.mode = live)",
    "Daily budget for spoken lookups (USD, strict)": "תקציב יומי לחיפושים בשיחה (דולר, קשיח)",
    "Budget for one spoken question (USD, strict)": "תקציב לשאלה אחת בשיחה (דולר, קשיח)",
    "Model requests for one spoken question": "בקשות למודל לשאלה אחת בשיחה",
    "Web searches per request": "חיפושים ברשת לבקשה",
    "Stop a spoken lookup after (s)": "לעצור חיפוש בשיחה אחרי (שניות)",
    "If client delegation fails: local = honest \"can't look it up\"; server = allow server delegation":
        "אם התקציב הקשיח נכשל: local = תשובה כנה \"לא יכול לבדוק\"; server = לאפשר מעבר להעברה בצד השרת",
}


def _load_parts() -> dict[str, str]:
    """New texts live in hebrew_parts/<date>-<topic>.json ({"English": "עברית"}), one file per PR, instead of lines
    added to TEXT above (32 of ~150 merges touched this file and any two PRs adding texts conflicted). One home per
    text: tests/test_hebrew_texts.py fails when a text is both in TEXT and in a part, or in two parts."""
    import json
    out: dict[str, str] = {}
    for f in sorted(Path(__file__).with_name("hebrew_parts").glob("*.json")):
        out.update(json.loads(f.read_text(encoding="utf-8")))
    return out


TEXT.update(_load_parts())

_OUTCOME = {"answered late": "נענה באיחור", "the user gave up and asked something else": "המשתמש ויתר ושאל משהו אחר",
            "never answered: the conversation ended while waiting": "לא נענה: השיחה הסתיימה בהמתנה"}
_SCOPE = {"calendar": "יומן", "calendar write": "כתיבה ליומן", "contacts": "אנשי קשר", "gmail read": "קריאת מיילים",
          "gmail send": "שליחת מיילים"}
_LEVEL = {"ERROR": "שגיאה", "WARNING": "אזהרה"}


def _feature_he(fid: str, fallback: str) -> str:
    from .features import CATALOG                    # late: features imports this module through locales
    f = CATALOG.get(fid)
    return f.title_he if f else fallback


def _s(fmt: str):
    """Rule helper: a Hebrew template with {1}, {2} for regex groups (kept verbatim)."""
    return lambda m: re.sub(r"\{(\d)\}", lambda g: m.group(int(g.group(1))), fmt)


RULES: list[tuple[re.Pattern, object]] = [(re.compile(p, re.S), f) for p, f in [
    # probe reasons
    (r"^Google connection expired \((.+?)\); tell the user: .*$",
     lambda m: f"החיבור לגוגל פג ({_SCOPE.get(m.group(1), m.group(1))}); צריך לחבר מחדש"),
    (r"^Google account not connected \((.+?)\); tell the user: .*$",
     lambda m: f"חשבון גוגל לא מחובר ({_SCOPE.get(m.group(1), m.group(1))})"),
    (r"^Google consent required for (.+?): run `(.+?)` and approve the requested permission in the browser\. No sign-in or permission change was performed\.$",
     lambda m: f"נדרשת הסכמה בגוגל ל{_SCOPE.get(m.group(1), m.group(1))}: הרץ `{m.group(2)}` ואשר את ההרשאה בדפדפן. "
               "לא בוצעה התחברות ולא שונתה הרשאה."),
    (r"^UNIT (\S+\.py)$", _s("בדיקות יחידה: {1}")),
    (r"^no Telegram bot token; tell the user: .*$", lambda m: "אין טוקן לבוט טלגרם; מריצים במחשב jarvis telegram start"),
    (r"^no Instagram token; tell the user: .*$", lambda m: "אין טוקן לאינסטגרם; מריצים במחשב jarvis instagram start"),
    (r"^Home Assistant not set up; tell the user: .*$", lambda m: "Home Assistant עוד לא הוגדר; מריצים במחשב jarvis home start"),
    (r"^needs (.+) \((\w+)\) on$", lambda m: f"צריך להפעיל קודם את \"{_feature_he(m.group(2), m.group(1))}\""),
    # usage
    (r"^(\d+(?:\.\d+)?) min$", _s("{1} דק׳")),
    (r"^API rate limit \((.+?)(, realtime)?\)$", lambda m: f"מגבלת קצב ה-API ({m.group(1)}{', realtime' if m.group(2) else ''})"),
    (r"^(requests|tokens|project-tokens) per rate-limit window$", _s("{1} בחלון מגבלת הקצב")),
    (r"^(daily buckets; today may lag\. The project budget set on platform\.openai\.com is not readable by API)"
     r" \(last refresh failed\)$", lambda m: TEXT[m.group(1)] + " (הרענון האחרון נכשל)"),
    # issue titles
    (r"^Request stalled (\d+) s \((.*)\)$", lambda m: f"בקשה נתקעה {m.group(1)} שנ׳ ({_OUTCOME.get(m.group(2), m.group(2))})"),
    (r"^Dead air while waiting: (\d+) s of silence$", _s("שתיקה בזמן המתנה: {1} שנ׳ בלי קול")),
    (r"^Dead air before replies: (\d+)x over (\S+) s \(worst (\S+) s\)$",
     _s("שתיקה לפני תשובות: {1} פעמים מעל {2} שנ׳ (הגרועה {3} שנ׳)")),
    (r"^Confusing filler \"(.+)\" said (\d+)x( - the user asked what it means)?$",
     lambda m: f"מילת מילוי מבלבלת \"{m.group(1)}\" נאמרה {m.group(2)} פעמים"
               + (" - המשתמש שאל מה זה אומר" if m.group(3) else "")),
    (r"^Tool (\S+) failed (\d+)x$", _s("הכלי {1} נכשל {2} פעמים")),
    (r"^Tool (\S+) never returned \((\d+)x\)$", _s("הכלי {1} לא החזיר תוצאה ({2} פעמים)")),
    (r"^Slow tool (\S+): (\S+) s$", _s("הכלי {1} איטי: {2} שנ׳")),
    (r"^Tool (\S+) called (\d+)x within 30 s$", _s("הכלי {1} נקרא {2} פעמים תוך 30 שנ׳")),
    (r"^Slow backend response: (\S+) s$", _s("תשובה איטית מהמודל: {1} שנ׳")),
    (r"^Backend response failed (\d+)x$", _s("תשובת המודל נכשלה {1} פעמים")),
    (r"^Model kept answering while the user was talking \((\d+) audio chunks dropped\)$",
     _s("המודל המשיך לענות בזמן שהמשתמש דיבר ({1} קטעי שמע נזרקו)")),
    (r"^Expensive conversation: ~\$(\S+) \(estimate\)$", _s("שיחה יקרה: כ-${1} (הערכה)")),
    (r"^High cost on (\S+): ~\$(\S+) \(estimate\)$", _s("עלות גבוהה ב-{1}: כ-${2} (הערכה)")),
    # issue details
    (r"^\"(.*)\" got only progress fillers \((\d+)\) for (\d+) s before answer audio began "
     r"\(or its first recorded speech bucket for older logs\)\.$",
     _s("על \"{1}\" נאמרו רק משפטי המתנה ({2}) במשך {3} שנ׳ לפני שהתשובה התחילה (או לפני קטע הדיבור הראשון ביומנים ישנים).")),
    (r"^JARVES answers actions with a bare \"(.+)\"\. Users do not understand it as \"done\"; say what was done instead "
     r"\(\"(.*)\"\)\.$", _s("JARVES עונה על פעולות במילה \"{1}\" לבדה. משתמשים לא מבינים אותה כ\"בוצע\"; "
                            "עדיף לומר מה נעשה (\"{2}\").")),
    (r"^Over history\.slow_tool_s \((\S+) s\)\.$", _s("מעל history.slow_tool_s ({1} שנ׳).")),
    (r"^Over history\.slow_backend_s \((\S+) s\)\.$", _s("מעל history.slow_backend_s ({1} שנ׳).")),
    (r"^Above pricing\.session_alert_usd \(\$(\S+)\)\.$", _s("מעל pricing.session_alert_usd (${1}).")),
    (r"^Above pricing\.daily_alert_usd \(\$(\S+)\)\.$", _s("מעל pricing.daily_alert_usd (${1}).")),
    (r"^(\S+) logged (ERROR|WARNING)\.$", lambda m: f"{m.group(1)} רשם {_LEVEL[m.group(2)]}."),
]]


def he(text) -> str | None:
    """Hebrew for one of our fixed English texts, or None when there is none (the caller keeps the English)."""
    if not isinstance(text, str) or not text:
        return None
    hit = TEXT.get(text)
    if hit:
        return hit
    for pat, fn in RULES:
        m = pat.match(text)
        if m:
            return fn(m)
    return None


text = he          # the locales.TRANSLATORS interface


# kept for older callers: every registered locale (locales.py), Hebrew included
def attach(obj: dict, *keys: str) -> dict:
    from . import locales
    return locales.attach(obj, *keys)


def attach_list(obj: dict, key: str) -> dict:
    from . import locales
    return locales.attach_list(obj, key)


def issue(row: dict) -> dict:
    from . import locales
    return locales.issue(row)
