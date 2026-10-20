"""SW-V4 G (#17) conversation recall: the text rules (Hebrew prefixes, final letters, NFC/NFD, niqqud, geresh, mixed
scripts, transliteration, concepts), the date phrases in both languages, and the real SQLite FTS5 index in tmp dirs
(incremental on commit, tool summaries, notes / reports / workspace memory, rebuild, repair, size cap, snippets,
ranking, the short answer). No mocks of the index; the clock is passed in."""
from __future__ import annotations

import sqlite3
import unicodedata
from datetime import datetime, timedelta

import pytest

from jarves import recall
from jarves import recall_text as rt
from jarves.store import Store

NOW = datetime(2026, 10, 20, 15, 0)          # Tuesday


def ts(days: float, hm: str = "12:00") -> float:
    h, m = map(int, hm.split(":"))
    return (NOW.replace(hour=h, minute=m) - timedelta(days=days)).timestamp()


def voice(st: Store, proc: str, t: float, *turns: tuple[str, str]) -> str:
    st.ingest({"ts": t, "code": "LIVE_STARTED", "session": proc, "msg": "live (gpt-live-1 -> gpt-6-luna)"})
    for i, (role, text) in enumerate(turns):
        st.ingest({"ts": t + 2 + i, "session": proc, "text": text,
                   "code": "LIVE_USER_SAID" if role == "user" else "LIVE_JARVES_SAID"})
    st.ingest({"ts": t + 3 + len(turns), "code": "VOICE_ENDED", "session": proc, "reason": "idle",
               "voice_seconds": 10.0})
    return st.rows("SELECT session_id FROM turns ORDER BY id DESC LIMIT 1")[0]["session_id"]


@pytest.fixture
def st(tmp_path):
    s = Store(tmp_path / "store.db")
    s.indexer = recall.Indexer()
    yield s
    s.close()


def find(st, q, **kw):
    with st._lock:
        return recall.search(st._db, q, now=NOW, **kw)


# ====================================================================== text rules
@pytest.mark.parametrize("word,stem", [
    ("לרופא", "רופא"), ("והרופא", "רופא"), ("וכשהרופא", "רופא"), ("שהמכונית", "מכונית"), ("מהבית", "בית"),
    ("בבית", "בית"), ("ולדנה", "דנה"), ("לווטרינר", "וטרינר"), ("הווטרינר", "וטרינר")])
def test_hebrew_clitic_prefixes_become_forms(word, stem):
    assert stem in rt.forms(rt._fold(word))


def test_prefix_grammar_keeps_the_stem_letters():
    assert "וסכ" not in rt.forms(rt._fold("במוסך")) and "מוסכ" in rt.forms(rt._fold("במוסך"))
    assert rt.prefix_forms("מה") == []                          # nothing left to search


@pytest.mark.parametrize("a,b", [("רופאים", "רופא"), ("מכוניות", "מכונ"), ("רופאה", "רופא")])
def test_light_hebrew_suffixes(a, b):
    assert b in rt.forms(rt._fold(a))


def test_final_letters_niqqud_cantillation_nfd_and_geresh_normalise_to_one_token():
    plain = rt.tokens("ספרים מוסך דנה ג'ירפה צה\"ל")
    pointed = rt.tokens("סְפָרִים מוּסָךְ דָּנָה ג׳ירפה צה״ל")              # niqqud, Hebrew geresh / gershayim
    nfd = rt.tokens(unicodedata.normalize("NFD", "סְפָרִים מוּסָךְ דָּנָה ג'ירפה צה\"ל"))
    cant = rt.tokens("סְפָרִ֑ים מוּסָ֖ךְ דָּנָ֣ה ג'ירפה צה\"ל")               # cantillation marks too
    assert plain == pointed == nfd == cant == ["ספרימ", "מוסכ", "דנה", "גירפה", "צהל"]
    assert rt.tokens("ﬠﬡﬢ") == rt.tokens(unicodedata.normalize("NFKD", "ﬠﬡﬢ"))     # presentation forms


