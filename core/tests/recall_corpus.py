"""SW-V4 G recall quality set: 40 planted synthetic conversations (Hebrew and English, voice and typed), hundreds of
distractor conversations that share their topics on other days (or their days with other topics), and 40 questions
(30 Hebrew, 10 English) with the conversation and the day each must find. Deterministic (fixed seed, fixed NOW).

The conversations are written into a real store.db through Store.ingest - the same log records the Core writes
(LIVE_STARTED / LIVE_USER_SAID / LIVE_JARVES_SAID / VOICE_ENDED for voice, REPLY for typed chat) - and indexed by
the real recall index. Used by tests/test_recall_benchmark.py and tools/recall_quality.py (prints the score)."""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

NOW = datetime(2026, 10, 20, 15, 0)          # a Tuesday; weeks start on Sunday (18 Oct)
SEED = 17


@dataclass
class Conv:
    key: str
    day: int                                  # days before NOW (0 = today)
    hm: str
    kind: str                                 # voice | typed
    turns: list[tuple[str, str]]
    tags: tuple[str, ...] = ()


PLANTED: list[Conv] = [
    Conv("P01", 1, "18:20", "voice", [("user", "תזכיר לי שאני צריך לקבוע תור לרופא השיניים ביום חמישי בבוקר"),
                                      ("assistant", "בסדר, אזכיר לך לקבוע תור לרופא השיניים.")], ("dentist",)),
    Conv("P02", 1, "09:10", "typed", [("user", "המוסך התקשר, הרכב יהיה מוכן רק ביום רביעי והתיקון יעלה 1800 שקל"),
                                      ("assistant", "הבנתי, הרכב נשאר במוסך עד יום רביעי.")], ("car",)),
    Conv("P03", 7, "20:05", "voice", [("user", "הטיסה שלנו לאתונה יוצאת בשש בבוקר ביום ראשון, צריך להיות בנתב\"ג בארבע"),
                                      ("assistant", "רשמתי את הטיסה לאתונה ביום ראשון בשש.")], ("flight",)),
    Conv("P04", 3, "11:30", "voice", [("user", "הכלב שלנו, רקסי, צריך חיסון אצל הווטרינר עד סוף החודש"),
                                      ("assistant", "אזכיר לך את החיסון של רקסי.")], ("dog", "vet")),
    Conv("P05", 2, "21:00", "voice", [("user", "אמא שלי מגיעה לארוחת שישי, והיא לא אוכלת גלוטן"),
                                      ("assistant", "רשמתי שאמא שלך לא אוכלת גלוטן.")], ("mom",)),
    Conv("P06", 14, "19:00", "typed", [("user", "החוזה של הדירה מסתיים בדצמבר ובעל הבית רוצה להעלות את שכר הדירה ב-300 שקל"),
                                       ("assistant", "כדאי לבקש ממנו את ההצעה בכתב.")], ("apartment",)),
    Conv("P07", 5, "08:15", "voice", [("user", "תזכור שהקוד של השער החשמלי בבניין הוא 4512"),
                                      ("assistant", "שמרתי.")], ("gate",)),
    Conv("P08", 4, "13:00", "voice", [("user", "ליום ההולדת של נועה אני רוצה לקנות לה אוזניות אלחוטיות"),
                                      ("assistant", "רעיון טוב, אוזניות אלחוטיות לנועה.")], ("birthday",)),
    Conv("P09", 6, "17:40", "typed", [("user", "הבוס שלי ביקש שאכין מצגת על התקציב עד יום ראשון"),
                                      ("assistant", "אפשר להתחיל משקף של סיכום ההוצאות.")], ("boss",)),
    Conv("P10", 9, "10:00", "voice", [("user", "המזגן בסלון מטפטף מים, צריך להזמין טכנאי"),
                                      ("assistant", "כדאי לכבות אותו עד שהטכנאי יגיע.")], ("ac",)),
    Conv("P11", 20, "16:00", "voice", [("user", "דיברתי עם עורך הדין על הצוואה, הפגישה הבאה בעשרים לנובמבר"),
                                       ("assistant", "רשמתי את הפגישה עם עורך הדין.")], ("lawyer",)),
    Conv("P12", 35, "12:00", "typed", [("user", "המתכון של עוגת הגזר של סבתא: שלוש כוסות גזר מגורר וכוס סוכר"),
                                       ("assistant", "שמרתי את המתכון של עוגת הגזר.")], ("recipe",)),
    Conv("P13", 1, "21:30", "voice", [("user", "ההופעה של עומר אדם נדחתה לחודש הבא, הכרטיסים עדיין בתוקף"),
                                      ("assistant", "טוב לדעת שהכרטיסים בתוקף.")], ("concert",)),
    Conv("P14", 0, "08:40", "voice", [("user", "תזכיר לי לקחת את התרופה ללחץ דם אחרי ארוחת הבוקר"),
                                      ("assistant", "אזכיר לך אחרי ארוחת הבוקר.")], ("medicine",)),
    Conv("P15", 8, "19:20", "voice", [("user", "השכן מלמעלה, יוסי, ביקש שנזיז את האוטו מהחניה שלו"),
                                      ("assistant", "אולי כדאי לחנות ברחוב הערב.")], ("yossi", "car")),
    Conv("P16", 10, "14:00", "typed", [("user", "Michal from work invited us to her wedding in Haifa next month"),
                                       ("assistant", "Congratulations to Michal! I can add the wedding to the calendar.")],
         ("wedding",)),
    Conv("P17", 12, "18:00", "typed", [("user", "דנה מהגן אמרה שמחר יש יום פיג'מות לילדים"),
                                       ("assistant", "אז מחר בבוקר פיג'מה במקום בגדים.")], ("kindergarten",)),
    Conv("P18", 16, "20:00", "voice", [("user", "קניתי כרטיסים לרכבת לירושלים ליום שישי בבוקר"),
                                       ("assistant", "נסיעה טובה לירושלים.")], ("train",)),
    Conv("P19", 2, "08:00", "voice", [("user", "מה מזג האוויר מחר בצפון?"),
                                      ("assistant", "מחר יהיה גשם חזק בצפון, כדאי לקחת מטריה.")], ("rain",)),
    Conv("P20", 25, "11:00", "typed", [("user", "המשכורת נכנסה רק ב-12 לחודש, צריך לדבר עם הנהלת החשבונות"),
                                       ("assistant", "כדאי לשלוח להם מייל קצר.")], ("salary",)),
    Conv("P21", 3, "20:30", "voice", [("user", "ההורים של אורי מזמינים אותנו לעל האש בשבת הבאה"),
                                      ("assistant", "נשמע כיף, לרשום ביומן?")], ("ori",)),
    Conv("P22", 40, "09:00", "voice", [("user", "ביטוח הרכב מתחדש באוקטובר, הצעת המחיר מהסוכן היא 3200 שקל"),
                                       ("assistant", "כדאי להשוות לפחות עוד הצעה אחת.")], ("insurance", "car")),
    Conv("P23", 6, "22:10", "voice", [("user", "אני רוצה להתחיל ללכת לחדר כושר שלוש פעמים בשבוע"),
                                      ("assistant", "מטרה מצוינת, אפשר להתחיל בשני ובחמישי.")], ("gym",)),
    Conv("P24", 11, "15:30", "typed", [("user", "המדפסת במשרד לא מתחברת לרשת, צריך להתקין דרייבר חדש"),
                                       ("assistant", "אפשר להוריד את הדרייבר מאתר היצרן.")], ("printer",)),
    Conv("P25", 13, "19:00", "voice", [("user", "החתול של השכנים נכנס שוב למרפסת ואכל את האוכל של הציפורים"),
                                       ("assistant", "אולי כדאי לשים את האוכל גבוה יותר.")], ("cat",)),
    Conv("P26", 4, "17:00", "voice", [("user", "תזכיר לי לשלם את חשבון החשמל עד העשרים וחמישה לחודש"),
                                      ("assistant", "אזכיר לך לשלם את חשבון החשמל.")], ("electricity",)),
    Conv("P27", 30, "20:00", "voice", [("user", "הבן שלי איתי קיבל 95 במבחן במתמטיקה"),
                                       ("assistant", "כל הכבוד לאיתי!")], ("exam",)),
    Conv("P28", 2, "13:00", "typed", [("user", "איפה כדאי לאכול דגים ביפו?"),
                                      ("assistant", "אני ממליץ על המסעדה הסטודיו ביפו, יש שם דגים טריים.")],
         ("restaurant",)),
    Conv("P29", 50, "10:00", "voice", [("user", "אנחנו מתכננים טיול לצפון בחנוכה, אולי צימר בגליל"),
                                       ("assistant", "בחנוכה כדאי להזמין צימר מוקדם.")], ("hanukkah",)),
    Conv("P30", 7, "09:30", "typed", [("user", "הפגישה עם הבנק נקבעה ליום שני בעשר, צריך להביא תלושי שכר"),
                                      ("assistant", "רשמתי: יום שני בעשר, תלושי שכר.")], ("bank",)),
    Conv("P31", 1, "12:00", "typed", [("user", "Remind me that the plumber is coming on Thursday at noon to fix the kitchen sink"),
                                      ("assistant", "I'll remind you about the plumber on Thursday.")], ("plumber",)),
    Conv("P32", 6, "18:30", "voice", [("user", "My sister Tamar is flying back from London next Sunday"),
                                      ("assistant", "Got it, Tamar lands next Sunday.")], ("tamar",)),
    Conv("P33", 8, "10:15", "typed", [("user", "I booked the hotel in Eilat for the first week of November"),
                                      ("assistant", "Nice, the hotel in Eilat is booked.")], ("hotel",)),
    Conv("P34", 2, "17:00", "voice", [("user", "לקחתי את רקסי לווטרינר בגלל הפציעה ברגל, הוא צריך משחה פעמיים ביום"),
                                      ("assistant", "רפואה שלמה לרקסי.")], ("vet", "dog")),
    Conv("P35", 20, "09:00", "voice", [("user", "Yael's birthday party is at the park on Saturday afternoon"),
                                       ("assistant", "I'll remember Yael's party on Saturday.")], ("yael", "birthday")),
    Conv("P36", 3, "15:00", "voice", [("user", "החלטנו לצבוע את חדר הילדים בכחול"),
                                      ("assistant", "כחול זה בחירה רגועה לחדר ילדים.")], ("paint",)),
    Conv("P37", 5, "20:00", "typed", [("user", "Netflix raised the price again, I want to cancel the subscription"),
                                      ("assistant", "You can cancel it in the account settings.")], ("netflix",)),
    Conv("P38", 0, "10:30", "voice", [("user", "Order more coffee capsules, we have only five left"),
                                      ("assistant", "I'll add coffee capsules to the shopping list.")], ("coffee",)),
    Conv("P39", 15, "19:00", "voice", [("user", "Which laptop should I buy for programming?"),
                                       ("assistant", "I recommend the ThinkPad X1 Carbon with 32 GB of memory.")],
         ("laptop",)),
    Conv("P40", 45, "14:00", "typed", [("user", "הדרכון של רוני פג תוקף במרץ, צריך לחדש אותו לפני הטיסה"),
                                       ("assistant", "את חידוש הדרכון אפשר לקבוע בלשכת האוכלוסין.")],
         ("passport", "flight")),
]

