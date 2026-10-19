"""SW-V2 WP-H dashboard over real HTTP: workspace edit/diff/history/undo, memory edit/delete/forget/undo, safe
config.toml editing, logs (filters, conversation links, privacy, export, paging), conversation search and raw view,
background tasks, and opening the dashboard from inside the Core (tool + face message)."""
import json
import time
import tomllib
from urllib.parse import quote

import pytest

from jarves import dashboard, diagnose
from jarves.config import Config
from jarves.memory import MemoryStore
from jarves.store import Store

from test_dashboard import FIXTURE, get, req

W = {"X-Jarves": "1"}


def log_line(ts, code, level="INFO", component="live", session="sess0001", **f):
    from datetime import datetime, timezone
    return json.dumps({"ts": datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="milliseconds"), "level": level,
                       "component": component, "code": code, "msg": f.pop("msg", code.lower()), "cid": "c1",
                       "session": session, **f}, ensure_ascii=False)


@pytest.fixture
def dash(home):
    st = Store(home / "store.db", tier="priority", backend_model="gpt-6-luna")
    diagnose.run(st, Config(), st.ingest_log(FIXTURE))
    st.close()
    (home / "logs").mkdir()
    lines = [json.loads(l) for l in FIXTURE.read_text(encoding="utf-8").splitlines() if l.strip()]
    (home / "logs" / "jarves.log.1").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines[:40]) + "\n",
                                                encoding="utf-8")
    t = time.time()
    (home / "logs" / "jarves.log").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines[40:]) + "\n" + "\n".join([
        log_line(t - 30, "WAKE_HEALTH", component="wake"),
        log_line(t - 20, "AI_REQUEST", component="ai", msg="agent request started", api_key="sk-abcdefghijklmnopqrstuv"),
        log_line(t - 10, "TOOL_RESULT", level="WARNING", component="skills", tool="music_play", msg="mail joe@example.com"),
    ]) + "\n", encoding="utf-8")
    (home / "tasks.jsonl").write_text(json.dumps({"id": "t1", "description": "השוואת טלפונים", "status": "done",
                                                  "created": t - 100, "finished": t - 80, "duration_s": 20.0,
                                                  "result": "אייפון", "attempts": 1, "queries": ["iphone vs galaxy"],
                                                  "usage": {"input_tokens": 1000}}, ensure_ascii=False) + "\n",
                                      encoding="utf-8")
    d = dashboard.Dashboard(home, Config()).start()
    yield d
    d.stop()


def send(d, method, path, body=None):
    r, data = req(d, method, path, headers=W, body=body if body is not None else {})
    return r.status, json.loads(data or b"{}")


def test_workspace_edit_preview_history_undo_restore(dash, home):
    st, r = send(dash, "POST", "/api/workspace/USER.md/preview", {"text": "# על המשתמש\n- קוראים לי דנה\n"})
    assert st == 200 and "+- קוראים לי דנה" in r["diff"]
    assert "קוראים לי דנה" not in (home / "workspace" / "USER.md").read_text(encoding="utf-8")   # preview only
    st, r = send(dash, "PUT", "/api/workspace/USER.md", {"text": "# על המשתמש\n- קוראים לי דנה\n"})
    assert st == 200 and r["changed"] and r["version"]
    f = get(dash, "/api/workspace/USER.md")
    assert "דנה" in f["text"] and len(f["history"]) == 1 and f["writable"]
    v = get(dash, f"/api/workspace/USER.md/version/{f['history'][0]['version']}")
    assert "-- קוראים לי דנה" in v["diff"]
    st, _ = send(dash, "POST", "/api/workspace/USER.md/undo")
    assert st == 200 and "דנה" not in get(dash, "/api/workspace/USER.md")["text"]
    hist = get(dash, "/api/workspace/USER.md")["history"]
    with_name = next(h["version"] for h in hist if "דנה" in get(dash, f"/api/workspace/USER.md/version/{h['version']}")["text"])
    st, _ = send(dash, "POST", "/api/workspace/USER.md/restore", {"version": with_name})
    assert st == 200 and "דנה" in get(dash, "/api/workspace/USER.md")["text"]
    st, r = send(dash, "PUT", "/api/workspace/USER.md", {"text": "password: hunter2"})
    assert st == 400 and "secret" in r["error"]                       # WP-D validation reaches the UI
    assert send(dash, "PUT", "/api/workspace/..%2Fsecrets.env", {"text": "x"})[0] == 400