def test_latin_accents_case_apostrophes_and_mixed_scripts():
    assert rt.tokens("Café NAÏVE Yael's don't") == ["cafe", "naive", "yael", "dont"]
    assert rt.tokens("קניתי iPhone חדש ב-3000 ש\"ח") == ["קניתי", "iphone", "חדש", "3000", "שח"]
    assert rt.tokens("לGoogle") == ["google"]                 # the lone prefix letter is dropped


@pytest.mark.parametrize("he,en", [("מיכל", "Michal"), ("חיים", "Chaim"), ("דוד", "David"), ("יוסי", "Yossi"),
                                   ("איגל", "Igal"), ("יעל", "Yael"), ("רונית", "Ronit"), ("צבי", "Tzvi"),
                                   ("דנה", "Dana"), ("שרה", "Sarah")])
def test_transliteration_keys_meet_across_scripts(he, en):
    assert rt.cross_keys(en.lower()) & rt.keys(he)
    assert rt.cross_keys(he) & rt.keys(en.lower())


def test_query_terms_drop_question_words_and_expand_concepts():
    terms = rt.query_terms("מה אמרתי לך על הרופא של אמא?")
    words = [t.word for t in terms]
    assert words == ["הרופא", "אמא"]
    assert "doctor" in terms[0].forms and "mom" in terms[1].body
    assert not terms[0].phon                                    # a concept word needs no transliteration
    assert [t.word for t in rt.query_terms("what did I tell you about the dentist")] == ["dentist"]


def test_speaker_hint():
    assert rt.speaker_hint("מה אמרתי על הרופא?") == "user"
    assert rt.speaker_hint("מה אמרת לי על הגשם?") == "assistant"
    assert rt.speaker_hint("what did you recommend") == "assistant"
    assert rt.speaker_hint("what happened with the car") is None
    assert rt.speaker_hint("what did you say about it") == "assistant"
    assert rt.speaker_hint("what did I say") == "user"


# ====================================================================== dates
def rng(q):
    w, rest = rt.parse_when(q, NOW)
    return (datetime.fromtimestamp(w.start), datetime.fromtimestamp(w.end), rest) if w else (None, None, rest)


@pytest.mark.parametrize("q,start,end", [
    ("מה דיברנו היום", "2026-10-20 00:00", "2026-10-21 00:00"),
    ("מה דיברנו אתמול על הרכב", "2026-10-19 00:00", "2026-10-20 00:00"),
    ("what did we say yesterday", "2026-10-19 00:00", "2026-10-20 00:00"),
    ("שלשום", "2026-10-18 00:00", "2026-10-19 00:00"),
    ("the day before yesterday", "2026-10-18 00:00", "2026-10-19 00:00"),
    ("what did we talk about on Tuesday", "2026-10-13 00:00", "2026-10-14 00:00"),
    ("ביום שלישי שעבר", "2026-10-13 00:00", "2026-10-14 00:00"),
    ("ביום ג' על הכלב", "2026-10-13 00:00", "2026-10-14 00:00"),
    ("ביום ראשון", "2026-10-18 00:00", "2026-10-19 00:00"),
    ("last sunday", "2026-10-11 00:00", "2026-10-12 00:00"),
    ("בשבת", "2026-10-17 00:00", "2026-10-18 00:00"),
    ("last week about the car", "2026-10-11 00:00", "2026-10-18 00:00"),
    ("בשבוע שעבר", "2026-10-11 00:00", "2026-10-18 00:00"),
    ("השבוע", "2026-10-18 00:00", "2026-10-21 00:00"),
    ("this month", "2026-10-01 00:00", "2026-10-21 00:00"),
    ("בחודש שעבר", "2026-09-01 00:00", "2026-10-01 00:00"),
    ("לפני שלושה ימים", "2026-10-16 00:00", "2026-10-19 00:00"),
    ("3 days ago", "2026-10-16 00:00", "2026-10-19 00:00"),
    ("לפני שבועיים", "2026-10-03 00:00", "2026-10-10 00:00"),
    ("two weeks ago", "2026-10-03 00:00", "2026-10-10 00:00"),
    ("הבוקר", "2026-10-20 05:00", "2026-10-20 12:00"),
    ("this morning", "2026-10-20 05:00", "2026-10-20 12:00"),
    ("אתמול בערב על הטיסה", "2026-10-19 17:00", "2026-10-19 23:00"),
    ("yesterday evening", "2026-10-19 17:00", "2026-10-19 23:00"),
    ("last night", "2026-10-19 19:00", "2026-10-20 05:00"),
    ("ב-14 באוקטובר", "2026-10-14 00:00", "2026-10-15 00:00"),
    ("on 14 October", "2026-10-14 00:00", "2026-10-15 00:00"),
    ("October 14th", "2026-10-14 00:00", "2026-10-15 00:00"),
    ("14/10", "2026-10-14 00:00", "2026-10-15 00:00"),
    ("ב14.10", "2026-10-14 00:00", "2026-10-15 00:00"),
    ("2026-09-03", "2026-09-03 00:00", "2026-09-04 00:00"),
    ("ב-3 בנובמבר", "2025-11-03 00:00", "2025-11-04 00:00"),      # a future day without a year = last year's
])
def test_time_phrases(q, start, end):
    s, e, _ = rng(q)
    assert (s.strftime("%Y-%m-%d %H:%M"), e.strftime("%Y-%m-%d %H:%M")) == (start, end), q


