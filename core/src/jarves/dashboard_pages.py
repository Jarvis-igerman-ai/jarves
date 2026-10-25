"""Dashboard page directory for `open_dashboard(page=...)`: which pages exist, what each is for and what you can do
there, in English and Hebrew. Opening a page and explaining it is all the assistant does: every change on a page is made
by the user's own click, and the consequential ones (approvals, features, connections, backups, updates, autonomy)
keep their own confirmation. The ids match the sidebar in dashboard_static/index.html (tests/test_dashboard_pages.py)."""
from __future__ import annotations

import re

# id: (title_en, title_he, what_en, what_he, controls_en, controls_he)   controls = how to use it, "" = just read it
_P = {
    "home": ("Home", "בית", "status of JARVES and the main switches", "מצב JARVES והמתגים הראשיים",
             "mute, sleep and runtime controls at the top; cards link to the other pages",
             "השתקה, שינה ובקרות ריצה למעלה; הכרטיסים מובילים לשאר הדפים"),
    "chat": ("Chat", "שיחה", "type to JARVES in the browser", "הקלדה ל-JARVES בדפדפן",
             "type in the box and press Enter; tool results show as expandable lines",
             "מקלידים בתיבה ולוחצים Enter; תוצאות כלים מופיעות בשורות שנפתחות"),
    "approvals": ("Approvals", "אישורים", "requests that wait for your yes or no", "בקשות שממתינות לאישור או דחייה שלכם",
                  "Approve or Deny each request after reading its details; risky ones need a typed code",
                  "מאשרים או דוחים כל בקשה אחרי קריאת הפרטים; בקשות מסוכנות דורשות קוד שמקלידים"),
    "setup": ("Setup guide", "הגדרה וסיור", "step-by-step setup and tour", "הגדרה וסיור צעד אחר צעד",
              "follow the steps in order; each shows whether it is done", "עוברים על הצעדים לפי הסדר; כל צעד מראה אם הושלם"),
    "calendar": ("Calendar", "יומן", "upcoming events from Google Calendar", "אירועים קרובים מיומן Google",
                 "", ""),
    "lists": ("Lists", "רשימות", "shopping and to-do lists", "רשימות קניות ומשימות",
              "add items, tick them off, delete an item or a list", "מוסיפים פריטים, מסמנים כבוצע, מוחקים פריט או רשימה"),
    "timers": ("Timers & alarms", "טיימרים ושעונים", "running timers and alarms", "טיימרים ושעונים מעוררים פעילים",
               "start a timer or alarm, delete one", "מתחילים טיימר או שעון מעורר, מוחקים"),
    "notes": ("Notes", "פתקים", "quick notes you dictated", "פתקים קצרים שהכתבתם", "add, edit or delete a note", "מוסיפים, עורכים או מוחקים פתק"),
    "goals": ("Goals", "יעדים", "your goals and their progress", "היעדים שלכם והתקדמותם",
              "add a goal, update progress, pause or delete", "מוסיפים יעד, מעדכנים התקדמות, משהים או מוחקים"),
    "goalplans": ("Goal plans", "תוכניות ליעדים", "step-by-step plans for goals", "תוכניות לביצוע יעדים",
                  "review a plan, run it or reject it", "בודקים תוכנית, מריצים או דוחים"),
    "jobs": ("Scheduled jobs", "משימות קבועות", "recurring jobs", "משימות שחוזרות לפי לוח זמנים",
             "pause, resume or run a job now", "משהים, ממשיכים או מריצים משימה עכשיו"),
    "reports": ("Reports", "דוחות", "generated reports", "דוחות שנוצרו", "", ""),
    "alerts": ("Alerts", "התראות", "alerts and their quiet hours", "התראות ושעות שקט", "pause or resume alerts", "משהים או מחדשים התראות"),
    "proactive": ("Proactive offers", "הצעות יזומות", "things JARVES offered on its own", "הצעות ש-JARVES העלה מעצמו",
                  "dismiss an offer", "מבטלים הצעה"),
    "tasks": ("Background tasks", "משימות רקע", "long research jobs and their results", "משימות מחקר ארוכות ותוצאותיהן", "", ""),
    "conversations": ("Conversations", "שיחות", "past conversations", "שיחות קודמות",
                      "search, filter by day, open a conversation", "מחפשים, מסננים לפי יום, פותחים שיחה"),
    "recall": ("Search conversations", "חיפוש בשיחות", "full-text search across everything said", "חיפוש בכל מה שנאמר",
               "type a phrase and open a hit", "מקלידים ביטוי ופותחים תוצאה"),
    "memory": ("Memory", "זיכרון", "what JARVES remembers about you", "מה ש-JARVES זוכר עליכם",
               "add, edit or forget a memory; Undo restores the last change", "מוסיפים, עורכים או שוכחים זיכרון; ביטול מחזיר את השינוי האחרון"),
    "preferences": ("Learned preferences", "העדפות נלמדות", "preferences JARVES learned", "העדפות ש-JARVES למד",
                    "confirm, edit or forget a preference", "מאשרים, עורכים או שוכחים העדפה"),
    "browser": ("Browser", "דפדפן", "the assistant's own browsing", "הגלישה של העוזר", "", ""),
    "whatsapp": ("WhatsApp", "וואטסאפ", "WhatsApp connection and drafts", "חיבור וואטסאפ וטיוטות",
                 "pair the device, review drafts; nothing is sent without your approval", "מחברים מכשיר ובודקים טיוטות; שום דבר לא נשלח בלי אישורכם"),
    "calls": ("Phone calls", "שיחות טלפון", "assisted dialing", "חיוג בסיוע", "", ""),
    "files": ("My files", "קבצים שלי", "files you gave JARVES", "קבצים שנתתם ל-JARVES", "", ""),
    "identity": ("Identity & voice", "שם וקול", "assistant name and voice", "שם העוזר והקול", "pick a voice; it applies from the next conversation", "בוחרים קול; הוא חל משיחה הבאה"),
    "appearance": ("Appearance", "מראה", "face, avatar and theme", "פנים, אווטאר וערכת נושא", "choose a presentation", "בוחרים מראה"),
    "workspace": ("Personality files", "קבצי אישיות", "the files that shape JARVES", "הקבצים שמעצבים את JARVES",
                  "open a file, edit and save (a backup is kept); Restore brings back an older version",
                  "פותחים קובץ, עורכים ושומרים (נשמר גיבוי); שחזור מחזיר גרסה ישנה"),
    "features": ("Features & switches", "יכולות ומתגים", "every feature with a switch", "כל יכולת עם מתג",
                 "flip a switch to turn a feature on or off; 'unavailable' explains what is missing",
                 "מחליפים מתג להפעלה או כיבוי; 'לא זמין' מסביר מה חסר"),
    "skills": ("Skills", "כלים", "installed skills and their tools", "כלים מותקנים", "", ""),
    "newskills": ("Skill ideas", "רעיונות לכלים", "skills JARVES proposes", "כלים ש-JARVES מציע", "review a draft before it is approved", "בודקים טיוטה לפני אישור"),
    "autonomy": ("Autonomy", "עצמאות", "what JARVES may do without asking", "מה מותר ל-JARVES בלי לשאול", "change a level; stricter is safer", "משנים רמה; מחמיר יותר = בטוח יותר"),
    "connections": ("Connections", "חיבורים", "Google, ChatGPT and other accounts", "חשבונות Google, ChatGPT ואחרים",
                    "Connect or Disconnect an account; sign-in happens in your browser", "מחברים או מנתקים חשבון; ההתחברות נעשית בדפדפן"),
    "phone": ("Phone app", "אפליקציית טלפון", "pair your phone", "חיבור הטלפון", "scan the pairing code on the phone", "סורקים את קוד החיבור בטלפון"),
    "addons": ("Add-ons", "תוספים", "optional extras", "תוספים אופציונליים", "", ""),
    "issues": ("Issues", "בעיות", "problems JARVES found", "בעיות ש-JARVES מצא", "mark an issue handled", "מסמנים בעיה כמטופלת"),
    "camera": ("Camera", "מצלמה", "the real camera state, device checks and a timed local preview",
               "המצב האמיתי של המצלמה, בדיקות מכשיר ותצוגה מקומית מוגבלת בזמן",
               "Check device (opens nothing), Test a frame (one local picture), Start local preview with a countdown and Stop",
               "בדיקת מכשיר (לא פותח כלום), בדיקת תמונה (תמונה אחת מקומית), תצוגה מקומית עם ספירה לאחור וכפתור עצור"),
    "health": ("Health", "תקינות", "status of each component", "מצב כל רכיב", "", ""),
    "speed": ("Health & speed", "תקינות ומהירות", "speed measurements", "מדידות מהירות", "run a check", "מריצים בדיקה"),
    "selfcheck": ("Self-check", "בדיקה עצמית", "JARVES tests itself", "JARVES בודק את עצמו", "run the self-check", "מריצים בדיקה עצמית"),
    "logs": ("System log", "יומן מערכת", "what happened, with filters", "מה קרה, עם מסננים", "filter by level or text; Export saves the log", "מסננים לפי רמה או טקסט; ייצוא שומר את היומן"),
    "usage": ("Usage & limits", "שימוש ומגבלות", "plan usage and limits", "ניצול מנוי ומגבלות", "", ""),
    "costs": ("Costs", "עלויות", "spending per day and model", "הוצאות לפי יום ומודל", "", ""),
    "development": ("Development", "פיתוח", "ideas and drafts for JARVES itself", "רעיונות וטיוטות ל-JARVES עצמו", "", ""),
    "devdrafts": ("Development drafts", "טיוטות פיתוח", "drafted change requests", "בקשות שינוי מנוסחות", "review a draft before it is sent anywhere", "בודקים טיוטה לפני שהיא נשלחת"),
    "agents": ("Coding agents: all", "סוכני קוד: הכל", "coding agents and their runs", "סוכני קוד והרצותיהם", "", ""),
    "projects": ("Projects", "פרויקטים", "projects JARVES tracks", "פרויקטים ש-JARVES עוקב אחריהם", "", ""),
    "bugs": ("Bug reports", "דיווחי תקלות", "bug reports you can send", "דיווחי תקלות", "open a report to review it before sending", "פותחים דיווח ובודקים לפני שליחה"),
    "signoff": ("Release sign-off", "אישור גרסה", "the owner's release checklist", "רשימת האישור של הבעלים", "", ""),
    "guides": ("Guides", "מדריכים", "how to use and set up each feature", "איך משתמשים ומגדירים כל יכולת", "open a guide and follow its steps", "פותחים מדריך ועוברים על הצעדים"),
    "settings": ("Settings", "הגדרות", "settings in effect", "ההגדרות הפעילות", "change the allowed settings; Undo restores the last change", "משנים את ההגדרות המותרות; ביטול מחזיר את השינוי האחרון"),
    "devices": ("Devices & access", "מכשירים וגישה", "paired devices and who can reach the dashboard", "מכשירים מחוברים ומי יכול להגיע ללוח", "revoke a device you do not recognise", "מנתקים מכשיר שאינכם מכירים"),
    "runtime": ("Controls & runtime", "שליטה ומצב ריצה", "mute, sleep and runtime state", "השתקה, שינה ומצב ריצה", "", ""),
    "backup": ("Backup", "גיבוי", "back up and restore your data", "גיבוי ושחזור הנתונים", "create a backup; a restore replaces current data", "יוצרים גיבוי; שחזור מחליף את הנתונים הנוכחיים"),
    "updates": ("Updates", "עדכונים", "check for and install updates", "בדיקה והתקנה של עדכונים", "check; installing restarts JARVES", "בודקים; התקנה מפעילה מחדש את JARVES"),
    "releasenotes": ("Release notes", "הערות גרסה", "what changed in each version", "מה השתנה בכל גרסה", "", ""),
    "selfmanage": ("Self-management", "ניהול עצמי", "JARVES maintaining itself", "JARVES מתחזק את עצמו", "", ""),
    "profiles": ("Profiles", "פרופילים", "household profiles", "פרופילי בית", "", ""),
}
# pages where a click changes or sends something outside the page: the assistant only opens and explains them
SENSITIVE = frozenset({"approvals", "features", "settings", "connections", "whatsapp", "backup", "updates", "autonomy",
                       "devices", "workspace", "calls", "phone", "identity", "memory", "files", "camera"})
