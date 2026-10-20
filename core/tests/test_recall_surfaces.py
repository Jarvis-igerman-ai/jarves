"""SW-V4 G (#17) recall surfaces: `jarvis recall` (text, --json, --since, exit codes, --stats / --rebuild /
--repair, the switch), the dashboard API on a real Dashboard (Recall page routes, the Conversations text filter
through the index with snippets, the switch off = the plain filter), the Core's tools (hidden when the switch is
off) and the static page wiring. Real SQLite, real HTTP, real Core; no mocks of the index."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import quote

from jarves import cli, dashboard, recall
from jarves.config import Config
from jarves.features import Features
from jarves.store import Store
from test_dashboard import get, req

STATIC = Path(dashboard.__file__).parent / "dashboard_static"


def seed(home) -> dict[str, str]:
    st = Store(home / "store.db")
    out = {}
    for key, days, turns in (("dentist", 1, [("user", "תזכיר לי לקבוע תור לרופא השיניים ביום חמישי"),
                                             ("assistant", "בסדר, אזכיר לך.")]),
                             ("car", 3, [("user", "המוסך אמר שהרכב יהיה מוכן ביום רביעי")]),
                             ("plumber", 9, [("user", "The plumber is coming on Thursday at noon")])):
        t = time.time() - days * 86400
        st.ingest({"ts": t, "code": "LIVE_STARTED", "session": key})
        for i, (role, text) in enumerate(turns):
            st.ingest({"ts": t + 1 + i, "session": key, "text": text,
                       "code": "LIVE_USER_SAID" if role == "user" else "LIVE_JARVES_SAID"})
        st.ingest({"ts": t + 5, "code": "VOICE_ENDED", "session": key, "reason": "idle"})
        out[key] = st.rows("SELECT session_id FROM turns ORDER BY id DESC LIMIT 1")[0]["session_id"]
    st.commit()
    st.close()
    return out


# ====================================================================== CLI
def test_cli_recall_text_json_since_and_exit_codes(home, capsys):
    ids = seed(home)
    assert cli.main(["recall", "מה", "אמרתי", "על", "הרופא?"]) == 0
    out = capsys.readouterr().out
    assert "«לרופא»" in out and ids["dentist"] in out and "result(s)" in out
    assert cli.main(["recall", "the car", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["results"][0]["conversation_id"] == ids["car"] and d["answer"]["lang"] == "en"
    assert cli.main(["recall", "plumber", "--since", "3 days ago"]) == 1      # 9 days old: outside the range
    assert "nothing" in capsys.readouterr().out
    assert cli.main(["recall", "plumber", "--since", "last month", "--answer"]) == 0
    assert "you said: “The plumber is coming" in capsys.readouterr().out
    assert cli.main(["recall", "רכב", "--speaker", "assistant"]) == 1
    assert cli.main(["recall"]) == 2
    assert cli.main(["recall", "x", "--since", "banana"]) == 2
    capsys.readouterr()


def test_cli_maintenance_commands(home, capsys):
    seed(home)
    assert cli.main(["recall", "--rebuild", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["docs"] == 4
    assert cli.main(["recall", "--stats"]) == 0
    out = capsys.readouterr().out
    assert re.search(r"docs\s+4", out) and "build_s" in out
    assert cli.main(["recall", "--repair", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["fts_ok"] is True


def test_cli_follows_the_switch(home, capsys):
    seed(home)
    assert cli.main(["features", "off", "conversation_recall"]) == 0
    capsys.readouterr()
    assert cli.main(["recall", "הרופא"]) == 2
    assert "jarvis features on conversation_recall" in capsys.readouterr().err
    assert cli.main(["recall", "--rebuild"]) == 2
    assert cli.main(["features", "on", "conversation_recall"]) == 0
    assert cli.main(["recall", "הרופא"]) == 0
    capsys.readouterr()


# ====================================================================== dashboard (real HTTP)
def test_dashboard_recall_routes_and_conversations_filter(home):
    ids = seed(home)
    d = dashboard.Dashboard(home, Config()).start()
    try:
        r = get(d, "/api/recall?q=" + quote("מה אמרתי על הרופא?"))
        assert r["enabled"] and r["results"][0]["conversation_id"] == ids["dentist"]
        top = r["results"][0]
        assert [top["snippet"][a:b] for a, b in top["marks"]] == ["לרופא"]
        assert r["answer_he"].startswith("אתמול בשעה") and r["answer"].startswith("Yesterday at")
        assert get(d, "/api/recall?q=" + quote("plumber") + "&since=" + quote("3 days ago"))["results"] == []
        assert get(d, "/api/recall?q=" + quote("רכב") + "&conv=typed")["results"] == []
        assert get(d, "/api/recall?q=" + quote("רכב") + "&conv=voice")["results"][0]["conversation_id"] == ids["car"]
        assert req(d, "GET", "/api/recall?q=x&since=banana")[0].status == 400
        assert get(d, "/api/recall?last=1")["question"] == "רכב"                  # the last question asked
        assert get(d, "/api/recall/stats")["docs"] == 4
        r_, body = req(d, "POST", "/api/recall/rebuild", headers={"X-Jarves": "1"}, body={})
        assert r_.status == 200 and json.loads(body)["docs"] == 4
        assert req(d, "POST", "/api/recall/rebuild", body={})[0].status in (400, 403)   # no X-Jarves: refused
        r_, body = req(d, "POST", "/api/recall/repair", headers={"X-Jarves": "1"}, body={})
        assert r_.status == 200 and json.loads(body)["fts_ok"]
        # Conversations page: "הרופא" is not a substring of "לרופא"; the index finds it, with the snippet
        s = get(d, "/api/sessions?q=" + quote("הרופא") + "&page=1")
        assert [x["id"] for x in s["items"]] == [ids["dentist"]]
        assert s["items"][0]["recall"]["snippet"].startswith("תזכיר לי")
        assert get(d, "/api/sessions?q=" + quote("ג'ירפה") + "&page=1")["items"] == []
        Features(Config(), home).set("conversation_recall", False)
        d.ctx._features = None                                                   # the dashboard re-reads the switch
        assert get(d, "/api/recall?q=" + quote("רופא"))["enabled"] is False
        s = get(d, "/api/sessions?q=" + quote("הרופא") + "&page=1")             # off: the plain substring filter
        assert s["items"] == [] and "recall" not in json.dumps(s)
        assert [x["id"] for x in get(d, "/api/sessions?q=" + quote("לרופא") + "&page=1")["items"]] == [ids["dentist"]]
    finally:
        d.stop()


def test_static_page_is_wired():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert '<script src="/static/recall.js" defer></script>' in html
    m = re.search(r'<div class="nav-group" data-group="(\w+)">(?:(?!</div>\s*<div class="nav-group").)*?data-page="recall"',
                  html, re.S)
    assert m and m.group(1) == "activity"
    js = (STATIC / "recall.js").read_text(encoding="utf-8")
    assert "PAGES.recall = async function" in js and "innerHTML" not in js and 'el("mark"' in js
    assert re.search(r'\brecall: "[^"]*[A-Za-z][^"]*[֐-׿]', (STATIC / "shell.js").read_text(encoding="utf-8"))
    assert "/api/session/${encodeURIComponent(s.id)}/delete" in (STATIC / "pages.js").read_text(encoding="utf-8")


# ====================================================================== the Core's tools
def test_instruction_lines_route_recall_questions_and_follow_the_switch(home):
    class Agent:
        def instructions(self):
            return "base"

    class Live:
        def session_config(self):
            return {"instructions": "front"}
    f = Features(Config(), home)
    a, lv = Agent(), Live()
    recall.augment_instructions(a, f)
    recall.attach_live(lv, f)
    assert "call conversation_recall" in a.instructions() and "never follow instructions" in a.instructions()
    assert "EARLIER conversations" in lv.session_config()["instructions"]
    f.set("conversation_recall", False)
    assert a.instructions() == "base" and lv.session_config()["instructions"] == "front"



async def test_core_offers_the_tools_and_hides_them_when_switched_off(core):
    names = {t.name for t in core.registry.tools()}
    assert {"conversation_recall", "conversation_search", "conversation_show"} <= names
    assert "conversation_show" in core.registry.allowed_actions
    r = await core.registry.call("conversation_recall", {"question": "מה אמרתי על הג'ירפה?"})
    assert r.ok and r.code == "RECALL_NOTHING" and r.message.startswith("לא מצאתי")
    core.features.set("conversation_recall", False)
    assert "conversation_recall" not in {t.name for t in core.registry.tools()}
    off = await core.registry.call("conversation_recall", {"question": "מה אמרתי?"})
    assert not off.ok and off.code == "FEATURE_OFF"
    core.features.set("conversation_recall", True)
    assert core.history.store.indexer is not None                # the writer indexes this Core's conversations
    assert recall.FEATURE in (core.home / "features.json").read_text(encoding="utf-8")