def test_memory_edit_delete_forget_and_undo(dash, home):
    mem = MemoryStore(home / "memory.db")
    a = mem.add("הצבע האהוב: כחול")
    b = mem.add("לכלב קוראים רקס", source="auto", session="s-1", confidence=0.85)
    c = mem.add("המשתמש גר בחיפה", source="auto", session="s-1", confidence=0.8)
    mem.close()
    m = get(dash, "/api/memory")
    assert m["editable"] and m["auto_sessions"] == [{"session": "s-1", "facts": 2, "first": m["auto_sessions"][0]["first"]}]
    assert send(dash, "PUT", f"/api/memory/{a}", {"text": "הצבע האהוב: ירוק"})[0] == 200
    assert send(dash, "PUT", f"/api/memory/{a}", {"text": ""})[0] == 400
    assert send(dash, "DELETE", f"/api/memory/{b}")[0] == 200
    st, r = send(dash, "POST", "/api/memory/forget-session", {"session": "s-1"})
    assert st == 200 and r["deleted"] == 1
    texts = lambda: {i["text"] for i in get(dash, "/api/memory")["items"]}   # noqa: E731
    assert texts() == {"הצבע האהוב: ירוק"}
    assert get(dash, "/api/memory")["undo"] == "deleting 1 fact(s)"
    for _ in range(3):
        assert send(dash, "POST", "/api/memory/undo")[0] == 200
    assert texts() == {"הצבע האהוב: כחול", "לכלב קוראים רקס", "המשתמש גר בחיפה"}
    assert {i["id"] for i in get(dash, "/api/memory")["items"]} == {a, b, c}     # same ids come back
    assert send(dash, "POST", "/api/memory/undo")[0] == 409
    st, r = send(dash, "POST", "/api/memory", {"text": "אוהב קפה שחור"})
    assert st == 200 and send(dash, "POST", "/api/memory/undo")[0] == 200 and "אוהב קפה שחור" not in texts()


def test_settings_validated_backed_up_and_undone(dash, home):
    cfg_path = home / "config.toml"
    cfg_path.write_text("# my settings\n[voice]\nidle_disconnect_s = 120  # two minutes\n\n[music]\nmax_volume = 60\n",
                        encoding="utf-8")
    s = get(dash, "/api/settings")
    e = {x["key"]: x for x in s["editable"]}
    assert e["voice.backend_deadline_s"]["default"] == 25 and e["voice.idle_disconnect_s"]["file"] == 120
    assert e["voice.idle_disconnect_s"]["min"] == 30 and e["ai.voice_service_tier"]["type"] == "choice"
    assert "voice.chime" not in e and "history.llm_review" not in e        # those are Features switches
    st, r = send(dash, "PUT", "/api/settings", {"values": {"voice.backend_deadline_s": "18", "voice.idle_disconnect_s": 240,
                                                           "history.llm_review_daily_max": 5, "log.privacy": True}})
    assert st == 200 and r["applies"] == "next start" and r["backup"]
    text = cfg_path.read_text(encoding="utf-8")
    raw = tomllib.loads(text)
    assert raw["voice"] == {"idle_disconnect_s": 240, "backend_deadline_s": 18.0}
    assert raw["history"]["llm_review_daily_max"] == 5 and raw["log"]["privacy"] is True
    assert "# my settings" in text and "# two minutes" in text and raw["music"]["max_volume"] == 60   # comments kept
    before = cfg_path.read_bytes()
    for bad in ({"voice.idle_disconnect_s": 5}, {"voice.idle_disconnect_s": "soon"}, {"ai.api_key": "sk-x"},
                {"link.host": "0.0.0.0"}, {"ai.voice_service_tier": "turbo"}):
        st, r = send(dash, "PUT", "/api/settings", {"values": bad})
        assert st == 400, bad
    assert cfg_path.read_bytes() == before                                 # nothing written on a refused value
    st, r = send(dash, "POST", "/api/settings/undo")
    assert st == 200 and tomllib.loads(cfg_path.read_text(encoding="utf-8"))["voice"] == {"idle_disconnect_s": 120}
    st, r = send(dash, "POST", "/api/settings/runtime", {"key": "voice.voice", "value": "meridian"})
    assert st == 200 and r["value"] == "meridian"
    assert send(dash, "POST", "/api/settings/runtime", {"key": "voice.voice", "value": "robot"})[0] == 400
    assert send(dash, "POST", "/api/settings/runtime", {"key": "ai.api_key", "value": "x"})[0] == 400
    assert "sk-" not in json.dumps(get(dash, "/api/settings"))