_WORDS = {"dashboard": "home", "main": "home", "chat with you": "chat", "approval": "approvals", "timer": "timers",
          "alarm": "timers", "alarms": "timers", "list": "lists", "cost": "costs", "spending": "costs", "log": "logs",
          "feature": "features", "switches": "features", "guide": "guides", "help": "guides", "setting": "settings",
          "connection": "connections", "google": "connections", "account": "connections", "goal": "goals",
          "note": "notes", "report": "reports", "alert": "alerts", "conversation": "conversations", "history": "conversations",
          "update": "updates", "bug": "bugs", "device": "devices", "health": "health", "speed": "speed",
          "voice": "identity", "usage": "usage", "limits": "usage", "plan": "usage", "task": "tasks", "memories": "memory"}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s.lower().replace("&", " "))).strip()


def resolve(query: str) -> str | None:
    """A page id for a hash ("#/costs"), an id, an English or Hebrew title, or a close word ("spending"); None if none."""
    q = _norm(query.removeprefix("#/").split("/", 1)[0] if query.startswith("#/") else query)
    if not q:
        return None
    if q in _P:
        return q
    for pid, row in _P.items():
        if q in (_norm(row[0]), _norm(row[1])):
            return pid
    if q in _WORDS:
        return _WORDS[q]
    hits = [pid for pid, row in _P.items() if q in _norm(row[0]) or q in _norm(row[1])]
    return hits[0] if len(hits) == 1 else None


def describe(pid: str) -> dict:
    t_en, t_he, w_en, w_he, c_en, c_he = _P[pid]
    return {"page": pid, "title": t_en, "title_he": t_he, "about": w_en, "about_he": w_he,
            "controls": c_en or "read-only: nothing to press here", "controls_he": c_he or "לקריאה בלבד: אין מה ללחוץ",
            "sensitive": pid in SENSITIVE}


def ids() -> list[str]:
    return list(_P)
