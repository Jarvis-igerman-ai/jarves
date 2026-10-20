"""Conversation recall, text side (SW-V4 package G, issue #17): Hebrew + English normalisation for the SQLite FTS5
index, query terms, and date / time-range phrases in both languages. Pure functions, no I/O.

Normalisation (index and query use the same steps):
- NFKD, every combining mark dropped (Hebrew niqqud and cantillation, Latin accents), NFC, casefold; maqaf = space;
  geresh / gershayim / typographic quotes unified, then removed inside a word ("ג'ירפה" = "גירפה", 'צה"ל' = "צהל").
- Hebrew final letters folded (ך→כ ם→מ ן→נ ף→פ ץ→צ), so "רופאים" and "רופאימ" are one token.
- Hebrew clitic prefixes (ו ה ב כ ל מ ש and their chains: "וכשהרופא") are stripped into extra FORMS (in their
  grammatical order, at least two letters left); light suffixes (plural ים / ות / יות, ית, feminine ה) give more forms.
  The surface word stays in the `body` column (an exact match ranks higher than a stripped form).
- Transliteration keys: every word also gets a consonant skeleton ("מיכל" and "Michal" -> "mkl", "חיים" and
  "Chaim" -> "km"...), tagged with its script. A Latin query word looks for HEBREW words with the same key and a
  Hebrew query word for Latin ones (cross-script only, so same-script noise stays out).
- A small bilingual concept table (car = רכב = מכונית = אוטו, dentist = שיניים ...) expands query words.

Dates (`parse_when`): "today / yesterday / the day before yesterday / on Tuesday / last week / 3 days ago / this
morning / yesterday evening / since Monday / 14 October / 14/10", and in Hebrew "היום / אתמול / שלשום / ביום שלישי
(שעבר) / ביום ג' / בשבוע שעבר / לפני שלושה ימים / לפני שבועיים / הבוקר / אתמול בערב / אמש / מאז יום ראשון / ב-14
באוקטובר / 14.10". Weeks start on Sunday (Israel). The phrases found are removed from the query.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from datetime import datetime, timedelta

# ---------------------------------------------------------------------- normalisation
_FINALS = str.maketrans("ךםןףץ", "כמנפצ")
_APOS = re.compile("['׳‘’`´]")
_QUOT = re.compile('["״“”„]')
# one Latin/digit run (may carry one inner apostrophe: don't) or one Hebrew run (may carry geresh / gershayim)
_TOKEN = re.compile(r"[a-z0-9]+(?:'[a-z]+)?|[א-ת]+(?:'[א-ת]*)?(?:\"[א-ת]+)?")
# a raw word of the ORIGINAL text (for snippets): letters, digits, marks, geresh/gershayim/apostrophes
RAW_WORD = re.compile(r"[\w֑-ׇ'׳״\"’]+")
HEBREW = re.compile(r"[א-ת]")
PREFIX_LETTERS = frozenset("והבכלמש")


def soft(text: object) -> str:
    """Marks dropped (niqqud, cantillation, accents), NFC, casefold, one apostrophe / quote character, maqaf = space.
    Final letters and the apostrophes stay (the date phrases and the transliteration keys need them)."""
    t = unicodedata.normalize("NFKD", str(text or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = unicodedata.normalize("NFC", t).casefold().replace("־", " ")
    return _QUOT.sub('"', _APOS.sub("'", t))


def _fold(tok: str) -> str:
    if tok.endswith("'s") and tok[:1].isascii():
        tok = tok[:-2]                                # "yael's" = "yael"
    return tok.replace("'", "").replace('"', "").translate(_FINALS)


def raw_tokens(text: object) -> list[str]:
    """Soft-normalised tokens with their geresh kept ("ג'ירפה") - the transliteration keys use it."""
    return [m.group(0) for m in _TOKEN.finditer(soft(text))]


def tokens(text: object) -> list[str]:
    """Index tokens: soft + geresh/quotes removed + final letters folded. One-letter Hebrew words (a lone prefix,
    "ה-iPhone") are dropped; digits stay."""
    out = []
    for raw in raw_tokens(text):
        t = _fold(raw)
        if len(t) >= 2 or t.isdigit():
            out.append(t)
    return out


def is_hebrew(tok: str) -> bool:
    return bool(HEBREW.search(tok))


def prefix_forms(w: str) -> list[str]:
    """Hebrew clitic chain in grammatical order: [ו] [ש | כש] [ב | כ | ל | מ] [ה], at least two letters left.
    'וכשהרופא' -> ['כשהרופא', 'הרופא', 'רופא']; 'במוסך' -> ['מוסך'] (the מ of the stem stays)."""
    out: list[str] = []
    cur = w

    def take(p: str) -> bool:
        nonlocal cur
        if cur.startswith(p) and len(_fold(cur[len(p):])) >= 2:
            cur = cur[len(p):]
            out.append(cur)
            return True
        return False
    take("ו")
    take("כש") or take("ש")
    any(take(p) for p in "בכלמ")
    take("ה")
    if out and out[-1].startswith("וו") and len(out[-1]) >= 4:
        out.append(out[-1][1:])                   # a stem's ו is doubled after a prefix: "לווטרינר" = ל + "וטרינר"
    return out


def suffix_forms(w: str) -> list[str]:
    out = []
    if len(w) >= 6 and w.endswith("יות"):
        out.append(w[:-3])
    if len(w) >= 5 and w.endswith(("ימ", "ות", "ית")):
        out.append(w[:-2])
    if len(w) >= 5 and w.endswith("ה"):
        out.append(w[:-1])
    return out


@lru_cache(maxsize=65536)
def forms(tok: str) -> frozenset[str]:
    """Every searchable form of one index token (itself, prefix-stripped and suffix-stripped Hebrew forms).
    Cached: words repeat across conversations (search and indexing speed)."""
    if not is_hebrew(tok):
        return frozenset({tok})
    out = {tok}
    for f in [tok, *prefix_forms(tok)]:
        out.add(f)
        out.update(suffix_forms(f))
    return frozenset(f for f in out if len(f) >= 2)


# ---------------------------------------------------------------------- transliteration keys
# Consonant classes shared by both scripts (upper case = placeholder: C = צ / tz, K = ח / ch; lower-cased at the end)
_HE_KEY = {"ב": "b", "ג": "g", "ד": "d", "ז": "z", "ט": "t", "ת": "t", "כ": "k", "ך": "k", "ק": "k", "ל": "l",
           "מ": "m", "ם": "m", "נ": "n", "ן": "n", "ס": "s", "ש": "s", "פ": "p", "ף": "p", "צ": "C", "ץ": "C",
           "ר": "r", "א": "", "ה": "", "ע": ""}
_LAT_SUBS = (("sch", "s"), ("tch", "C"), ("tz", "C"), ("ts", "C"), ("kh", "K"), ("sh", "s"), ("ph", "p"),
             ("th", "t"), ("ck", "k"), ("dj", "j"), ("zh", "j"), ("x", "ks"), ("q", "k"), ("w", "b"), ("v", "b"),
             ("f", "p"))


def _squeeze(s: str) -> str:
    return re.sub(r"(.)\1+", r"\1", s)


def _he_keys(raw: str) -> set[str]:
    """Consonant skeletons of a Hebrew word; ח and ו are ambiguous (Chaim/Hana, David/Yossi): both readings."""
    word = raw.replace('"', "")
    variants = [""]
    for i, c in enumerate(word):
        if c == "'":
            continue
        geresh = i + 1 < len(word) and word[i + 1] == "'"
        if geresh and c in "גז":
            opts = ["j"]
        elif geresh and c in "צץ":
            opts = ["C", "K"]                       # צ'יפס / צ'רלי
        elif c == "ח":
            opts = ["K", ""]
        elif c == "ו":
            opts = ["", "b"]
        elif c == "י":
            opts = ["y"] if i == 0 else [""]
        else:
            opts = [_HE_KEY.get(c, "")]
        variants = [v + o for v in variants for o in opts][:8]
    return {k for k in (_squeeze(v.lower()) for v in variants) if 2 <= len(k) <= 12}


def _lat_keys(word: str) -> set[str]:
    w = re.sub(r"[^a-z]", "", word)
    if len(w) < 2:
        return set()
    for a, b in _LAT_SUBS:
        w = w.replace(a, b)
    alts = {w.replace("ch", "K")}
    if "ch" in w:                                   # "ch" = ח / כ (Chaim, Michal) or צ' (Charlie)
        alts.add(w.replace("ch", "C"))
    out = set()
    for v in alts:
        v = re.sub(r"c(?=[eiy])", "s", v).replace("c", "k")      # soft c, then hard c
        head, tail = v[:1], v[1:]
        head = "" if head in "aeiouh" else head
        tail = re.sub(r"[aeiouyh]", "", tail)
        k = _squeeze((head + tail).lower())
        if 2 <= len(k) <= 12:
            out.add(k)
    return out


@lru_cache(maxsize=65536)
def keys(raw: str) -> frozenset[str]:
    """Script-tagged transliteration keys of one raw token: 'xh<key>' for Hebrew words, 'xl<key>' for Latin."""
    if is_hebrew(raw):
        out: set[str] = set()
        for f in [raw, *prefix_forms(raw)]:
            out |= _he_keys(f)
        return frozenset("xh" + k for k in out)
    if raw.isdigit():
        return frozenset()
    return frozenset("xl" + k for k in _lat_keys(raw))


def cross_keys(raw: str) -> set[str]:
    """What a QUERY word looks for in the other script."""
    return {("xl" if k.startswith("xh") else "xh") + k[2:] for k in keys(raw)}


def doc_fields(text: object) -> tuple[str, str, str]:
    """(body, stems, phon) columns of one indexed text."""
    raws = raw_tokens(text)
    toks = [t for t in (_fold(r) for r in raws) if len(t) >= 2 or t.isdigit()]
    stems: set[str] = set()
    for t in toks:
        stems |= forms(t)
    phon: set[str] = set()
    for r in raws:
        if len(_fold(r)) >= 2:
            phon |= keys(r)
    return " ".join(toks), " ".join(sorted(stems)), " ".join(sorted(phon))


# ---------------------------------------------------------------------- query words
STOP_HE = {_fold(w) for w in """
מה מי איפה מתי איך למה כמה האם על את אתה אני אנחנו הוא היא הם הן זה זאת זו יש אין היה היתה היו של עם אל לי לך לו לה
לנו לכם להם שלי שלך שלו שלה שלנו אותי אותך אותו אותה אמרתי אמרת אמרנו אמר אמרה דיברנו דיברתי דיברת דברנו סיפרתי
סיפרת ספרתי שאלתי שאלת ביקשתי ביקשת הזכרתי הזכרת רשמתי רשמת רשמנו תזכיר תזכירי תזכור תגיד תגידי תספר תספרי ספר תאמר שיחה שיחות
דבר דברים משהו כל גם רק כבר עוד אז כן לא אבל או אם כי בקשר לגבי בנוגע אודות נושא הנושא בעניין עניין קודם פעם
ג'רוויס גארוויס ג'ארוויס ג'ארביס ג'רביס jarvis jarves בבקשה אתם היינו הייתי הייתה היית ממש אולי איזה איזו אילו
""".split()}
STOP_EN = set("""
what whats which who whom when where why how did do does done i we you he she they it me my our your us was were is
are am be been the a an on in at of to for with about from talk talked talking speak spoke say said tell told
conversation conversations chat chatted discuss discussed mention mentioned remember recall recalled anything
something that this those these there any again please jarvis jarves regarding ask asked thing things us can could
would will shall should hey let lets know
""".split())
SPEAKER_USER = re.compile(r"\b(?:did i|i said|i told|i asked|i mentioned|have i|i say|i tell)\b|"
                          r"(?<![א-ת])[ושמ]?(?:אמרתי|סיפרתי|שאלתי|ביקשתי|הזכרתי|ספרתי)(?![א-ת])")
SPEAKER_JARVES = re.compile(r"\b(?:did you|you said|you told|you suggested|you recommended|you say|you tell)\b|"
                            r"(?<![א-ת])[ושמ]?(?:אמרת|סיפרת|הצעת|המלצת|ענית|הסברת)(?![א-ת])")

# Bilingual concept table: any member found in a query expands to all members (each with its own forms).
CONCEPTS: list[set[str]] = [set(g.split()) for g in """
car cars vehicle רכב מכונית מכוניות אוטו
mechanic garage מוסך מכונאי
doctor physician רופא רופאה רופאת דוקטור
dentist dental teeth tooth שיניים שן דנטיסט שיננית
mom mother mum אמא אימא
dad father אבא
wife אישה אשתי
husband בעלי
grandma grandmother סבתא
grandpa grandfather סבא
birthday יומולדת הולדת
flight flights plane טיסה טיסות מטוס
vacation holiday trip חופשה טיול נסיעה
hotel מלון
restaurant מסעדה
dog puppy כלב כלבה
cat kitten חתול חתולה
vet veterinarian וטרינר וטרינרית
meeting פגישה
appointment תור
insurance ביטוח
bank בנק
payment pay paid תשלום לשלם שילמתי
money cash כסף
rent שכירות
apartment flat דירה
work job office עבודה משרד
boss manager בוס מנהל מנהלת
gift present מתנה מתנות
recipe מתכון
movie film סרט סרטים
book ספר
song שיר
music מוזיקה
phone טלפון
email mail מייל
weather מזג
rain גשם
electricity electric חשמל
plumber אינסטלטור
tax taxes מס מיסים
passport דרכון
medicine medication pills תרופה תרופות כדורים
pharmacy מרקחת
gym workout כושר אימון
groceries supermarket shopping קניות סופר
wedding חתונה
party מסיבה
neighbor neighbour שכן שכנה
teacher מורה
exam test מבחן בחינה
course class קורס שיעור
computer laptop מחשב
printer מדפסת
internet wifi router אינטרנט ראוטר
train רכבת
bus אוטובוס
airport נתבג תעופה
tickets ticket כרטיסים כרטיס
concert show הופעה
football soccer כדורגל
garden plants גינה צמחים
broken fault תקלה שבור התקלקל
fridge refrigerator מקרר
ac aircon מזגן
mortgage משכנתא
salary משכורת
lawyer עורך עורכת
contract חוזה
paint painting painted צבע לצבוע צביעה
kids children child ילדים ילד ילדה
room bedroom חדר
decide decided decision החלטנו החלטתי החלטה
""".strip().splitlines()]
_CONCEPT_OF: dict[str, set[str]] = {}
for _g in CONCEPTS:
    _norm = {_fold(soft(w)) for w in _g}
    for _w in _norm:
        _CONCEPT_OF.setdefault(_w, set()).update(_norm)


def _stop(tok: str) -> bool:
    if is_hebrew(tok):
        if tok in STOP_HE:
            return True
        return any(f in STOP_HE and len(tok) - len(f) <= 2 for f in prefix_forms(tok))
    return tok in STOP_EN


@dataclass
class Term:
    word: str                          # the query word as written (normalised)
    forms: set[str]                    # matched against the `stems` column (own forms + concept synonyms' forms)
    body: set[str]                     # surface words for the `body` column (the word + its synonyms)
    phon: set[str] = field(default_factory=set)   # cross-script transliteration keys (`phon`)
    own: set[str] = field(default_factory=set)    # the word's own forms (an exact-language match ranks higher)
    said: str = ""                                # as written in the question (geresh kept: for the answer)
    near: set[str] = field(default_factory=set)   # same-script keys of a Latin word (STT spellings: Tomer/Tamar)


# match strength of one query word in one text: its own surface word > its own forms > a concept synonym > a
# transliteration of the other script
STRENGTH = {"exact": 1.2, "form": 1.0, "synonym": 0.8, "near": 0.75, "phon": 0.5}


def query_terms(text: str) -> list[Term]:
    """Content words of a natural-language question (stop words and question words removed), each with its forms,
    concept synonyms and cross-script keys (only for words outside the concept table: a concept already crosses
    the language, and a common word's skeleton matches too much). Duplicates merged."""
    out: list[Term] = []
    seen: set[str] = set()
    for raw in raw_tokens(text):
        tok = _fold(raw)
        if (len(tok) < 2 and not tok.isdigit()) or _stop(tok) or tok in seen:
            continue
        seen.add(tok)
        own = set(forms(tok))
        fs = set(own)
        body = {tok}
        syn: set[str] = set()
        for f in own:
            syn |= _CONCEPT_OF.get(f, set())
        for s in syn:
            fs |= forms(s)
            body.add(s)
        # a Latin word also meets other Latin spellings of the same name (speech-to-text: "Tomer" for "Tamar")
        near = set() if syn or is_hebrew(raw) else set(keys(raw))
        out.append(Term(tok, fs, body, set() if syn else set(cross_keys(raw)), own, raw, near))
    return out


def match_strength(term: Term, toks: set[str], fs: set[str], phon: set[str]) -> float:
    """0 = no match; else STRENGTH of the best way the term matches a text (its tokens, forms, keys)."""
    if term.word in toks:
        return STRENGTH["exact"]
    if term.own & fs:
        return STRENGTH["form"]
    if term.forms & fs or term.body & toks:
        return STRENGTH["synonym"]
    if term.near & phon:
        return STRENGTH["near"]
    if term.phon & phon:
        return STRENGTH["phon"]
    return 0.0


def speaker_hint(text: str) -> str | None:
    """'what did I say' -> user, 'what did you tell me' -> assistant (a ranking boost, not a filter)."""
    t = soft(text)
    if SPEAKER_JARVES.search(t):
        return "assistant"
    if SPEAKER_USER.search(t):
        return "user"
    return None


# ---------------------------------------------------------------------- date / time ranges
@dataclass
class When:
    start: float
    end: float
    phrase: str                        # the words that set it, as written (soft form)
    since: bool = False
    center: float | None = None        # "3 days ago" / "two weeks ago": the day meant (closer ranks higher)


DAY_PARTS = {"morning": (5, 12), "noon": (11, 15), "afternoon": (12, 18), "evening": (17, 23), "night": (19, 29)}
_HE_PARTS = {"בבוקר": "morning", "הבוקר": "morning!", "בצהריים": "noon", "בצהרים": "noon", "הצהריים": "noon!",
             "אחר הצהריים": "afternoon", "אחרי הצהריים": "afternoon", 'אחה"צ': "afternoon", "בערב": "evening",
             "הערב": "evening!", "בלילה": "night", "הלילה": "night!"}
_EN_PARTS = {"in the morning": "morning", "this morning": "morning!", "at noon": "noon", "in the afternoon": "afternoon",
             "this afternoon": "afternoon!", "in the evening": "evening", "this evening": "evening!",
             "tonight": "night!", "at night": "night", "morning": "morning", "afternoon": "afternoon",
             "evening": "evening", "night": "night"}
_NUM_EN = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
           "nine": 9, "ten": 10, "couple": 2, "a couple of": 2, "few": 3, "a few": 3}
