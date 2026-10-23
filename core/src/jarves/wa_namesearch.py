"""Forgiving, ranked search over WhatsApp chat / contact DISPLAY NAMES (Issue #17). Pure and deterministic: no model, no
network, no message text. Used by `whatsapp_bridge.resolve()` (read tools), `whatsapp_find_chat` and `jarvis whatsapp find`.

What it understands, best match first (the score is the rank, `reason` is shown to the owner):
  100 exact          the whole name (Hebrew prefixes ל/מ/ה/ש/של... on the query are ignored)
   90 starts with    the name starts with the words typed
   70 word match     every typed word is a word of the name (prefixes ה/ו/ב/ל/מ/ש/כ/של; 65 for spelling variants such as
                     רשימת/רשימה, plene ו/י, plural ות/ים)
   60 starts with    the name starts with the typed letters (inside the first word)
   60 pattern        the owner's pattern matched (see below)
   50 contains       the typed letters are inside the name
   40 similar (typo) every word is within edit distance 1 (words of 4+ letters) / 2 (8+ letters) of a word of the name
   30 similar (keyboard layout)   the query typed with the wrong keyboard (Hebrew <-> English letters)
   25 similar (spelling)          Latin name vs Hebrew name by sound (dana ~ דנה), consonant skeleton, no model

Pattern mini-language (also in the guide): `*` any text, `?` one letter, "exact words" (whole words), `a|b` either,
leading `^` starts with, trailing `$` ends with; several words = all of them. `/regex/` (or regex=True) is a regular
expression run by the small LINEAR-TIME engine below (no backreferences / lookaround, length and size limits): it can
never backtrack catastrophically, and an invalid pattern raises QueryError with an English and a Hebrew message."""
from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from . import whatsapp_policy as pol

MAX_QUERY = 80            # characters; longer is refused with a clear message
MAX_PROG = 240            # compiled regex instructions
MAX_REPEAT = 30           # {n,m} bound
MAX_NAME = 90             # characters of a name given to the matcher
DEADLINE_S = 1.0          # total time for one search; the engine is linear, this is a second guard
MAX_RESULTS = 10

_HE_PREFIX = ("ל", "מ", "של", "ש", "ה", "ו", "ב", "כ")
_FINALS = str.maketrans("ךםןףץ", "כמנפצ")
_HE_WORD = re.compile(r"[א-ת]")
_HE_ANY = re.compile(r"[א-ת]")
_DROP = "'’‘`´׳״\""          # inside a word these vanish (geresh, gershayim, apostrophes): צ'אט -> צאט


class QueryError(ValueError):
    """An unusable search pattern; `.en` / `.he` are the messages for the owner."""

    def __init__(self, en: str, he: str) -> None:
        super().__init__(en)
        self.en, self.he = en, he


@dataclass
class Hit:
    chat: dict
    score: int
    reason: str


# ---------------------------------------------------------------------------------------------------- normalisation
def norm(s: str) -> str:
    """Comparable form of a name: invisible/bidi characters, niqqud, emoji/symbols, punctuation removed (apostrophes and
    geresh vanish inside words), case folded, Hebrew final letters unified, spaces collapsed."""
    out = []
    for ch in unicodedata.normalize("NFKD", pol.clean_text(str(s or ""))[0]):
        if ch in _DROP:
            continue
        cat = unicodedata.category(ch)
        if cat[0] in "MSC":
            continue
        out.append(" " if cat[0] in "PZ" else ch)
    return " ".join("".join(out).casefold().translate(_FINALS).split())


def word_forms(w: str) -> set[str]:
    """A word and its Hebrew-prefix-stripped forms (up to two stacked: ולאשתי -> אשתי)."""
    out, frontier = {w}, {w}
    for _ in range(2):
        nxt = set()
        for x in frontier:
            for p in sorted(_HE_PREFIX, key=len, reverse=True):
                if x.startswith(p) and len(x) - len(p) >= 2 and _HE_WORD.match(x[len(p)]):
                    nxt.add(x[len(p):])
        out |= nxt
        frontier = nxt
    return out


