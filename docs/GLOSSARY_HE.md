# Hebrew glossary (מילון מונחים)

One Hebrew term per concept in the guides (`core/src/jarves/guides/data/*.toml`), `USER_GUIDE.he.md` and the dashboard. Dashboard labels come from `core/src/jarves/dashboard_static/locales/he.js` (add them with `core/tools/catalog_add.py`); the guides must use the same word as the page they point to.

Style rules:
- Address the reader in the plural ("פתחו", "שלכם"), never "אתה/שלך", so the text is gender-neutral. JARVES himself is masculine ("ג'ארביס בודק").
- Command names, flags, config keys, file names and button names that exist only in English stay in Latin, inside backticks in Markdown. Keep a command on its own or at the end of a clause so the RTL punctuation does not jump (period and colon after the command, not inside it).
- Short sentences, no translated English idioms. Spelling per the Academy: "ודאו" (not "וודאו"), "הכול" (not "הכל").

| English | Hebrew | Note |
|---|---|---|
| JARVES | ג'ארביס (in sentences), JARVES (product name, titles) | never ג'רוויס / ג'ארוויס |
| wake word | מילת הפעלה | not "מילת השכמה" |
| dashboard | לוח הבקרה | not "דשבורד" (alias only) |
| Features page | עמוד היכולות | page label "יכולות" |
| Health page | תקינות | UI label; not "בריאות" |
| Recall | חיפוש בשיחות | UI label; "Recall" in brackets once |
| Coding agents | סוכני קוד | |
| Phone calls | שיחות טלפון | |
| Logs | יומן מערכת | |
| approval / approve / deny | אישור / אשר / דחה | button labels are masculine imperative |
| privacy mode | מצב פרטיות | |
| pairing (phone) | צימוד; מצומד | matches the dashboard |
| profile | פרופיל | |
| switch (feature toggle) | מתג | "הדליקו / כבו את המתג" |
| budget / daily limit | תקציב / תקרה יומית | |
| key (API) | מפתח API | admin key: מפתח אדמין |
| token | טוקן | |
| backup / restore | גיבוי / שחזור | |
| hash | גיבוב (hash) | not "מגבב" |
| untrusted text | טקסט לא מהימן | |
| allow-list | רשימת מותרים | |
| note / list / timer | פתק / רשימה / טיימר | |
| quiet hours | שעות שקט | |
| offline | במצב לא מקוון | |
| Teams | Teams | Latin |
