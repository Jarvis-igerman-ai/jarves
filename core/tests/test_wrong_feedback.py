"""JAR-230: 'Jarvis, that was wrong' saves the last exchange (redacted) as a local feedback item; voice, dashboard and
CLI share one save(); week() feeds the weekly report. Real sqlite store in tmp; no network, clock injected."""
from __future__ import annotations

import asyncio
import json
import sqlite3

from jarves import cli, features as feat, store, wrong_feedback as wf
from jarves.config import Config

NOW = 1_800_000_000.0


def seed(home, *turns):
    store.Store(home / "store.db").close()          # the real schema
    con = sqlite3.connect(home / "store.db")
    for i, (role, text) in enumerate(turns):
        con.execute("INSERT INTO turns (session_id, ts, role, text, chars) VALUES ('s1', ?, ?, ?, 0)", (NOW + i, role, text))
    con.commit()
    con.close()


def test_saves_the_last_exchange_redacted_and_feeds_the_week(home):
    seed(home, ("user", "old question"), ("assistant", "old answer"),
         ("user", "call me at +972 54 123 4567 or a@b.com"), ("assistant", "the capital of Israel is Haifa"),
         ("user", "Jarvis, that was wrong"))                                  # the feedback phrase itself comes last
    it = wf.save(home, note="it is Jerusalem", source="voice", now=NOW)
    assert it["jarves"] == "the capital of Israel is Haifa" and it["source"] == "voice"
    assert "4567" not in it["user"] and "a@b.com" not in it["user"] and "<phone>" in it["user"]
    assert json.loads((home / wf.FILE).read_text(encoding="utf-8").splitlines()[0])["note"] == "it is Jerusalem"
    assert wf.week(home, NOW + 3600)["count"] == 1 and wf.week(home, NOW + 8 * 86400)["count"] == 0


def test_nothing_to_save_without_a_reply(home):
    assert wf.save(home, now=NOW) is None and not (home / wf.FILE).exists()


def test_feature_is_beta_and_off_by_default():
    f = feat.CATALOG[wf.FEATURE]
    assert f.default_enabled is False and feat.stage_of(wf.FEATURE) == "beta" and wf.FEATURE in f.skills


def test_voice_tool_and_dashboard_route_use_the_same_save(home):
    seed(home, ("user", "q"), ("assistant", "a"))
    tool = wf.make_skill(home).tools()[0]
    assert tool.name == "report_wrong"
    assert asyncio.run(tool.handler(None)).ok
    routes = {}
    wf.dashboard_routes(lambda m, p, h: routes.__setitem__((m, p), h))
    ctx = type("C", (), {"home": home, "cfg": Config()})()
    feat.Features(Config(), home).set(wf.FEATURE, True)
    assert routes[("POST", r"/api/feedback/wrong")](ctx, None, {})[0] == 200
    assert routes[("GET", r"/api/feedback")](ctx, None, None)[1]["count"] == 2


def test_cli_wrong_is_refused_while_off_then_saves(home, capsys):
    seed(home, ("user", "q"), ("assistant", "a"))
    assert cli.main(["feedback", "wrong"]) == 2
    feat.Features(Config(), home).set(wf.FEATURE, True)
    assert cli.main(["feedback", "wrong", "--note", "bad"]) == 0
    assert cli.main(["feedback", "list", "--json"]) == 0
    assert '"count": 1' in capsys.readouterr().out