def test_since_stretches_to_now_and_phrases_leave_the_query():
    s, e, rest = rng("מה אמרתי מאז יום ראשון על הכלב")
    assert s == datetime(2026, 10, 18) and e > NOW and rest.split()[-2:] == ["על", "הכלב"]
    s, e, rest = rng("what did I say since monday")
    assert s == datetime(2026, 10, 19) and e > NOW
    assert rng("מה אמרתי על הרופא")[0] is None                      # no time phrase, no range
    assert rng("שני דברים על הרופא")[0] is None                      # "שני" = two, not Monday
    assert rng("יום הולדת של נועה")[0] is None                       # "יום" alone is not a day


# ====================================================================== the index (real SQLite FTS5)
def test_turns_are_indexed_on_commit_in_the_same_transaction(st):
    sid = voice(st, "p1", ts(1, "18:20"), ("user", "תזכיר לי לקבוע תור לרופא השיניים"), ("assistant", "בסדר."))
    assert st.rows("SELECT COUNT(*) n FROM recall_docs")[0]["n"] == 0      # not before the commit
    st.commit()
    assert st.rows("SELECT COUNT(*) n FROM recall_docs")[0]["n"] == 2
    other = sqlite3.connect(st.path)                                      # another process sees both at once
    assert other.execute("SELECT COUNT(*) FROM recall_fts WHERE recall_fts MATCH 'stems:\"שיניימ\"'").fetchone()[0] == 1
    other.close()
    res = find(st, "מה אמרתי על רופא השיניים?")
    assert res["results"][0]["conversation_id"] == sid and res["results"][0]["role"] == "user"
    voice(st, "p1", ts(0, "09:00"), ("user", "מה עם השיניים?"))
    st.commit()                                                           # incremental: only the new row
    assert st.rows("SELECT COUNT(*) n FROM recall_docs")[0]["n"] == 3


def test_tool_summaries_are_indexed_without_bodies(st):
    t = ts(2)
    st.ingest({"ts": t, "code": "LIVE_STARTED", "session": "p"})
    st.ingest({"ts": t + 1, "code": "LIVE_USER_SAID", "session": "p", "text": "תשלח מייל לדנה"})
    st.ingest({"ts": t + 2, "code": "LIVE_TOOL_CALL", "session": "p", "tool": "gmail_send_draft",
               "args": {"to": "Dana Levi", "subject": "סיכום פגישה", "body": "הסוד הגדול שלי"}})
    st.ingest({"ts": t + 9, "code": "VOICE_ENDED", "session": "p", "reason": "idle"})
    st.commit()
    tools = st.rows("SELECT text FROM recall_docs WHERE kind = 'tool'")
    assert tools == [{"text": "gmail send draft Dana Levi סיכום פגישה"}]
    assert find(st, "הסוד הגדול")["results"] == []
    assert find(st, "gmail draft")["results"][0]["source"] == "tool"