# (question, planted key) - 30 Hebrew, then 10 English
QUESTIONS: list[tuple[str, str]] = [
    ("מה אמרתי על רופא השיניים?", "P01"),
    ("מה דיברנו אתמול על הרכב?", "P02"),
    ("מה אמרתי ביום שלישי שעבר על הטיסה?", "P03"),
    ("מה אמרתי על החיסון של הכלב?", "P04"),
    ("מה סיפרתי לך על אמא שלי וארוחת שישי?", "P05"),
    ("מה אמרתי לפני שבועיים על הדירה?", "P06"),
    ("מה אמרתי על השער בבניין?", "P07"),
    ("מה רציתי לקנות לנועה ליום ההולדת?", "P08"),
    ("מה הבוס ביקש ממני?", "P09"),
    ("מה אמרתי על המזגן שמטפטף?", "P10"),
    ("מה דיברנו על עורך הדין?", "P11"),
    ("מה המתכון של עוגת הגזר?", "P12"),
    ("מה אמרתי אתמול בערב על ההופעה?", "P13"),
    ("מה אמרתי הבוקר על התרופה?", "P14"),
    ("מה יוסי ביקש?", "P15"),
    ("מה אמרתי על החתונה של מיכל?", "P16"),
    ("מה אמרה דנה מהגן?", "P17"),
    ("מה אמרתי על הרכבת לירושלים?", "P18"),
    ("מה אמרת לי על הגשם?", "P19"),
    ("מה אמרתי על המשכורת?", "P20"),
    ("מה אמרתי בשבת על אורי?", "P21"),
    ("מה אמרתי על ביטוח הרכב?", "P22"),
    ("מה אמרתי על חדר הכושר?", "P23"),
    ("מה הבעיה עם המדפסת?", "P24"),
    ("מה אמרתי על החתול של השכנים?", "P25"),
    ("מה אמרתי על חשבון החשמל?", "P26"),
    ("מה סיפרתי על המבחן של איתי?", "P27"),
    ("איזו מסעדה המלצת לי?", "P28"),
    ("מה תכננו לחנוכה?", "P29"),
    ("מה דיברנו על הבנק בשבוע שעבר?", "P30"),
    ("What did I say about the plumber?", "P31"),
    ("What did I tell you about Tamar?", "P32"),
    ("What did we talk about last week about the hotel?", "P33"),
    ("What did I say about the vet on Sunday?", "P34"),
    ("When is Yael's party?", "P35"),
    ("What did we decide about painting the kids room?", "P36"),
    ("What did I say about Netflix?", "P37"),
    ("What did I say this morning about coffee?", "P38"),
    ("Which laptop did you recommend?", "P39"),
    ("What did I say about Roni's passport?", "P40"),
]

