"""V5 Development Manager status layer: fake GitHub endpoint + fake agents (a real agents.db in tmp). No network, no
browser, no sleeping; clocks are injected. Status is read-only; write actions run only after an approval."""
from __future__ import annotations

import asyncio
from pathlib import Path

from jarves import cli, dashboard, devstatus as ds
from jarves.config import Config
from jarves.orchestrator.db import AgentDB
from jarves.skills import SkillRegistry

NOW = 1_800_000_000.0
REPO = "igalgerman/jarves"


def run(name, status, conclusion, run_id):
    return {"name": name, "status": status, "conclusion": conclusion,
            "details_url": f"https://github.com/{REPO}/actions/runs/{run_id}/job/9"}


class FakeGitHub:
    """GET-only fake of the REST API; counts calls; can simulate the rate limit."""

    def __init__(self):
        self.calls, self.limited = [], False
        pulls = [
            {"number": 1, "title": "red ghp_abcdefghijklmnopqrstuvwxyz0123456789 leak", "draft": False, "head": {"ref": "v5/red", "sha": "a" * 40}, "updated_at": "t"},
            {"number": 2, "title": "green one", "draft": False, "head": {"ref": "v5/green", "sha": "b" * 40}, "updated_at": "t"},
            {"number": 3, "title": "running", "draft": False, "head": {"ref": "v5/run", "sha": "c" * 40}, "updated_at": "t"},
            {"number": 4, "title": "many red", "draft": False, "head": {"ref": "v5/many", "sha": "d" * 40}, "updated_at": "t"},
        ]
        issues = [{"number": 7, "title": "open bug"}, {"number": 8, "title": "being fixed"}, {"number": 2, "title": "a pr", "pull_request": {}}]
        checks = {"a" * 40: [run("Test", "completed", "failure", 111), run("Lint", "completed", "success", 111)],
                  "b" * 40: [run("Test", "completed", "success", 112)],
                  "c" * 40: [run("Test", "in_progress", None, 113)],
                  "d" * 40: [run("Test", "completed", "failure", 114), run("Lint", "completed", "failure", 114)]}
        self.routes = {f"/repos/{REPO}/pulls?state=open&per_page=30": pulls,
                       f"/repos/{REPO}/issues?state=open&per_page=30": issues}
        for sha, c in checks.items():
            self.routes[f"/repos/{REPO}/commits/{sha}/check-runs?per_page=50"] = {"check_runs": c}

    def __call__(self, path):
        self.calls.append(path)
        if self.limited:
            return 403, {"message": "rate limit"}, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(NOW + 5000)}
        if path in self.routes:
            return 200, self.routes[path], {"x-ratelimit-remaining": "4000"}
        return 404, None, {}


def fake_agents(home):
    db = AgentDB(Path(home) / "agents.db")
    db.upsert_session("claude:s1", project="jarves", branch="v5/red", name="fix ci", state="RUNNING", last_progress_at=NOW - 7200)
    db.upsert_session("codex:s2", project="jarves", branch="x", name="work on #8", state="RUNNING", last_progress_at=NOW - 60)
    db.upsert_session("claude:s3", project="jarves", branch="v5/run", name="done", state="STOPPED", ended_at=NOW - 100)
    db.close()


def test_status_correlates_classifies_and_suggests(home):
    fake_agents(home)
    gh = FakeGitHub()
    st = ds.build(home, REPO, fetch=gh, now=NOW)
    by = {(i["type"], i["number"]): i for i in st["items"]}
    assert st["read_only"] and all(c.startswith("/repos/") for c in gh.calls)
    red = by["pr", 1]
    assert red["state"] == "ci_failed" and red["blocker"].startswith("failed: Test") and "agent is stuck" in red["blocker"]
    assert red["suggestion"]["kind"] == "unblock" and "ghp_" not in red["title"]
    assert red["proposals"][0]["command"] == f"gh run rerun 111 --failed -R {REPO}"
    assert by["pr", 2]["state"] == "green" and by["pr", 2]["suggestion"]["kind"] == "merge_ready"
    assert by["pr", 2]["proposals"][0]["command"] == f"gh pr merge 2 --squash -R {REPO}"
    assert by["pr", 3]["state"] == "ci_running" and by["pr", 3]["suggestion"]["kind"] == "wait"
    assert by["pr", 4]["suggestion"]["kind"] == "repair" and "Lint, Test" in by["pr", 4]["blocker"]
    assert by["issue", 7]["state"] == "open" and by["issue", 7]["suggestion"]["kind"] == "assign"
    assert by["issue", 8]["state"] == "in_progress" and ("issue", 2) not in by
    health = {a["id"]: a["health"] for a in st["agents"]}
    assert health == {"claude:s1": "stuck", "codex:s2": "working", "claude:s3": "finished"}
    s = st["summary"]
    assert (s["prs"], s["issues"], s["ci_failed"], s["green"], s["agents_stuck"]) == (4, 2, 2, 1, 1)
    assert "4 open pull requests" in ds.sentence(st, "en") and "בקשות משיכה" in ds.sentence(st, "he")
    assert ds.digest(st, "en").count("\n") >= 4
    assert "last_summary" not in repr(st) and "'body':" not in repr(st)