def test_hebrew_forms_cross_script_names_and_concepts_are_found(st):
    a = voice(st, "a", ts(3), ("user", "כשהמכונית של דָּנָה הייתה במוסך"))
    b = voice(st, "b", ts(4), ("user", "Michal invited us to her wedding"))
    c = voice(st, "c", ts(5), ("user", "my dentist moved the appointment"))
    st.commit()
    assert find(st, "מה אמרתי על המכוניות של דנה")["results"][0]["conversation_id"] == a
    assert find(st, "what did I say about the car")["results"][0]["conversation_id"] == a      # concept
    assert find(st, "מה אמרתי על החתונה של מיכל")["results"][0]["conversation_id"] == b       # both ways
    assert find(st, "מה אמרתי על רופא השיניים")["results"][0]["conversation_id"] == c
    assert find(st, "Dana")["results"][0]["conversation_id"] == a                          # transliteration


def test_a_misheard_latin_name_and_the_current_conversation(st):
    tamar = voice(st, "a", ts(6), ("user", "My sister Tamar is flying back from London"))
    voice(st, "t", ts(1), ("user", "טיימר לעשר דקות לפסטה"))          # "טיימר" has the same skeleton (t-m-r)
    st.commit()
    assert find(st, "What did I tell you about Tomer?")["results"][0]["conversation_id"] == tamar   # STT variant
    now_ = voice(st, "b", ts(0, "14:59"), ("user", "what did I tell you about London?"))
    st.commit()
    ids = [r["conversation_id"] for r in find(st, "London", exclude_sessions=(now_,))["results"]]
    assert ids == [tamar]                                       # the question itself is not an earlier conversation


def test_ranking_prefers_coverage_the_speaker_and_the_range(st):
    old = voice(st, "o", ts(9), ("user", "המוסך אמר שהרכב מוכן"))
    new = voice(st, "n", ts(1), ("user", "שטפתי את הרכב"))
    both = voice(st, "b", ts(20), ("user", "המוסך התקשר"), ("assistant", "אז הרכב מוכן בצהריים"))
    tip = voice(st, "t", ts(6), ("user", "איפה לאכול דגים?"), ("assistant", "אני ממליץ על מסעדה ביפו"))
    st.commit()
    top = [r["conversation_id"] for r in find(st, "מה אמרתי על הרכב במוסך")["results"]]
    assert set(top[:2]) == {old, both} and new in top
    assert find(st, "מה דיברנו אתמול על הרכב")["results"][0]["conversation_id"] == new
    assert [r["conversation_id"] for r in find(st, "רכב", speaker="assistant")["results"]] == [both]
    assert find(st, "איזו מסעדה המלצת לי?")["results"][0]["conversation_id"] == tip
    assert find(st, "רכב", since="2026-10-15")["results"][0]["conversation_id"] == new
    assert [r["conversation_id"] for r in find(st, "רכב", since="last week", until="last week")["results"]] == [old]
    with pytest.raises(ValueError):
        find(st, "רכב", since="banana")


def test_date_only_question_lists_that_days_conversations(st):
    a = voice(st, "a", ts(7, "09:00"), ("user", "בוקר טוב, מה התוכניות?"), ("assistant", "יום רגוע."))
    voice(st, "b", ts(6), ("user", "משהו אחר"))
    st.commit()
    res = find(st, "על מה דיברנו ביום שלישי שעבר?")
    assert [r["conversation_id"] for r in res["results"]] == [a] and res["terms"] == []
    assert "דיברנו על" in recall.answer(res, now=NOW)["text"]
    assert find(st, "מה")["reason"] == "no_terms"