# distractors: same topics on other days, other topics on the same days (tag, [(role, text), ...])
DISTRACTORS: list[tuple[str, list[tuple[str, str]]]] = [
    ("doctor", [("user", "הייתי אצל רופא המשפחה בגלל השיעול"), ("assistant", "רפואה שלמה, תנוח היום.")]),
    ("doctor", [("user", "I need a doctor's note for work tomorrow"), ("assistant", "You can ask the clinic online.")]),
    ("car", [("user", "צריך לשטוף את הרכב בסוף השבוע"), ("assistant", "רשמתי שטיפת רכב לסוף השבוע.")]),
    ("car", [("user", "How much does a car wash cost around here?"), ("assistant", "Usually about fifty shekels.")]),
    ("car", [("user", "תבדוק כמה זמן נסיעה באוטו לחיפה"), ("assistant", "בערך שעה וחצי בלי פקקים.")]),
    ("flight", [("user", "בדקתי מחירי טיסות לרומא לחופש הגדול"), ("assistant", "המחירים עולים בקיץ.")]),
    ("flight", [("user", "Flight prices to Paris went up again"), ("assistant", "Maybe try midweek dates.")]),
    ("dog", [("user", "יצאתי לטיול עם הכלב בפארק"), ("assistant", "איזה כיף, מזג אוויר טוב לטיול.")]),
    ("dog", [("user", "The dog ate my slipper again"), ("assistant", "Maybe a chew toy will help.")]),
    ("mom", [("user", "אמא התקשרה לשאול מה שלומנו"), ("assistant", "נחמד, תמסור לה ד\"ש.")]),
    ("mom", [("user", "Call mom tonight"), ("assistant", "I'll remind you to call your mom tonight.")]),
    ("apartment", [("user", "צריך לנקות את הדירה לפני שבת"), ("assistant", "אפשר להתחיל מהמטבח.")]),
    ("bank", [("user", "כמה כסף יש לנו בחשבון הבנק?"), ("assistant", "אין לי גישה לחשבון הבנק.")]),
    ("bank", [("user", "The bank sent a new credit card"), ("assistant", "Remember to activate it.")]),
    ("ac", [("user", "תדליק את המזגן על עשרים וארבע"), ("assistant", "אין לי שליטה על המזגן עדיין.")]),
    ("cat", [("user", "ראיתי חתול ג'ינג'י ליד הבית"), ("assistant", "חתולי רחוב אוהבים את השכונה.")]),
    ("electricity", [("user", "הייתה הפסקת חשמל בשכונה בלילה"), ("assistant", "מקווה שהכול חזר לעבוד.")]),
    ("concert", [("user", "יש הופעה של אייל גולן בקיסריה בקיץ"), ("assistant", "הכרטיסים נגמרים מהר.")]),
    ("weather", [("user", "מה מזג האוויר היום?"), ("assistant", "היום יהיה שמשי וחם.")]),
    ("weather", [("user", "Will it be windy tomorrow?"), ("assistant", "A light breeze, nothing strong.")]),
    ("music", [("user", "תשמיע מוזיקה שקטה"), ("assistant", "מנגן מוזיקה שקטה.")]),
    ("music", [("user", "Play some jazz"), ("assistant", "Playing jazz.")]),
    ("timer", [("user", "טיימר לעשר דקות לפסטה"), ("assistant", "טיימר לעשר דקות התחיל.")]),
    ("joke", [("user", "ספר לי בדיחה"), ("assistant", "למה המחשב הלך לרופא? כי היה לו וירוס.")]),
    ("joke", [("user", "Tell me a joke"), ("assistant", "Why did the bicycle fall over? It was two tired.")]),
    ("shopping", [("user", "תוסיף חלב ולחם לרשימת הקניות"), ("assistant", "הוספתי חלב ולחם.")]),
    ("shopping", [("user", "Add eggs and cheese to the shopping list"), ("assistant", "Added eggs and cheese.")]),
    ("gym", [("user", "רצתי חמישה קילומטר היום"), ("assistant", "כל הכבוד!")]),
    ("license", [("user", "צריך לחדש את רישיון הנהיגה"), ("assistant", "אפשר לחדש באתר משרד התחבורה.")]),
    ("yossi", [("user", "יוסי מהעבודה שלח את הדוח"), ("assistant", "מעולה, תעבור עליו מחר.")]),
    ("tamar", [("user", "תמר מהשכנים החזירה את המגש"), ("assistant", "נחמד מצידה.")]),
    ("news", [("user", "מה החדשות היום?"), ("assistant", "אני יכול לחפש לך כותרות.")]),
    ("news", [("user", "What's in the news today?"), ("assistant", "I can look up the headlines.")]),
    ("time", [("user", "מה השעה?"), ("assistant", "השעה שלוש.")]),
    ("cooking", [("user", "מה לבשל לארוחת ערב?"), ("assistant", "אולי פסטה ברוטב עגבניות.")]),
    ("cooking", [("user", "How long do I boil an egg?"), ("assistant", "About nine minutes for hard boiled.")]),
    ("hotel", [("user", "Hotels in Tel Aviv are expensive this summer"), ("assistant", "Maybe look at Bat Yam.")]),
    ("wedding", [("user", "החתונה של בן הדוד הייתה מרגשת"), ("assistant", "מזל טוב!")]),
    ("train", [("user", "הרכבת לתל אביב התעכבה בחצי שעה"), ("assistant", "מעצבן, מקווה שהספקת.")]),
    ("medicine", [("user", "Did I take my vitamins today?"), ("assistant", "I don't know, I didn't hear you say.")]),
    ("restaurant", [("user", "המסעדה האיטלקית בשכונה נסגרה"), ("assistant", "חבל, היה שם טעים.")]),
    ("printer", [("user", "Buy ink for the home printer"), ("assistant", "Added printer ink to the list.")]),
    ("coffee", [("user", "תכין לי רשימה של בתי קפה בעיר"), ("assistant", "אני יכול לחפש בתי קפה פתוחים.")]),
    ("laptop", [("user", "My laptop battery dies fast"), ("assistant", "Lower the screen brightness first.")]),
    ("netflix", [("user", "What should I watch tonight?"), ("assistant", "Maybe a documentary.")]),
    ("passport", [("user", "Where is the nearest post office?"), ("assistant", "I can search for it.")]),
]