_NUM_HE = {"אחד": 1, "אחת": 1, "שני": 2, "שתי": 2, "שניים": 2, "שתיים": 2, "שלושה": 3, "שלוש": 3, "ארבעה": 4,
           "ארבע": 4, "חמישה": 5, "חמש": 5, "שישה": 6, "שש": 6, "שבעה": 7, "שבע": 7, "שמונה": 8, "תשעה": 9,
           "תשע": 9, "עשרה": 10, "עשר": 10, "כמה": 3}
_EN_WD = {"monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2, "thursday": 3,
          "thu": 3, "thurs": 3, "friday": 4, "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6}
_HE_WD = {"ראשון": 6, "שני": 0, "שלישי": 1, "רביעי": 2, "חמישי": 3, "שישי": 4, "שבת": 5}
_HE_LETTER_WD = {"א": 6, "ב": 0, "ג": 1, "ד": 2, "ה": 3, "ו": 4, "ש": 5}
_EN_MONTHS = {m: i + 1 for i, ms in enumerate(("jan january", "feb february", "mar march", "apr april", "may",
                                                 "jun june", "jul july", "aug august", "sep sept september",
                                                 "oct october", "nov november", "dec december")) for m in ms.split()}
_HE_MONTHS = {m: i + 1 for i, ms in enumerate(("ינואר", "פברואר", "מרץ מרס", "אפריל", "מאי", "יוני", "יולי",
                                                 "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"))
              for m in ms.split()}
_NB = r"(?<![א-תa-z0-9])"          # word start (Hebrew + Latin)
_NE = r"(?![א-תa-z0-9])"           # word end
_P = r"[ושמב]?"                     # an attached Hebrew conjunction / preposition


def _day(d: datetime) -> datetime:
    return d.replace(hour=0, minute=0, second=0, microsecond=0)


def _week_start(d: datetime) -> datetime:
    return _day(d) - timedelta(days=(d.weekday() + 1) % 7)       # Sunday


def _num(s: str) -> int | None:
    s = s.strip()
    if s.isdigit():
        return int(s)
    return _NUM_EN.get(s) or _NUM_HE.get(s)


def _ago(n: int, unit: str, now: datetime) -> tuple[datetime, datetime, datetime]:
    """A fuzzy window around the day meant (and that day itself: closer conversations rank higher)."""
    if unit.startswith(("day", "יו", "ימ")):
        d = _day(now) - timedelta(days=n)
        return ((d, d + timedelta(days=1)) if n <= 2 else (d - timedelta(days=1), d + timedelta(days=2))) + (d,)
    if unit.startswith(("week", "שבו")):
        d = _day(now) - timedelta(days=7 * n)
        return d - timedelta(days=3), d + timedelta(days=4), d
    d = _day(now) - timedelta(days=30 * n)                         # months
    return d - timedelta(days=10), d + timedelta(days=11), d


def _past_weekday(wd: int, now: datetime, last: bool) -> tuple[datetime, datetime]:
    """The most recent such day before today ('last' = in the previous week when it would be this week)."""
    back = (now.weekday() - wd) % 7 or 7
    d = _day(now) - timedelta(days=back)
    if last and d >= _week_start(now):
        d -= timedelta(days=7)
    return d, d + timedelta(days=1)


def _date(y: int | None, m: int, d: int, now: datetime) -> tuple[datetime, datetime] | None:
    try:
        x = datetime(y if y else now.year, m, d)
    except ValueError:
        return None
    if y is None and x > now:
        x = x.replace(year=x.year - 1)
    return x, x + timedelta(days=1)


def _rules(now: datetime):
    """(regex, handler(match) -> (start, end) | None, since?) in priority order."""
    today = _day(now)
    tomorrow = today + timedelta(days=1)
    ws = _week_start(now)
    month0 = today.replace(day=1)
    prev_month0 = (month0 - timedelta(days=1)).replace(day=1)
    wd_he = "|".join(_HE_WD)
    wd_en = "|".join(sorted(_EN_WD, key=len, reverse=True))
    mon_en = "|".join(sorted(_EN_MONTHS, key=len, reverse=True))
    mon_he = "|".join(_HE_MONTHS)
    num_he = "|".join(sorted(_NUM_HE, key=len, reverse=True))
    num_en = "|".join(sorted(_NUM_EN, key=len, reverse=True))
    return [
        # explicit dates
        (rf"{_NB}(?P<y>\d{{4}})-(?P<m>\d{{1,2}})-(?P<d>\d{{1,2}}){_NE}",
         lambda m: _date(int(m["y"]), int(m["m"]), int(m["d"]), now)),
        (rf"{_NB}{_P}-?(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s*(?:of\s+)?(?:ב|ל)?(?P<mo>{mon_he}|{mon_en}){_NE}"
         rf"(?:,?\s+(?P<y>\d{{4}}))?",
         lambda m: _date(int(m["y"]) if m["y"] else None, (_HE_MONTHS.get(m["mo"]) or _EN_MONTHS[m["mo"]]),
                         int(m["d"]), now)),
        (rf"{_NB}(?P<mo>{mon_en})\.?\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(?P<y>\d{{4}}))?{_NE}",
         lambda m: _date(int(m["y"]) if m["y"] else None, _EN_MONTHS[m["mo"]], int(m["d"]), now)),
        (rf"(?<![\d./]){_P}-?(?P<d>\d{{1,2}})[./](?P<m>\d{{1,2}})(?:[./](?P<y>\d{{2,4}}))?(?![\d./])",
         lambda m: _date((int(m["y"]) + (2000 if len(m["y"]) == 2 else 0)) if m["y"] else None, int(m["m"]),
                         int(m["d"]), now)),
        # relative days
        (r"\bthe day before yesterday\b", lambda m: (today - timedelta(days=2), today - timedelta(days=1))),
        (rf"{_NB}[ושמ]?שלשום{_NE}", lambda m: (today - timedelta(days=2), today - timedelta(days=1))),
        (rf"{_NB}[ושמ]?אתמול{_NE}|\byesterday\b", lambda m: (today - timedelta(days=1), today)),
        (rf"{_NB}[ושמ]?אמש{_NE}|\blast night\b",
         lambda m: (today - timedelta(hours=5), today + timedelta(hours=5))),
        (rf"{_NB}[ושמ]?היום{_NE}|\btoday\b", lambda m: (today, tomorrow)),
        # N units ago
        (rf"{_NB}[ושמ]?לפני\s+(?P<u>יומיים|שבועיים|חודשיים){_NE}",
         lambda m: _ago(2, {"יומיים": "day", "שבועיים": "week", "חודשיים": "month"}[m["u"]], now)),
        (rf"{_NB}[ושמ]?לפני\s+(?:(?P<n>\d+|{num_he})\s+)?(?P<u>ימים|יום|שבועות|שבוע|חודשים|חודש){_NE}",
         lambda m: _ago(_num(m["n"]) if m["n"] else 1, m["u"], now)),
        (rf"\b(?P<n>\d+|{num_en})\s+(?:of\s+)?(?P<u>days?|weeks?|months?)\s+ago\b",
         lambda m: _ago(_num(m["n"]) or 1, m["u"], now)),
        # rolling windows
        (rf"{_NB}{_P}(?:ה)?ימים\s+האחרונים{_NE}|\b(?:in\s+)?the\s+(?:last|past)\s+(?:few|couple of)\s+days\b",
         lambda m: (today - timedelta(days=3), tomorrow)),
        (rf"{_NB}{_P}(?:ה)?שבוע\s+האחרון{_NE}|\b(?:in\s+)?the\s+(?:last|past)\s+(?:7\s+days|week)\b",
         lambda m: (today - timedelta(days=7), tomorrow)),
        (rf"{_NB}{_P}(?:ה)?חודש\s+האחרון{_NE}|\b(?:in\s+)?the\s+(?:last|past)\s+(?:30\s+days|month)\b",
         lambda m: (today - timedelta(days=30), tomorrow)),
        (rf"{_NB}[ו]?לאחרונה{_NE}|\b(?:recently|lately)\b", lambda m: (today - timedelta(days=14), tomorrow)),
        # weekdays (before the plain weeks: "ביום שלישי שעבר" is a day)
        (rf"{_NB}{_P}יום\s+(?:ה)?(?P<wd>{wd_he})(?:\s+(?P<last>שעבר|שעברה|הקודם|האחרון))?{_NE}",
         lambda m: _past_weekday(_HE_WD[m["wd"]], now, bool(m["last"]))),
        (rf"{_NB}{_P}יום\s+(?P<l>[אבגדהוש])'", lambda m: _past_weekday(_HE_LETTER_WD[m["l"]], now, False)),
        (rf"{_NB}[ו]?(?:ב|מ)?שבת(?:\s+(?P<last>שעברה|שעבר|האחרונה|הקודמת))?{_NE}",
         lambda m: _past_weekday(5, now, bool(m["last"]))),
        (rf"\b(?:on\s+)?(?:(?P<last>last|past|this)\s+)?(?P<wd>{wd_en})s?\b",
         lambda m: _past_weekday(_EN_WD[m["wd"]], now, m["last"] in ("last", "past"))),
        # weeks / months / years
        (rf"{_NB}{_P}(?:ה)?שבוע\s+(?:שעבר|הקודם){_NE}|\blast week\b",
         lambda m: (ws - timedelta(days=7), ws)),
        (rf"{_NB}[ו]?השבוע{_NE}|\bthis week\b", lambda m: (ws, tomorrow)),
        (rf"{_NB}{_P}(?:ה)?חודש\s+(?:שעבר|הקודם){_NE}|\blast month\b", lambda m: (prev_month0, month0)),
        (rf"{_NB}[ו]?החודש{_NE}|\bthis month\b", lambda m: (month0, tomorrow)),
        (rf"{_NB}{_P}(?:ה)?שנה\s+(?:שעברה|הקודמת){_NE}|\blast year\b",
         lambda m: (today.replace(year=today.year - 1, month=1, day=1), today.replace(month=1, day=1))),
        (rf"{_NB}[ו]?השנה{_NE}|\bthis year\b", lambda m: (today.replace(month=1, day=1), tomorrow)),
    ]


_SINCE = re.compile(rf"(?:{_NB}מאז\s*|\bsince\s+)$")


def parse_when(text: str, now: datetime | None = None) -> tuple[When | None, str]:
    """(the time range the question names, the question with those words removed). None = no range named.
    A day phrase plus a part of the day ("אתמול בערב", "yesterday evening") narrow each other; "this morning",
    "הערב" mean today; "מאז" / "since" stretch the range to now."""
    now = now or datetime.now()
    t = soft(text)
    found: tuple[datetime, datetime] | None = None
    phrase, since, center = "", False, None
    for rx, fn in _rules(now):
        m = re.search(rx, t)
        if not m:
            continue
        r = fn(m)
        if r is None:
            continue
        if len(r) == 3:
            center = (r[2] + timedelta(hours=12)).timestamp()
            r = r[:2]
        before = t[:m.start()]
        sm = _SINCE.search(before)
        if sm:
            since = True
            before = before[:sm.start()]
        found, phrase = r, m.group(0).strip()
        t = before + " " + t[m.end():]
        break
    part = None
    for table in (_HE_PARTS, _EN_PARTS):
        for words, name in sorted(table.items(), key=lambda kv: -len(kv[0])):
            m = re.search(rf"{_NB}{re.escape(words)}{_NE}", t)
            if m:
                part = name
                phrase = (phrase + " " + m.group(0)).strip()
                t = t[:m.start()] + " " + t[m.end():]
                break
        if part:
            break
    if part is None and found is None:
        return None, text
    if part is not None:
        today_only = part.endswith("!")
        h0, h1 = DAY_PARTS[part.rstrip("!")]
        base = _day(now) if (found is None or today_only) else found[0]
        if found is not None and not today_only and found[1] - found[0] > timedelta(days=1):
            pass                                       # "last week in the evening": keep the days, no hour cut
        else:
            found = (base + timedelta(hours=h0), base + timedelta(hours=h1))
    start, end = found
    if since:
        end = max(end, now + timedelta(minutes=1))
    return When(start.timestamp(), end.timestamp(), phrase, since, center), " ".join(t.split())