def test_snippet_marks_the_matched_words_in_the_original_text():
    text = "אז " * 80 + "תזכיר לי לקבוע תור לרופא השיניים ביום חמישי" + " בסדר" * 40
    terms = rt.query_terms("רופא שיניים")
    s, marks = recall.snippet(text, terms)
    assert len(s) <= recall.SNIPPET_CHARS + 10 and s.startswith("…") and s.endswith("…")
    assert [s[a:b] for a, b in marks] == ["לרופא", "השיניים"]
    s, marks = recall.snippet("short: לרופא", terms)
    assert [s[a:b] for a, b in marks] == ["לרופא"]


def test_answer_is_two_sentences_with_date_and_quote_and_never_invents(st):
    voice(st, "a", ts(1, "18:20"), ("user", "תזכיר לי לקבוע תור לרופא השיניים ביום חמישי"))
    voice(st, "b", ts(5, "08:15"), ("user", "הקוד של השער הוא 451234"))
    voice(st, "c", ts(8, "10:00"), ("user", "My sister Tamar is flying back from London next Sunday after a long "
                                            "trip through Europe with her friends from university and work"))
    st.commit()
    a = recall.answer(find(st, "מה אמרתי על רופא השיניים?"), now=NOW)
    assert a["text"] == "אתמול בשעה 18:20 אמרת: «תזכיר לי לקבוע תור לרופא השיניים ביום חמישי»." and not a["screen_offer"]
    p = recall.answer(find(st, "מה אמרתי על השער?"), now=NOW)
    assert "451234" not in p["text"] and "פרטי" in p["text"] and "להציג לך על המסך?" in p["text"]
    e = recall.answer(find(st, "What did I tell you about Tamar?"), now=NOW)
    assert e["text"].startswith("On Monday 12 October at 10:00 you said: “My sister Tamar") and e["screen_offer"]
    assert e["text"].count(".") + e["text"].count("?") <= 3 and "Shall I show it on the screen?" in e["text"]
    none = recall.answer(find(st, "מה אמרתי על הג'ירפה?"), now=NOW)
    assert none["text"] == "לא מצאתי בשיחות שלנו שום דבר על הג'ירפה." and not none["screen_offer"]
    none = recall.answer(find(st, "what did we say yesterday about giraffes"), now=NOW)
    assert none["text"] == "I found nothing about giraffes in our conversations yesterday."


def test_third_party_text_repeated_by_jarves_is_never_read_aloud(st):
    t = ts(2)
    st.ingest({"ts": t, "code": "LIVE_STARTED", "session": "m"})
    st.ingest({"ts": t + 1, "code": "LIVE_USER_SAID", "session": "m", "text": "מה כתוב במייל מהבנק?"})
    st.ingest({"ts": t + 2, "code": "LIVE_TOOL_CALL", "session": "m", "tool": "gmail_read", "args": {"id": "x"}})
    st.ingest({"ts": t + 4, "code": "LIVE_JARVES_SAID", "session": "m", "text": "הבנק כותב שהמשכנתא אושרה בריבית קבועה"})
    st.ingest({"ts": t + 9, "code": "VOICE_ENDED", "session": "m", "reason": "idle"})
    st.commit()
    res = find(st, "מה אמרת על המשכנתא?")
    assert res["results"][0]["third_party"]
    a = recall.answer(res, now=NOW)
    assert "ריבית" not in a["text"] and a["screen_offer"]


def test_notes_reports_and_workspace_memory_are_searchable_and_follow_changes(tmp_path):
    from jarves.notes import Notes
    from jarves.userdata import open_db
    db = open_db(tmp_path)
    n = Notes(db, clock=lambda: ts(3)).add("רשימת מתנות", "אוזניות לנועה ליום ההולדת", ["family"])
    db.x("INSERT INTO reports (job_id, run_id, title, text, status, created) VALUES (?,?,?,?,?,?)",
         ("j", 1, "Morning briefing", "Heavy rain expected in Haifa", "ok", ts(1)))
    (tmp_path / "workspace" / "memory").mkdir(parents=True)
    (tmp_path / "workspace" / "memory" / "2026-10-18.md").write_text("[s1] learned: the user's dog is Rexi\n",
                                                                     encoding="utf-8")
    rec = recall.Recall(tmp_path, enabled=lambda: True)
    try:
        hit = rec.search("מה רשמתי על המתנה לנועה", now=NOW)["results"][0]
        assert hit["source"] == "note" and hit["link"] == "#/notes"
        assert rec.search("rain in Haifa", now=NOW)["results"][0]["source"] == "report"
        assert rec.search("Rexi", now=NOW)["results"][0]["source"] == "memory"
        Notes(db, clock=lambda: ts(2)).edit(n["id"], text="שעון חכם לנועה", by="user")
        assert rec.search("אוזניות", now=NOW)["results"] == []
        assert rec.search("שעון חכם", now=NOW)["results"][0]["source"] == "note"
        Notes(db).delete(n["id"], by="user")
        assert rec.search("שעון חכם", now=NOW)["results"] == []
    finally:
        rec.close()
        db.close()


