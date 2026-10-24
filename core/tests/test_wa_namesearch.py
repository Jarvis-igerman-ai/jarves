"""Issue #17: the forgiving, ranked chat-NAME search (wa_namesearch): typos, Hebrew spelling variants, keyboard layout,
Latin<->Hebrew spelling, the pattern mini-language, the safe regex mode, ranking with reasons, never auto-picking among
several, send paths exact, speed. Offline."""
from __future__ import annotations

import json
import time

import pytest

from jarves import wa_namesearch as ns
from jarves import whatsapp_bridge as wab
from jarves.whatsapp_bridge import WhatsAppBridgeSkill
from whatsapp_fixtures import bridge, clock, home  # noqa: F401

LIST = "רשימת קניות"
LIST_ID = "972501230001@s.whatsapp.net"
OTHER_ID = "972501230002@s.whatsapp.net"


def chats(*names):
    return [{"id": f"97250000000{i}@s.whatsapp.net", "name": n, "number": f"97250000000{i}", "last_ts": 100 - i}
            for i, n in enumerate(names)]


def names(hits):
    return [h.chat["name"] for h in hits]


CS = chats(LIST, "קניות לשבת", "דנה כהן", "Bob Smith", "משפחה", "Dana Levi", "אשתי", "עבודה - צוות")


@pytest.mark.parametrize("q", ["קניות", "רשימה קניות", "רשימת", "קניות*", "רשמית קניות", "רשימת קניות", "לקניות"])
def test_example_chat_is_found(q):
    assert LIST in names(ns.search(CS, q)), q


def test_example_chat_is_ranked_above_weaker_hits():
    cs = chats("קניות לשבת", "Shopping קניות ביתיות", LIST, "רשימת מכולת")
    hits = ns.search(cs, "רשימת קניות")
    assert hits[0].chat["name"] == LIST and hits[0].reason == "exact"
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)
    hits = ns.search(chats("שבת קניות", LIST), "רשמית קניות")                      # typo + variant
    assert hits[0].chat["name"] == LIST and hits[0].reason == "word match"


@pytest.mark.parametrize("q,reason", [("קניות", "word match"), ("רשימה קניות", "word match"), ("שימת", "contains"),
                                      ("רשמית קניות", "similar (typo)"), ("רשימת קניות", "exact"),
                                      ("רשימת", "starts with"), ("רשי", "starts with")])
def test_reasons_are_visible(q, reason):
    cs = chats(LIST, "Bob")
    h = ns.search(cs, q)
    assert h and h[0].chat["name"] == LIST, q
    assert (reason == "word match" and h[0].reason in ("word match", "starts with")) or h[0].reason == reason or \
        (q == "רשמית קניות" and h[0].reason in ("word match", "similar (typo)")), (q, h[0].reason)


def test_typo_tolerance_by_word_length():
    cs = chats("Alexander Hamilton", "Jonathan")
    assert names(ns.search(cs, "Alexandar")) == ["Alexander Hamilton"]            # 1 edit (>=4 letters)
    assert names(ns.search(cs, "Alexandr Hamiltn")) == ["Alexander Hamilton"]    # 8+ letters: 2 edits
    assert names(ns.search(cs, "Jonathn"))[:1] == ["Jonathan"]
    assert ns.search(chats("Bob"), "Rob") == []                                   # under 4 letters: exact only
    assert ns.search(chats("Hamilton"), "Hamxxxon") == []                         # too far
    assert names(ns.search(chats("Hamilton"), "Hamliton")) == ["Hamilton"]         # a swap is one edit


@pytest.mark.parametrize("a,b", [("רשימת", "רשימה"), ("קניות", "קניה"), ("שלום", "שלם"), ("מִשְׁפָּחָה", "משפחה"),
                                 ("צ'אט", "צאט"), ("ספרים", "ספרי"), ("חברים", "חברים")])
def test_hebrew_spelling_variants(a, b):
    assert names(ns.search(chats(a), b)) == [a] or names(ns.search(chats(b), a)) == [b]


def test_final_letters_and_niqqud_and_geresh():
    assert len(ns.search(chats("שלום עולם"), "עולמ")) == 1                       # final mem
    assert len(ns.search(chats("ג'ורג' בוש"), "גורג")) == 1                        # geresh
    assert len(ns.search(chats("לֵב אִשְׁתִּי"), "אשתי")) == 1                       # niqqud


def test_transliteration_latin_to_hebrew_and_back():
    cs = chats("דנה כהן", "יוסי", "Bob")
    h = ns.search(cs, "dana")
    assert names(h) == ["דנה כהן"] and h[0].reason == "similar (spelling)"
    assert names(ns.search(chats("משה לוי", "Bob"), "moshe")) == ["משה לוי"]
    assert names(ns.search(chats("Dana Levi", "Bob"), "דנה")) == ["Dana Levi"]
    assert ns.search(chats("קניות"), "shopping") == []                              # a translation is not a spelling