def test_cache_and_rate_limit(home):
    gh = FakeGitHub()
    t = [NOW]
    g = ds.GitHub(gh, cache_path=Path(home) / "c.json", clock=lambda: t[0])
    ds.build(home, REPO, now=NOW, gh=g)
    n = len(gh.calls)
    ds.build(home, REPO, now=NOW + 10, gh=g)
    assert len(gh.calls) == n                                   # served from the cache
    t[0] = NOW + 1000
    gh.limited = True
    st = ds.build(home, REPO, now=NOW + 1000, gh=g)
    assert "rate_limited" in st["notes"] and st["items"]       # stale cache served
    calls = len(gh.calls)
    t[0] = NOW + 1100
    ds.build(home, REPO, now=NOW + 1100, gh=g)
    assert len(gh.calls) == calls                               # no more asking while blocked
    g2 = ds.GitHub(gh, cache_path=Path(home) / "c.json", clock=lambda: NOW + 1200)
    assert g2.blocked_until > NOW + 1200 and g2.cache          # the cache survives a restart
    dead = ds.GitHub(lambda p: (0, None, {}), clock=lambda: NOW)
    empty = ds.build(home, REPO, now=NOW, gh=dead)
    assert "unavailable" in empty["notes"] and "cannot read GitHub" in ds.sentence(empty, "en")


def test_actions_are_proposals_that_need_approval(home):
    st = ds.build(home, REPO, fetch=FakeGitHub(), now=NOW)
    ran, asked = [], []

    def runner(argv):
        ran.append(argv)
        return 0, "ok"

    def deny(prompt, **kw):
        asked.append((prompt, kw))
        return False, "no"
    r = ds.apply(home, st, "pr2-merge", ask=deny, runner=runner)
    assert r["status"] == "not_approved" and ran == []
    assert f"gh pr merge 2 --squash -R {REPO}" in asked[0][0] and asked[0][1]["risk"] == "high"
    r = ds.apply(home, st, "pr2-merge", ask=lambda p, **k: (True, ""), runner=runner)
    assert r["status"] == "done" and ran == [["pr", "merge", "2", "--squash", "-R", REPO]]
    assert ds.apply(home, st, "pr2-delete", ask=lambda p, **k: (True, ""), runner=runner)["status"] == "unknown"
    assert len(ran) == 1
    assert ds.apply(home, st, "pr1-rerun", ask=lambda p, **k: (True, ""), runner=lambda a: (1, "boom"))["status"] == "failed"


def test_voice_tool_dashboard_and_cli(home, capsys, monkeypatch):
    fake_agents(home)
    gh = FakeGitHub()
    monkeypatch.setattr(ds, "default_fetch", gh)
    reg = SkillRegistry()
    skill = ds.make_skill(home, fetch=gh)
    reg.register(skill)
    r = asyncio.run(reg.call("dev_status", {}, origin="model"))
    assert r.ok and "open pull requests" in r.message and r.message.count(".") == 1
    for t in skill.tools():
        assert t.permission == "read" and len(t.description) <= 80
    ctx = dashboard.Ctx(home, Config())
    try:
        code, body = ds.api_status(ctx, None, None)
        assert code == 200 and body["summary"]["prs"] == 4 and body["sentence_he"]
        assert ds.api_apply(ctx, None, {"id": "bad id"})[0] == 400
        assert ds.api_apply(ctx, None, {"id": "pr99-merge"})[0] == 404
    finally:
        ctx.close()
    assert cli.main(["dev", "status"]) == 0
    out = capsys.readouterr().out
    assert "ci_failed" in out and "PR#2" in out
    assert cli.main(["dev", "proposals"]) == 0 and "pr2-merge" in capsys.readouterr().out
    assert cli.main(["dev", "digest"]) == 0 and "pull requests" in capsys.readouterr().out
    from jarves.features import CATALOG
    assert "dev_status" in CATALOG and "jarves.devstatus" in dashboard.PLUGIN_MODULES
    static = Path(dashboard.__file__).parent / "dashboard_static"
    assert "development: pageDevelopment" in (static / "pillars.js").read_text(encoding="utf-8")
    assert 'data-page="development"' in (static / "index.html").read_text(encoding="utf-8")