def test_logs_filters_links_privacy_and_export(dash, home):
    d = get(dash, "/api/logs")
    assert {f["name"] for f in d["files"]} == {"jarves.log", "jarves.log.1"}
    codes = [l["code"] for l in d["lines"]]
    assert "WAKE_HEALTH" not in codes and codes[0] == "TOOL_RESULT"         # newest first, noisy hidden
    assert "WAKE_HEALTH" in [l["code"] for l in get(dash, "/api/logs?noisy=1")["lines"]]
    body = json.dumps(d, ensure_ascii=False)
    assert "sk-abcdefghij" not in body and "example.com" not in body        # never secrets or e-mail addresses
    assert all(l["level"] in ("WARNING", "ERROR") for l in get(dash, "/api/logs?level=WARNING")["lines"])
    live = get(dash, "/api/logs?code=LIVE_*")["lines"]
    assert live and all(l["code"].startswith("LIVE_") for l in live)
    assert get(dash, "/api/logs?module=wake&noisy=1")["matched"] == 1
    said = get(dash, "/api/logs?q=" + quote("אינטל"))
    assert said["matched"] >= 1 and all("אינטל" in json.dumps(l, ensure_ascii=False) for l in said["lines"])
    sid = get(dash, "/api/sessions")[0]["id"]
    conv = get(dash, f"/api/logs?conv={sid}&limit=5")
    assert conv["matched"] > 20 and all(l["conv"] == sid for l in conv["lines"]) and conv["more"]
    older = get(dash, f"/api/logs?conv={sid}&limit=5&before={conv['cursor']}")
    assert {l["ref"] for l in older["lines"]}.isdisjoint({l["ref"] for l in conv["lines"]})
    assert req(dash, "GET", "/api/logs?conv=nope")[0].status == 404
    r, data = req(dash, "GET", "/export/logs.jsonl?code=LIVE_USER_SAID")
    assert r.status == 200 and "attachment" in r.getheader("Content-Disposition")
    rows = [json.loads(x) for x in data.decode("utf-8").splitlines()]
    assert rows and all(x["code"] == "LIVE_USER_SAID" for x in rows) and rows[0]["ts"] <= rows[-1]["ts"]
    assert req(dash, "GET", "/export/logs.jsonl", cookie=False)[0].status == 403
    facets = get(dash, "/api/logs/facets")
    assert "LIVE_*" in facets["prefixes"] and "wake" in facets["modules"]
    dash.ctx.cfg.log.privacy = True                                         # privacy on now: content masked
    said = get(dash, "/api/logs?code=LIVE_USER_SAID")["lines"]
    assert said and all(str(l["text"]).startswith("<") for l in said)
    dash.ctx.cfg.log.privacy = False