def conversations(n_distractors: int = 400, seed: int = SEED) -> list[Conv]:
    """The planted conversations plus distractors. A distractor never shares its topic tag with a planted
    conversation of the same day (so every question has one right answer)."""
    from jarves.recall_text import parse_when
    rnd = random.Random(seed)
    taken = {(t, c.day) for c in PLANTED for t in c.tags}
    by_key = {c.key: c for c in PLANTED}
    for q, key in QUESTIONS:                  # a question with a time range: no same-topic distractor inside it
        w, _ = parse_when(q, NOW)
        if w is not None:
            for day in range(0, 120):
                mid = (NOW - timedelta(days=day)).replace(hour=12, minute=0).timestamp()
                if w.start - 43200 <= mid < w.end + 43200:
                    taken |= {(t, day) for t in by_key[key].tags}
    out = list(PLANTED)
    i = 0
    while len(out) < len(PLANTED) + n_distractors:
        tag, turns = DISTRACTORS[rnd.randrange(len(DISTRACTORS))]
        day = rnd.randrange(0, 88)
        if (tag, day) in taken:
            continue
        hm = f"{rnd.randrange(7, 23):02d}:{rnd.randrange(0, 60):02d}"
        if day == 0 and hm >= NOW.strftime("%H:%M"):
            continue
        i += 1
        out.append(Conv(f"D{i:04d}", day, hm, "voice" if rnd.random() < 0.6 else "typed", list(turns), (tag,)))
    return out