def test_rebuild_repair_stats_and_size_cap(tmp_path):
    st = Store(tmp_path / "store.db")
    st.indexer = recall.Indexer(max_docs=1000)
    try:
        for i in range(30):
            voice(st, f"p{i}", ts(30 - i), ("user", f"שיחה מספר {i} על הגינה"), ("assistant", "יפה"))
        st.commit()
        rec = recall.Recall(tmp_path, store=st, enabled=lambda: True)
        s = rec.stats()
        assert s["docs"] == 60 and s["by_kind"] == {"turn": 60} and s["version"] == recall.INDEX_VERSION
        with st._lock:                                          # damage: an FTS row without its doc, a doc without FTS
            st._db.execute("INSERT INTO recall_fts (rowid, body, stems, phon) VALUES (99999, 'x', 'x', '')")
            st._db.execute("DELETE FROM recall_fts WHERE rowid = (SELECT MIN(id) FROM recall_docs)")
            st._db.execute("UPDATE turns SET text = NULL WHERE id = 2")
            st._db.commit()
        r = rec.repair()
        assert r["fts_ok"] and r["orphan_fts"] == 1 and r["docs_without_fts"] == 1 and r["masked_or_gone"] == 1
        assert rec.stats()["docs"] == 58
        b = rec.rebuild()
        assert b["docs"] == 59 and b["build_s"] >= 0 and rec.stats()["build_s"] is not None
        st.indexer.max_docs = 1000
        st.indexer.cap(st._db)
        assert rec.stats()["docs"] == 59                         # under the cap: nothing removed
        st.indexer.max_docs = 40
        assert st.indexer.cap(st._db) == 59 - 38
        oldest = st.rows("SELECT MIN(ts) t FROM recall_docs")[0]["t"]
        assert oldest > ts(30)                                   # the oldest rows went first
    finally:
        st.close()


def test_index_version_change_rebuilds_from_history(tmp_path):
    st = Store(tmp_path / "store.db")
    st.indexer = recall.Indexer()
    voice(st, "a", ts(1), ("user", "על הגינה"))
    st.commit()
    with st._lock:
        st._db.execute("UPDATE recall_meta SET value = 'old' WHERE key = 'version'")
        st._db.commit()
    st.close()
    st = Store(tmp_path / "store.db")                            # opening with a newer INDEX_VERSION empties it
    try:
        assert st.rows("SELECT COUNT(*) n FROM recall_docs")[0]["n"] == 0
        rec = recall.Recall(tmp_path, store=st, enabled=lambda: True)
        assert rec.search("גינה", now=NOW)["results"]           # the catch-up re-indexed the history
    finally:
        st.close()


def test_feature_off_indexes_nothing_until_switched_on(tmp_path):
    on = {"v": False}
    st = Store(tmp_path / "store.db")
    st.indexer = recall.Indexer(enabled=lambda: on["v"])
    try:
        voice(st, "a", ts(1), ("user", "על הגינה"))
        st.commit()
        assert st.rows("SELECT COUNT(*) n FROM recall_docs")[0]["n"] == 0
        on["v"] = True
        st.commit()
        assert st.rows("SELECT COUNT(*) n FROM recall_docs")[0]["n"] == 1
    finally:
        st.close()