def canon(w: str) -> str:
    """Spelling-variant key of a Hebrew word: construct-state/feminine ת~ה ending, plural ות/ים, plene ו/י dropped
    (not the first letter). רשימת = רשימה, קניות = קניה. Non-Hebrew words are returned unchanged."""
    if not _HE_ANY.search(w):
        return w
    if len(w) >= 4 and w.endswith(("ות", "ים")):
        w = w[:-2]
    elif len(w) >= 4 and w[-1] in "תה":
        w = w[:-1]
    k = w[:1] + "".join(c for c in w[1:] if c not in "וי")
    return k if len(k) >= 2 else w


def _damerau(a: str, b: str, limit: int) -> int:
    """Optimal-string-alignment distance, or limit+1 when it exceeds `limit`."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2: list[int] | None = None
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            c = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + c)
            if prev2 is not None and i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1]


def tolerance(word: str) -> int:
    n = len(word)
    return 2 if n >= 8 else 1 if n >= 4 else 0


# ---------------------------------------------------------------------------------------------- keyboard / spelling
_HE_KEYS = {"q": "/", "w": "'", "e": "ק", "r": "ר", "t": "א", "y": "ט", "u": "ו", "i": "ן", "o": "ם", "p": "פ",
            "a": "ש", "s": "ד", "d": "ג", "f": "כ", "g": "ע", "h": "י", "j": "ח", "k": "ל", "l": "ך", ";": "ף",
            "z": "ז", "x": "ס", "c": "ב", "v": "ה", "b": "נ", "n": "מ", "m": "צ", ",": "ת", ".": "ץ"}
_EN_KEYS = {v: k for k, v in _HE_KEYS.items() if v not in "/'"}


def keyboard_swap(q: str) -> str | None:
    """The query as if typed on the other layout, when it is made of one script only (letters , . ;)."""
    raw = q.strip().casefold()
    if len(raw) < 3:
        return None
    if all(c in _HE_KEYS or c == " " for c in raw) and any(c.isalpha() for c in raw):
        return norm("".join(_HE_KEYS.get(c, " ") for c in raw))
    if all(c in _EN_KEYS or c in " ךםןףץ" or "א" <= c <= "ת" for c in raw):
        return norm("".join(_EN_KEYS.get(c, " ") for c in raw)) or None
    return None


_HE_SOUND = {"ב": "b", "ג": "g", "ד": "d", "ז": "z", "ח": "k", "כ": "k", "ק": "k", "ט": "t", "ת": "t", "ל": "l",
             "מ": "m", "נ": "n", "ס": "s", "ש": "s", "פ": "p", "צ": "c", "ר": "r"}      # א ע ה ו י are vowels: dropped
_LAT_DIGRAPH = (("sh", "s"), ("ch", "k"), ("kh", "k"), ("th", "t"), ("ts", "c"), ("tz", "c"), ("zh", "z"))
_LAT_SOUND = {"b": "b", "v": "b", "g": "g", "j": "g", "d": "d", "z": "z", "k": "k", "c": "k", "q": "k", "t": "t",
              "l": "l", "m": "m", "n": "n", "s": "s", "p": "p", "f": "p", "r": "r", "x": "ks"}


def _squash(s: str) -> str:
    return re.sub(r"(.)\1+", r"\1", s)


def sound_keys(w: str) -> frozenset[str]:
    """Consonant skeleton(s) of a word in a script-neutral alphabet (dana and דנה are both 'dn')."""
    if _HE_ANY.search(w):
        return frozenset({_squash("".join(_HE_SOUND.get(c, "") for c in w))} - {""})
    if not w.isascii():
        return frozenset()
    s, i, out = w, 0, []
    while i < len(s):
        for d, v in _LAT_DIGRAPH:
            if s.startswith(d, i):
                out.append(v)
                i += 2
                break
        else:
            out.append(_LAT_SOUND.get(s[i], ""))
            i += 1
    k = _squash("".join(out))
    keys = {k}
    if w.startswith("h"):                      # Hana = חנה but Hadar = הדר
        keys.add(_squash("k" + k))
    return frozenset(keys - {""})


# ---------------------------------------------------------------------------------------------------- name profile
@dataclass(frozen=True)
class _Name:
    text: str
    words: tuple[str, ...]
    forms: tuple[frozenset, ...]      # per word: prefix-stripped forms
    canons: tuple[frozenset, ...]
    keys: tuple[frozenset, ...]


@lru_cache(maxsize=2048)
def _profile(display: str) -> _Name:
    t = norm(display)[:MAX_NAME]
    ws = tuple(t.split())
    fs = tuple(frozenset(word_forms(w)) for w in ws)
    return _Name(t, ws, fs, tuple(frozenset(canon(f) for f in x) for x in fs), tuple(sound_keys(w) for w in ws))


# ---------------------------------------------------------------------------------------------------- plain engine
def _score_plain(nm: _Name, query: str, qvariants: set[str]) -> tuple[int, str] | None:
    """(score, reason) of one name against a normalised plain query, or None."""
    t = nm.text
    if not t:
        return None
    if t in qvariants:
        return 100, "exact"
    for v in qvariants:
        if t.startswith(v + " "):
            return 90, "starts with"
    qtoks = query.split()
    qforms = [word_forms(x) for x in qtoks]
    if qtoks and all(any(f & wf for wf in nm.forms) for f in qforms):
        return 70, "word match"
    if qtoks and all(any(f & wf for wf in nm.forms) or any({canon(x) for x in f} & wc for wc in nm.canons)
                     for f in qforms):
        return 65, "word match"
    for v in qvariants:
        if len(v) >= 2 and t.startswith(v):
            return 60, "starts with"
    for v in qvariants:
        if len(v) >= 2 and v in t:
            return 50, "contains"
    return None


def _score_typo(nm: _Name, qtoks: list[str]) -> tuple[int, str] | None:
    """Every query word is (nearly) a word of the name; at least one with a typo. Words under 4 letters must be exact."""
    if not qtoks or not nm.words:
        return None
    total = 0
    for q in qtoks:
        best = None
        qf = word_forms(q)
        for forms, canons in zip(nm.forms, nm.canons):
            for a in qf:
                tol = tolerance(q)
                if a in forms or canon(a) in canons:
                    best = 0
                    break
                if tol:
                    for f in forms:
                        d = _damerau(a, f, tol)
                        if d <= tol and (best is None or d < best):
                            best = d
                    ca = canon(a)
                    d = min((_damerau(ca, c, tol) for c in canons if len(c) >= 3 and len(ca) >= 3), default=tol + 1)
                    if d <= tol and (best is None or d < best):
                        best = max(d, 1)
            if best == 0:
                break
        if best is None:
            return None
        total += best
    return (40 - min(total, 4), "similar (typo)") if total else None


def _score_spelling(nm: _Name, qtoks: list[str]) -> tuple[int, str] | None:
    """Latin <-> Hebrew by sound: each query word has the same consonant skeleton as a name word of the OTHER script."""
    if not qtoks:
        return None
    for q in qtoks:
        qk = sound_keys(q)
        qhe = bool(_HE_ANY.search(q))
        if not qk or all(len(k) < 2 for k in qk):
            return None
        if not any(k in qk for w, wk in zip(nm.words, nm.keys) for k in wk if bool(_HE_ANY.search(w)) != qhe):
            return None
    return 25, "similar (spelling)"


# ---------------------------------------------------------------------------------------------- safe regex engine
# A small Thompson/Pike NFA simulation: time is O(len(text) * len(program)) whatever the pattern; there is no
# backtracking, no backreference and no lookaround, so a pattern can never run away.
def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.category(c).startswith("M")
                   ).casefold().translate(_FINALS)


class _Parser:
    def __init__(self, src: str) -> None:
        self.s, self.i = src, 0

    def err(self, en: str, he: str) -> QueryError:
        return QueryError(f"Invalid pattern at position {self.i + 1}: {en}", f"תבנית לא תקינה במקום {self.i + 1}: {he}")

    def parse(self):
        node = self.alt()
        if self.i < len(self.s):
            raise self.err("unmatched ')'", "סוגר ')' בלי פותח")
        return node

    def alt(self):
        alts = [self.seq()]
        while self.i < len(self.s) and self.s[self.i] == "|":
            self.i += 1
            alts.append(self.seq())
        return alts[0] if len(alts) == 1 else ("alt", alts)

    def seq(self):
        items = []
        while self.i < len(self.s) and self.s[self.i] not in "|)":
            atom = self.atom()
            atom = self.quant(atom)
            items.append(atom)
        return ("seq", items)

    def quant(self, atom):
        if self.i >= len(self.s):
            return atom
        c = self.s[self.i]
        lo = hi = None
        if c in "*+?":
            lo, hi = {"*": (0, None), "+": (1, None), "?": (0, 1)}[c]
            self.i += 1
        elif c == "{":
            m = re.compile(r"\{(\d+)(,(\d*))?\}").match(self.s, self.i)
            if not m:
                raise self.err("bad {n,m} repeat", "חזרה {n,m} לא תקינה")
            lo = int(m.group(1))
            hi = (None if m.group(3) == "" else int(m.group(3))) if m.group(2) else lo
            if lo > MAX_REPEAT or (hi is not None and (hi > MAX_REPEAT or hi < lo)):
                raise self.err(f"repeat counts must be at most {MAX_REPEAT}", f"מספר חזרות עד {MAX_REPEAT} בלבד")
            self.i = m.end()
        else:
            return atom
        if atom[0] in ("bol", "eol"):
            raise self.err("nothing to repeat", "אין מה לחזור עליו")
        if self.i < len(self.s) and self.s[self.i] == "?":          # lazy marker: same answer for 'does it match'
            self.i += 1
        if self.i < len(self.s) and self.s[self.i] in "*+{":
            raise self.err("nested repeat is not supported", "חזרה בתוך חזרה אינה נתמכת")
        return ("rep", atom, lo, hi)

    def atom(self):
        c = self.s[self.i]
        if c in "*+?":
            raise self.err("nothing to repeat", "אין מה לחזור עליו")
        self.i += 1
        if c == "(":
            if self.s.startswith("?", self.i):
                if self.s.startswith("?:", self.i):
                    self.i += 2
                else:
                    raise self.err("lookaround / named groups are not supported", "מבט קדימה/אחורה וקבוצות עם שם אינם נתמכים")
            node = self.alt()
            if self.i >= len(self.s) or self.s[self.i] != ")":
                raise self.err("missing ')'", "חסר סוגר ')'")
            self.i += 1
            return node
        if c == ".":
            return ("any",)
        if c == "^":
            return ("bol",)
        if c == "$":
            return ("eol",)
        if c == "[":
            return self.cls()
        if c == "\\":
            return self.esc(False)
        if c == "{" and re.compile(r"\{\d").match(self.s, self.i - 1):
            raise self.err("nothing to repeat", "אין מה לחזור עליו")
        return ("chr", _fold(c))

    def esc(self, in_cls: bool):
        if self.i >= len(self.s):
            raise self.err("pattern ends with a backslash", "התבנית נגמרת בלוכסן הפוך")
        c = self.s[self.i]
        self.i += 1
        if c in "dwsDWS":
            return ("esc", c)
        if c.isdigit() or c in "bBAZzGk":
            raise self.err(f"\\{c} (backreference / word boundary) is not supported", f"\\{c} אינו נתמך")
        return ("chr", _fold({"n": "\n", "t": "\t"}.get(c, c)))

    def cls(self):
        neg = self.s.startswith("^", self.i)
        self.i += neg
        items: list = []
        first = True
        while True:
            if self.i >= len(self.s):
                raise self.err("missing ']'", "חסר סוגר ']'")
            c = self.s[self.i]
            if c == "]" and not first:
                self.i += 1
                break
            first = False
            self.i += 1
            if c == "\\":
                e = self.esc(True)
                items.append(e if e[0] == "esc" else e[1])
                continue
            if self.i + 1 < len(self.s) and self.s[self.i] == "-" and self.s[self.i + 1] != "]":
                hi = self.s[self.i + 1]
                self.i += 2
                if hi < c:
                    raise self.err("bad range in [...]", "טווח לא תקין ב-[...]")
                items.append((_fold(c), _fold(hi)))
            else:
                items.append(_fold(c))
        return ("set", tuple(items), neg)


def _esc_match(e: str, ch: str) -> bool:
    base = e.lower()
    ok = ch.isdigit() if base == "d" else (ch.isalnum() or ch == "_") if base == "w" else ch.isspace()
    return ok if e.islower() else not ok


def _set_match(items, neg: bool, ch: str) -> bool:
    hit = False
    for it in items:
        if isinstance(it, tuple) and it and it[0] == "esc":
            hit = _esc_match(it[1], ch)
        elif isinstance(it, tuple):
            hit = it[0] <= ch <= it[1]
        else:
            hit = it == ch
        if hit:
            break
    return hit != neg


class Regex:
    """Compiled safe regex; `search(text)` is true when the pattern matches anywhere in `text` (already normalised)."""

    def __init__(self, src: str) -> None:
        if len(src) > MAX_QUERY:
            raise QueryError(f"The pattern is too long (at most {MAX_QUERY} characters).",
                             f"התבנית ארוכה מדי (עד {MAX_QUERY} תווים).")
        if not src:
            raise QueryError("The pattern is empty.", "התבנית ריקה.")
        self.src = src
        self.prog: list[tuple] = []
        ast = _Parser(src).parse()
        self._emit(ast)
        self.prog.append(("match",))

    def _push(self, ins: tuple) -> int:
        if len(self.prog) >= MAX_PROG:
            raise QueryError("The pattern is too complex (too many repeats or groups).",
                             "התבנית מורכבת מדי (יותר מדי חזרות או קבוצות).")
        self.prog.append(ins)
        return len(self.prog) - 1

    def _emit(self, n) -> None:
        k = n[0]
        if k == "seq":
            for x in n[1]:
                self._emit(x)
        elif k == "chr" or k == "any" or k == "bol" or k == "eol" or k == "set" or k == "esc":
            self._push(n)
        elif k == "alt":
            jumps = []
            for idx, a in enumerate(n[1]):
                if idx < len(n[1]) - 1:
                    sp = self._push(("split", 0, 0))
                    self.prog[sp] = ("split", sp + 1, 0)
                    self._emit(a)
                    jumps.append(self._push(("jmp", 0)))
                    self.prog[sp] = ("split", sp + 1, len(self.prog))
                else:
                    self._emit(a)
            for j in jumps:
                self.prog[j] = ("jmp", len(self.prog))
        elif k == "rep":
            _, a, lo, hi = n
            for _ in range(lo):
                self._emit(a)
            if hi is None:
                sp = self._push(("split", 0, 0))
                self._emit(a)
                self._push(("jmp", sp))
                self.prog[sp] = ("split", sp + 1, len(self.prog))
            else:
                splits = []
                for _ in range(hi - lo):
                    splits.append(self._push(("split", 0, 0)))
                    self._emit(a)
                for sp in splits:
                    self.prog[sp] = ("split", sp + 1, len(self.prog))

    def _add(self, pcs: set, pc: int, i: int, n: int) -> None:
        stack = [pc]
        seen: set[int] = set()
        while stack:
            p = stack.pop()
            if p in seen:
                continue
            seen.add(p)
            ins = self.prog[p]
            op = ins[0]
            if op == "jmp":
                stack.append(ins[1])
            elif op == "split":
                stack.append(ins[2])
                stack.append(ins[1])
            elif op == "bol":
                if i == 0:
                    stack.append(p + 1)
            elif op == "eol":
                if i == n:
                    stack.append(p + 1)
            else:
                pcs.add(p)

    def search(self, text: str) -> bool:
        n = len(text)
        cur: set[int] = set()
        for i in range(n + 1):
            self._add(cur, 0, i, n)                          # unanchored: a new attempt may start at every position
            if any(self.prog[p][0] == "match" for p in cur):
                return True
            if i == n:
                break
            ch, nxt = text[i], set()
            for p in cur:
                ins = self.prog[p]
                op = ins[0]
                if (op == "chr" and ins[1] == ch) or (op == "any" and ch != "\n") or \
                        (op == "set" and _set_match(ins[1], ins[2], ch)) or (op == "esc" and _esc_match(ins[1], ch)):
                    self._add(nxt, p + 1, i + 1, n)
            cur = nxt
        return False


# ---------------------------------------------------------------------------------------------------- mini-language
def looks_like_pattern(q: str) -> bool:
    """True when the owner used the mini-language or /regex/ (a stray '?' or '"' in a normal sentence is not one)."""
    s = q.strip()
    if len(s) > 2 and s.startswith("/") and s.endswith("/"):
        return True
    if "*" in s or "|" in s or s.startswith("^") or (s.endswith("$") and len(s) > 1):
        return True
    if re.search(r"(?:^|\s)\"[^\"]+\"(?:\s|$)", s):
        return True
    return bool(re.search(r"\w\?[\w?]", s))


def _term_regex(term: str, quoted: bool) -> str:
    out = []
    for c in term:
        out.append(".*" if c == "*" else "[^ ]" if c == "?" else re.escape(_fold(c)) if not c.isalnum() else _fold(c))
    body = "".join(out)
    return f"(?:^| ){body}(?: |$)" if quoted else body


def _mini_to_programs(q: str) -> list[list[Regex]]:
    """Alternatives (a|b) of AND-lists of compiled terms."""
    alts: list[str] = []
    cur, inq = [], False
    for c in q:
        if c == '"':
            inq = not inq
        if c == "|" and not inq:
            alts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
    alts.append("".join(cur))
    progs = []
    for a in alts:
        a = a.strip()
        start = a.startswith("^")
        end = a.endswith("$") and len(a) > (1 if not start else 2)
        core = a[start:len(a) - end if end else len(a)].strip()
        terms = [(m.group(1) or m.group(2), m.group(1) is not None)
                 for m in re.finditer(r'"([^"]+)"|(\S+)', core)]
        terms = [(t.strip(), qd) for t, qd in terms if t and t.strip()]
        if not terms:
            continue
        if start or end:                                      # anchored: the words are ONE phrase
            body = " ".join(_term_regex(t, False) for t, _ in terms)
            progs.append([Regex(("^" if start else "") + body + ("$" if end else ""))])
        else:
            progs.append([Regex(_term_regex(t, qd)) for t, qd in terms])
    if not progs:
        raise QueryError("The pattern has no words in it.", "בתבנית אין אף מילה.")
    return progs


# ---------------------------------------------------------------------------------------------------------- search
def _tie(c: dict):
    return (-float(c.get("last_ts") or 0), str(c.get("name") or ""))


def search(chats: list[dict], query: str, *, regex: bool = False, patterns: bool = True,
           limit: int = MAX_RESULTS) -> list[Hit]:
    """Ranked chat-name hits (best first, at most `limit` <= 10). Raises QueryError for an unusable pattern."""
    q = (query or "").strip()
    if len(q) > MAX_QUERY:
        raise QueryError(f"The search text is too long (at most {MAX_QUERY} characters).",
                         f"טקסט החיפוש ארוך מדי (עד {MAX_QUERY} תווים).")
    if not q:
        return []
    limit = max(1, min(int(limit), MAX_RESULTS))
    t_end = time.monotonic() + DEADLINE_S
    hits: list[Hit] = []
    is_re = regex or (patterns and len(q) > 2 and q.startswith("/") and q.endswith("/"))
    if is_re or (patterns and looks_like_pattern(q)):
        if is_re:
            src = q[1:-1] if (q.startswith("/") and q.endswith("/") and len(q) > 1) else q
            progs = [[Regex(src)]]
        else:
            progs = _mini_to_programs(q)
        for c in chats:
            if time.monotonic() > t_end:
                raise QueryError("The pattern took too long; make it simpler.", "התבנית לקחה יותר מדי זמן, כדאי לפשט אותה.")
            nm = _profile(c.get("name") or "")
            if nm.text and any(all(r.search(nm.text) for r in alt) for alt in progs):
                hits.append(Hit(c, 60, "pattern"))
    else:
        nq = norm(q)
        qvars = _variants(nq)
        qtoks = nq.split()
        swapped = keyboard_swap(q)
        sw_vars = _variants(swapped) if swapped else set()
        for c in chats:
            nm = _profile(c.get("name") or "")
            if not nm.text:
                continue
            r = _score_plain(nm, nq, qvars) or _score_typo(nm, qtoks)
            if r is None and swapped:
                s2 = _score_plain(nm, swapped, sw_vars)
                if s2 and s2[0] >= 50:
                    r = (30, "similar (keyboard layout)")
            if r is None:
                r = _score_spelling(nm, qtoks)
            if r:
                hits.append(Hit(c, r[0], r[1]))
    hits.sort(key=lambda h: (-h.score, _tie(h.chat)))
    return hits[:limit]


def _variants(nq: str) -> set[str]:
    out = {nq}
    for p in sorted(_HE_PREFIX, key=len, reverse=True):     # "לדנה" / "של דנה" -> "דנה"
        if nq.startswith(p + " "):
            out.add(nq[len(p) + 1:])
        elif nq.startswith(p) and len(nq) > len(p) + 1 and _HE_WORD.match(nq[len(p)]):
            out.add(nq[len(p):])
    return {v for v in out if v}


def top_group(hits: list[Hit]) -> list[Hit]:
    """The hits that share the best score: the only ones a read tool may act on (one = used, several = ask the owner)."""
    return [h for h in hits if hits and h.score == hits[0].score]