def start_ts(c: Conv, now: datetime = NOW) -> float:
    h, m = map(int, c.hm.split(":"))
    return (now.replace(hour=h, minute=m, second=0, microsecond=0) - timedelta(days=c.day)).timestamp()


def records(c: Conv, n: int, now: datetime = NOW) -> list[dict]:
    """The log records the Core writes for one conversation (privacy off), in time order."""
    t = start_ts(c, now)
    proc = f"bench{n % 7}"
    recs: list[dict] = []
    if c.kind == "voice":
        recs.append({"ts": t, "code": "LIVE_STARTED", "session": proc, "level": "INFO", "component": "voice",
                     "msg": "live session started (gpt-live-1 -> gpt-6-luna)"})
        for i, (role, text) in enumerate(c.turns):
            recs.append({"ts": t + 3 + 6 * i, "session": proc, "level": "INFO", "component": "voice",
                         "code": "LIVE_USER_SAID" if role == "user" else "LIVE_JARVES_SAID", "text": text})
        recs.append({"ts": t + 6 * len(c.turns) + 5, "code": "VOICE_ENDED", "session": proc, "level": "INFO",
                     "component": "voice", "reason": "idle", "voice_seconds": 6.0 * len(c.turns) + 5})
    else:
        pairs = [(c.turns[i][1], c.turns[i + 1][1] if i + 1 < len(c.turns) else "") for i in range(0, len(c.turns), 2)]
        for i, (u, a) in enumerate(pairs):
            recs.append({"ts": t + 2 + 30 * i, "code": "REPLY", "session": proc, "level": "INFO", "component": "core",
                         "utterance": u, "text": a, "ms": 2000, "typed": True})
    return recs