def test_wrong_keyboard_layout_both_ways():
    cs = chats("שלום חברים", "Dana Levi", "Bob")
    h = ns.search(cs, "akuo")                                                      # שלום typed on an English keyboard
    assert names(h) == ["שלום חברים"] and h[0].reason == "similar (keyboard layout)"
    h = ns.search(cs, "גשמש")                                                      # "dana" typed on a Hebrew keyboard
    assert names(h) == ["Dana Levi"] and h[0].reason == "similar (keyboard layout)"
    assert names(ns.search(chats("משפחה"), "napjv"))[:1] == ["משפחה"]


def test_wildcards_anchors_alternation_phrase():
    cs = chats(LIST, "קניות לשבת", "דנה כהן", "Bob Smith", "משפחה")
    assert set(names(ns.search(cs, "קניות*"))) == {LIST, "קניות לשבת"}
    assert names(ns.search(cs, "^קניות")) == ["קניות לשבת"]                         # starts with
    assert names(ns.search(cs, "קניות$")) == [LIST]                                 # ends with
    assert names(ns.search(cs, "^רשימת קניות$")) == [LIST]                          # whole
    assert set(names(ns.search(cs, "קני?ת"))) == {LIST, "קניות לשבת"}               # one letter
    assert ns.search(cs, "קנ?ת") == []                                              # ? is exactly one letter
    assert set(names(ns.search(cs, "משפחה|bob"))) == {"משפחה", "Bob Smith"}         # either
    assert names(ns.search(cs, '"קניות לשבת"')) == ["קניות לשבת"]                   # exact words
    assert ns.search(cs, '"קני"') == []                                             # whole words only
    assert names(ns.search(cs, "רשימת*קניות")) == [LIST]
    assert all(h.reason == "pattern" for h in ns.search(cs, "קניות*"))
    assert names(ns.search(cs, "קניות שבת")) == ["קניות לשבת"]                      # several words = all of them


def test_a_stray_question_mark_or_quote_is_not_a_pattern():
    assert not ns.looks_like_pattern("דנה?") and not ns.looks_like_pattern('צה"ל')
    assert ns.looks_like_pattern("ק?ניות") and ns.looks_like_pattern("a*") and ns.looks_like_pattern('"a b"')


def test_regex_mode():
    cs = chats(LIST, "קניות לשבת", "Bob Smith", "Bob Brown", "משפחה 2024")
    assert names(ns.search(cs, "/^bob (smith|jones)$/")) == ["Bob Smith"]
    assert set(names(ns.search(cs, "bob .*n", regex=True))) == {"Bob Brown"}
    assert names(ns.search(cs, r"/\d{4}$/")) == ["משפחה 2024"]
    assert set(names(ns.search(cs, "/[bB]ob/"))) == {"Bob Smith", "Bob Brown"}
    assert set(names(ns.search(cs, "/ק.יות/"))) == {LIST, "קניות לשבת"}
    assert names(ns.search(cs, "/קניות?/"))                                          # optional letter


@pytest.mark.parametrize("bad", ["/[/", "/(/", "/)/", "/a{99}/", "/a**/", "/(?=a)/", r"/(a)\1/", "/*a/", "/a{2,1}/"])
def test_invalid_regex_has_clear_en_and_he_messages(bad):
    with pytest.raises(ns.QueryError) as e:
        ns.search(CS, bad)
    assert "Invalid pattern" in e.value.en or "complex" in e.value.en
    assert any("א" <= ch <= "ת" for ch in e.value.he)


def test_over_long_pattern_is_refused_quickly():
    t = time.perf_counter()
    for q in ("/" + "a" * 500 + "/", "x" * 500):
        with pytest.raises(ns.QueryError) as e:
            ns.search(CS, q)
        assert "too long" in e.value.en and "ארוך" in e.value.he
    with pytest.raises(ns.QueryError):
        ns.search(CS, "/((((a{30}){30}){30}){30})/")                               # expands past the size limit
    assert time.perf_counter() - t < 0.5


def test_pathological_regexes_are_linear_time():
    long_name = "a" * 80
    cs = chats(long_name + "!", "b" * 80, long_name)
    t = time.perf_counter()
    for pat in ("/(a*)*b/", "/(a+)+$/", "/(a|aa)+c/", "/(a|a)*c/", "/(.*)*x/", "/(a?){25}a{25}/", "/(a*a*a*a*a*)+b/"):
        ns.search(cs, pat)
    assert time.perf_counter() - t < 2.0


def test_no_auto_pick_among_several_but_one_clear_best_is_used():
    cs = chats("דנה כהן", "דנה לוי", "Bob")
    assert len(wab.resolve(cs, "דנה")) == 2                                         # ambiguous: the caller asks
    assert len(wab.resolve(cs, "כהן")) == 1                                         # one clear match is used
    assert len(wab.resolve(cs, "דננה")) == 2                                        # typo: still both, none picked
    assert len(wab.resolve(chats("רשימת קניות", "קניות לשבת"), "קניות")) == 1       # best rank wins the group


