import asyncio

from jarves import pronounce
from jarves import workspace as wsp
from jarves.voice.tts import OpenAISpeechTTS

TZACHI = "צַ֫חִי"


def d(**kw):
    return pronounce.Dictionary({"צחי": TZACHI, "אביטל": "אֲבִיטַל", "Jarvis": "ג'ארוויס", **kw})


def test_prefix_handling():
    dic = d()
    assert dic.apply("צחי") == TZACHI
    assert dic.apply("לצחי") == "לְ" + TZACHI
    assert dic.apply("ולצחי") == "וּלְ" + TZACHI          # u-le-Tzachi
    assert dic.apply("מצחי") == "מִ" + TZACHI
    assert dic.apply("בצחי") == "בְּ" + TZACHI
    assert dic.apply("שלצחי") == "שֶׁלְ" + TZACHI
    assert dic.apply("לאביטל") == "לַאֲבִיטַל"             # hataf stem: la-avital
    assert dic.apply("שלום לצחי!") == "שלום לְ" + TZACHI + "!"


def test_no_false_matches_and_latin():
    dic = d()
    assert dic.apply("שלום עולם") == "שלום עולם"
    assert dic.apply("רצחי") == "רצחי"                      # ר is not a prefix
    assert dic.apply("say jarvis now") == "say ג'ארוויס now"
    assert dic.apply("צחי") == dic.apply(dic.apply("צחי"))    # idempotent on already vocalized text


def test_phonetic_spoken_form_keeps_plain_prefix():
    assert pronounce.Dictionary({"צחי": "טסחי"}).apply("ולצחי") == "ולטסחי"


def test_parse_file():
    got = pronounce.parse("# h\n<!-- c = d -->\n- צחי = צַחִי\nJarvis -> ג'ארוויס\nnoise line\n")
    assert got == {"צחי": "צַחִי", "Jarvis": "ג'ארוויס"}


def test_workspace_default_and_edit_changes_output(tmp_path):
    w = wsp.Workspace(tmp_path / "workspace")
    assert "PRONUNCIATIONS.md" in wsp.SPECS
    pronounce.set_path(w.root / "PRONUNCIATIONS.md")
    try:
        assert pronounce.load().entries == {}                       # shipped template has no entries
        assert pronounce.apply("שלום לצחי") == "שלום לצחי"
        w.write("PRONUNCIATIONS.md", "- צחי = " + TZACHI + "\n", by="user")
        assert pronounce.apply("שלום לצחי") == "שלום לְ" + TZACHI
        w.write("PRONUNCIATIONS.md", "- צחי = טסחי\n", by="user")
        assert pronounce.apply("שלום לצחי") == "שלום לטסחי"
    finally:
        pronounce.set_path(None)


def test_persona_note_in_live_front_section(tmp_path):
    w = wsp.Workspace(tmp_path / "workspace")
    front = w.section("front")
    assert TZACHI not in front                                       # the shipped template adds no entries
    w.write("PRONUNCIATIONS.md", "- צחי = " + TZACHI + "\n", by="user")
    assert TZACHI in w.section("front")
    assert pronounce.Dictionary({}).note() == ""
    assert "צחי" in d().note()


def test_openai_tts_text_goes_through_dictionary(tmp_path, monkeypatch):
    f = tmp_path / "PRONUNCIATIONS.md"
    f.write_text("- צחי = צַ֫חִי\n", encoding="utf-8")
    pronounce.set_path(f)
    sent = {}

    class R:
        status_code, content = 200, b"\0\0" * 10

    class C:
        def __init__(self, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, url, json=None, headers=None):
            sent.update(json)
            return R()
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", C)
    try:
        asyncio.run(OpenAISpeechTTS().synth("שלום ולצחי"))
        assert sent["input"] == "שלום וּלְ" + TZACHI
        f.write_text("- צחי = טסחי\n", encoding="utf-8")
        asyncio.run(OpenAISpeechTTS().synth("שלום ולצחי"))
        assert sent["input"] == "שלום ולטסחי"
    finally:
        pronounce.set_path(None)