def seed_store(store, convs: list[Conv], now: datetime = NOW) -> dict[str, str]:
    """Ingest every conversation (oldest first, like a real day) and return {conversation key: session id}.
    A typed conversation gets its own processes' id so neighbouring typed chats never merge."""
    ids: dict[str, str] = {}
    order = sorted(range(len(convs)), key=lambda i: start_ts(convs[i], now))
    for n, i in enumerate(order):
        c = convs[i]
        store._typed = None                       # each planted typed chat is its own conversation
        for r in records(c, n, now):
            store.ingest(r)
        sid = store.rows("SELECT session_id FROM turns ORDER BY id DESC LIMIT 1")[0]["session_id"]
        ids[c.key] = sid
    store.commit()
    return ids


def day_marker(c: Conv, lang: str, now: datetime = NOW) -> str:
    """The words of the right day that a correct spoken answer must contain."""
    d = (now - timedelta(days=c.day)).date()
    months = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
              "November", "December")
    if c.day == 0:
        return "היום" if lang == "he" else "today"
    if c.day == 1:
        return "אתמול" if lang == "he" else "yesterday"
    return f"{d.day}.{d.month}" if lang == "he" else f"{d.day} {months[d.month - 1]}".lower()


def score(rec, ids: dict[str, str], now: datetime = NOW, k: int = 3) -> dict:
    """Recall@k and right-day-in-the-spoken-answer over the 40 questions, with per-question rows and timings."""
    from jarves.recall import answer, language
    by_key = {c.key: c for c in PLANTED}
    rows = []
    for q, key in QUESTIONS:
        res = rec.search(q, now=now, limit=k)
        got = [r["conversation_id"] for r in res["results"]]
        ans = answer(res, now=now)["text"]
        hit = ids[key] in got
        date_ok = bool(got) and got[0] == ids[key] and day_marker(by_key[key], language(q), now) in ans.lower()
        rows.append({"q": q, "want": key, "rank": got.index(ids[key]) + 1 if hit else None, "hit": hit,
                     "date_ok": date_ok, "answer": ans, "ms": res["took_ms"], "lang": language(q)})
    ms = sorted(r["ms"] for r in rows)
    return {"recall_at_k": sum(r["hit"] for r in rows), "date_ok": sum(r["date_ok"] for r in rows), "n": len(rows),
            "k": k, "p50_ms": ms[len(ms) // 2], "p95_ms": ms[int(len(ms) * 0.95) - 1], "max_ms": ms[-1],
            "rows": rows}


def build(home: Path, n_distractors: int = 400, now: datetime = NOW):
    """A fresh store.db in `home` with the corpus ingested; returns (Store, ids, conversations)."""
    from jarves.store import Store
    st = Store(Path(home) / "store.db")
    convs = conversations(n_distractors)
    ids = seed_store(st, convs, now)
    return st, ids, convs