def test_send_and_allow_paths_stay_exact():
    cs = chats(LIST, "דנה כהן")
    for q in ("קניות", "רשמית קניות", "dana", "קניות*", "/.*/", "דנא"):
        assert wab.resolve(cs, q, fuzzy=False) == [], q
    assert wab.resolve(cs, "דנה", fuzzy=False)[0]["name"] == "דנה כהן"             # the old first-name rule


def test_resolve_accepts_patterns_only_when_asked():
    cs = chats(LIST, "Bob")
    assert wab.resolve(cs, "ק??ות") == []                                          # plain text: ? is punctuation
    assert [c["name"] for c in wab.resolve(cs, "ק??ות", patterns=True)] == [LIST]


def test_performance_200_chats():
    pool = ["דנה", "יוסי", "Bob", "Alice", "קניות", "משפחה", "עבודה", "Smith", "כהן", "לוי", "Cooper", "רשימת"]
    cs = chats(*[f"{pool[i % 12]} {pool[(i * 7) % 12]} {i}" for i in range(200)])
    t = time.perf_counter()
    for q in ("קניות", "רשמית קניות", "dana", "akuo", "קני*|bob", "/^דנה .* \\d+$/", "zzzzqqqq", "Alexandar Hamiltn"):
        assert len(ns.search(cs, q)) <= 10
    assert time.perf_counter() - t < 1.5


# ------------------------------------------------------------------------- the tool, the CLI, the instructions
@pytest.fixture
def skill(home, bridge):
    taint = []
    s = WhatsAppBridgeSkill(home, on_untrusted=lambda: taint.append(1))
    s.taint = taint
    return s


async def call(skill, name, **args):
    t = next(t for t in skill.tools() if t.name == name)
    return await t.handler(t.args(**args))


async def test_find_chat_tool_ranks_with_reasons_and_flags_the_clear_best(skill, bridge):
    bridge.add_chat(LIST_ID, LIST)
    bridge.add_chat(OTHER_ID, "קניות לשבת")
    bridge.deliver(LIST_ID, "a", ts=1_790_000_000.0 - 50)
    bridge.deliver(OTHER_ID, "b", ts=1_790_000_000.0 - 40)
    r = await call(skill, "whatsapp_find_chat", text="רשמית קניות")
    assert r.ok and skill.taint
    rows = r.data["untrusted_chats"]
    assert rows[0]["name"] == LIST and rows[0]["reason"] in ("word match", "similar (typo)")
    assert r.data["clear_best_match"] is True
    r = await call(skill, "whatsapp_find_chat", text="קניות")
    assert [x["reason"] for x in r.data["untrusted_chats"]][:1] == ["starts with"]
    assert "reason" in json.dumps(r.data["rule"])
    r = await call(skill, "whatsapp_find_chat", text="קני*")
    assert r.data["clear_best_match"] is False and len(r.data["untrusted_chats"]) == 2


async def test_find_chat_tool_regex_and_errors(skill, bridge):
    bridge.add_chat(LIST_ID, LIST)
    bridge.deliver(LIST_ID, "a", ts=1_790_000_000.0 - 50)
    r = await call(skill, "whatsapp_find_chat", text="^רשימת", regex=True)
    assert r.ok and r.data["untrusted_chats"][0]["name"] == LIST and r.data["untrusted_chats"][0]["reason"] == "pattern"
    r = await call(skill, "whatsapp_find_chat", text="/[/")
    assert not r.ok and r.code == "WA_BAD_PATTERN" and "Invalid pattern" in r.data["error"] and "תבנית" in r.message
    r = await call(skill, "whatsapp_chat", who="/(/")
    assert not r.ok and r.code == "WA_BAD_PATTERN"


async def test_chat_tools_use_the_fallback_but_never_pick_among_several(skill, bridge):
    bridge.add_chat(LIST_ID, LIST)
    bridge.deliver(LIST_ID, "חלב ולחם", ts=1_790_000_000.0 - 50)
    r = await call(skill, "whatsapp_chat", who="רשמית קניות")
    assert r.ok and r.data["untrusted_chat_name"] == LIST
    bridge.add_chat(OTHER_ID, "רשימת קניות לחג")
    bridge.deliver(OTHER_ID, "x", ts=1_790_000_000.0 - 40)
    r = await call(skill, "whatsapp_chat", who="רשמית קניות")
    assert not r.ok and r.code == "WA_AMBIGUOUS" and len(r.data["untrusted_candidates"]) == 2
    r = await call(skill, "whatsapp_compose", who="רשמית קניות", text="היי")
    assert not r.ok and r.code != "WA_DRAFT_READY"


def test_instructions_tell_the_assistant_to_try_similar_names():
    assert "similar spelling" in wab.BACKEND_RULE and "ranked" in wab.BACKEND_RULE
    assert "instead of saying you cannot find the chat" in wab.BACKEND_RULE