def test_conversation_search_sort_pages_and_raw(dash, home):
    st = Store(home / "store.db", backend_model="gpt-6-luna")
    t = time.time() - 3600
    st.ingest({"ts": t, "code": "REPLY", "msg": "reply via ai", "ms": 900, "text": "בוקר טוב!", "utterance": "שלום",
               "typed": True, "session": "typed01"})
    st.commit()
    st.close()
    every = get(dash, "/api/sessions?page=1")
    assert every["total"] == 2 and every["pages"] == 1 and "music_play" in every["tools"]
    assert get(dash, "/api/sessions?kind=typed")["items"][0]["typed"] is True
    assert get(dash, "/api/sessions?kind=voice")["total"] == 1
    assert get(dash, "/api/sessions?q=" + quote("אינטל"))["total"] == 1
    assert get(dash, "/api/sessions?tool=music_play")["total"] == 1 and get(dash, "/api/sessions?tool=nope")["total"] == 0
    assert get(dash, "/api/sessions?issues=1")["total"] == 1
    assert get(dash, "/api/sessions?min_cost=0.1")["total"] == 1
    assert get(dash, "/api/sessions?min_dur=60")["total"] == 1
    by_cost = get(dash, "/api/sessions?sort=cost&dir=asc")["items"]
    assert by_cost[0]["cost"]["total"] <= by_cost[1]["cost"]["total"]
    p2 = get(dash, "/api/sessions?per=1&page=2")
    assert p2["pages"] == 2 and len(p2["items"]) == 1
    assert req(dash, "GET", "/api/sessions?min_cost=lots")[0].status == 400
    assert isinstance(get(dash, "/api/sessions"), list)                     # old plain list still works
    sid = next(s["id"] for s in every["items"] if not s["typed"])
    raw = get(dash, f"/api/session/{sid}/raw")
    assert raw["session"]["id"] == sid and len(raw["turns"]) == 26 and raw["issue_hits"]
    one = get(dash, f"/api/session/{sid}")
    assert "review" in one and one["review"]["skipped"]                     # no API key in tests: no AI review
    assert req(dash, "POST", f"/api/session/{sid}/review", headers=W, body={})[0].status == 409


def test_tasks_page_and_privacy(dash):
    t = get(dash, "/api/tasks")
    assert t["items"][0]["description"] == "השוואת טלפונים" and t["counts"]["done"] == 1
    dash.ctx.cfg.log.privacy = True
    assert get(dash, "/api/tasks")["items"][0]["description"].startswith("<")
    dash.ctx.cfg.log.privacy = False


def test_static_pages_script_is_served_and_safe(dash):
    r, html = req(dash, "GET", "/")
    assert b"/static/pages.js" in html
    r, js = req(dash, "GET", "/static/pages.js")
    assert r.status == 200 and b"PAGES.logs" in js and b".innerHTML" not in js


async def test_open_dashboard_from_the_core_tool_and_face(core, home):
    from conftest import open_face
    opened = []
    core.open_url = opened.append
    res = await core.registry.call("open_dashboard", {}, origin="model")
    assert res.ok and res.data["url"].startswith("http://127.0.0.1:") and "t=" not in res.data["url"]
    assert len(opened) == 1 and "?t=" in opened[0]                          # the browser gets the one-time key
    assert core._dashboard.ctx.workspace is core.workspace                  # shares the Core's objects
    f = await open_face(core)
    await f.send("ui.open_dashboard")
    p = await f.wait("dashboard.opened")
    assert p["browser"] is True and p["url"] == res.data["url"] and len(opened) == 2
    r, _ = req(core._dashboard, "GET", "/api/overview")                  # really serving, behind its key
    assert r.status == 200 and req(core._dashboard, "GET", "/api/overview", cookie=False)[0].status == 403
