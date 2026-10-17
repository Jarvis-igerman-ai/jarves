# JARVIS — שלב 1 בלבד: תאום דיגיטלי (Windows)

השם החדש של הפרויקט: **JARVIS**. זהו רובוט **נייח** המיועד להפוך לעוזר אישי. **לא** מפתחים עכשיו Raspberry Pi, מנועים, ניווט, גלגלים או תנועה מכנית.

## מה בונים בשלב זה?
- דגם **הנדסי פרמטרי**, מתפרק וניתן בעתיד להדפסת תלת־ממד.
- מודל **מרשים וריאליסטי**, שנגזר מהדגם ההנדסי ולא ממציא מידות חדשות.
- סימולטור אינטראקטיבי ל־Windows המציג את אותו רובוט, את פנים המסך, את הפתיחה/סגירה, רכיבי הפנים והתנהגות המערכת.
- שכבת התקנים מדומה: כפתור/GPIO, מיקרופון, רמקולים, מצלמה, אור, נוכחות, רשת; ניתן לבדוק תרחישים גם ללא חומרה.
- ליבת עוזר אישי שאינה תלויה בחומרה, עם ממשק מוגדר ל־AI בענן/שרת רשת.

## הכללים
1. **משימה אחת בכל פעם**: קוראים `AGENTS.md` + `docs/CURRENT_TASK.md`. פותחים קבצים נוספים רק אם המשימה דורשת.
2. מסיימים משימה עם בדיקות ותוצאה עובדת; אחרי אישור המשתמש רק אז מקדמים את `CURRENT_TASK.md`.
3. Claude Code ו־Codex **לא** עורכים אותו קובץ או אותה משימה במקביל.
4. **FreeCAD הוא מקור האמת למידות ולגאומטריה הייצורית**. Blender הוא תצוגה/גימור, Godot הוא הסימולטור. לא מייצרים STL מתוך מודל קוסמטי.
5. אין טענה להתאמה פיזית 100% לפני קבלת מידות רכיבים אמיתיות והדפסת תושבות בדיקה. הסימולציה היא 1:1 *לוגית* מול חוזה ההתקנים, ובקנה מידה מכני לפי המידות המאומתות.

## סדר עבודה בתוך שלב 1 (לא להתחיל את כולם יחד)
- **T01 — CAD בסיסי**: תכנון נפחים, רכיבים ומעטפת ב־FreeCAD, חתימת assumptions ורשימת מידות חסרות. **זו המשימה הפעילה כעת**.
- **T02 — מראה איכותי**: Blender; חומרים/תאורה ומודל JARVIS שאפשר לזהות מהתמונה המקורית.
- **T03 — סימולטור Godot**: ייבוא GLB; סיבוב, Zoom, Exploded View, הרכבה ופירוק וירטואליים.
- **T04 — ליבה ושכבת התקנים מדומה**: Python + Event Contract, מדמי כפתור/GPIO, חיישנים, מצלמה ואודיו.
- **T05 — אינטגרציית מוצר**: מסך פנים חי, מדיה, AI stub ו־AI אופציונלי אמיתי, בדיקות end-to-end.
- **T06 — הקשחה וסיכום**: smoke/regression, תיעוד, הקפאת חוזה לקראת בדיקות רכיבים אמיתיים במחשב (שלב 2 בלבד).

## קבצים חשובים
- `AGENTS.md` — חוקים משותפים לסוכנים.
- `CLAUDE.md` — כניסה ל־Claude Code.
- `docs/ARCHITECTURE.md` — ארכיטקטורה והפרדה בין תכנון, סימולציה וליבה.
- `docs/DEVICE_CONTRACT.md` — אירועים וממשקי I/O.
- `docs/CURRENT_TASK.md` — המשימה היחידה הפעילה.
- `docs/HANDOFF.md` — סיכום קצר בסוף משימה.
- `assets/JARVIS_visual_reference.png` — **השראה חזותית**, לא שרטוט ייצור.

## מקורות רשמיים
- FreeCAD Assembly Workbench: https://github.com/FreeCAD/FreeCAD-documentation/blob/main/wiki/Assembly_Workbench.md
- FreeCAD STL export: https://github.com/FreeCAD/FreeCAD-documentation/blob/main/wiki/Export_to_STL_or_OBJ.md
- Godot stable glTF/Blender import: https://docs.godotengine.org/en/stable/tutorials/assets_pipeline/importing_3d_scenes/available_formats.html
- Godot renderers: https://docs.godotengine.org/en/stable/tutorials/rendering/renderers.html
- Godot requirements: https://docs.godotengine.org/en/stable/about/system_requirements.html
