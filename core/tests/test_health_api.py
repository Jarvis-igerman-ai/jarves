"""SW-V4 O (#17): the Health page API (real dashboard HTTP server on a tmp home), the spoken summary (en + he), the
`jarvis health` CLI (parity), ticket drafts filed only after approval and only on a private repo (fake `gh`), and
the feature registration (guide, capability row, wizard group, bundle check, Hebrew texts)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import diag_replay as rp
from jarves import cli
from jarves.diag import FEATURE, db, service, tickets
from jarves.store import Store
from jarves.userdata import open_db
from test_dashboard import dash, get, req  # noqa: F401  (fixture + helpers)

STATIC = Path(__file__).resolve().parents[1] / "src" / "jarves" / "dashboard_static"


def post(d, path, body):
    r, data = req(d, "POST", path, headers={"X-Jarves": "1"}, body=body)
    return r.status, json.loads(data)


@pytest.fixture
def planted(home):
    st = Store(home / "store.db")
    rp.build(st, plant=("false_action_claim", "ram_creep", "version_regression"), now=__import__("time").time())
    from jarves.diag import anomaly
    anomaly.save(st, anomaly.Detector(st).run(2 * 86400), __import__("time").time())
    st.close()
    return home


def test_health_report_shape(planted, dash):
    d = get(dash, "/api/health")
    fps = {f["fingerprint"] for f in d["findings"]}
    assert {"anomaly:claim:LIVE_FALSE_ACTION_CLAIM", "trend:ram_mb", "version:4.0.1"} <= fps
    assert d["score"] < 100 and d["open"] >= 3 and d["enabled"] is True
    assert d["version_report"]["version"] == "4.0.1" and d["preflight"]["items"] and d["canary"]
    assert d["ai_settings"]["health.ai_review_every_days"] == 2 and d["ai_settings"]["never_shown"]
    assert len(d["ai_settings"]["never_shown_he"]) == len(d["ai_settings"]["never_shown"])
    assert {r["id"] for r in d["remedies"]} >= {"restart_face_link", "rollback_update"}
    assert d["summary"].startswith("Health score") and "ציון התקינות שלי" in d["summary_he"]
    assert d["summary"].count(". ") <= 3                                       # 2-3 sentences


def test_decide_settings_and_review_schedule(planted, dash):
    s, r = post(dash, "/api/health/decide", {"fingerprint": "trend:ram_mb", "op": "snooze", "days": 3})
    assert s == 200 and r["state"] == "snoozed"
    assert post(dash, "/api/health/decide", {"fingerprint": "nope", "op": "accept"})[0] == 404
    assert post(dash, "/api/health/settings", {"health.ai_review_run_cap_usd": 50})[0] == 400
    assert post(dash, "/api/health/settings", {"health.ai_review_job_id": "x"})[0] == 400
    s, r = post(dash, "/api/health/settings", {"health.ai_review_every_days": "3"})
    assert s == 200 and r["health.ai_review_every_days"] == 3 and r["health.ai_review_enabled"] is True
    dbs = open_db(planted)
    try:
        from jarves.scheduler import Scheduler
        job = Scheduler(dbs).job(dbs.setting("health.ai_review_job_id"))
        assert job["status"] == "active" and "every 3 days" in job["name"] and job["tool"] == "health_ai_review_run"
        post(dash, "/api/health/settings", {"health.ai_review_every_days": "off"})
        assert Scheduler(dbs).job(job["id"])["status"] == "paused" and dbs.setting("health.ai_review_enabled") is False
    finally:
        dbs.close()


def test_on_demand_review_needs_the_estimate_first_and_spends_nothing_without_a_key(planted, dash):
    s, r = post(dash, "/api/health/ai/run", {})
    assert s == 400 and r["needs_confirm"]
    pv = get(dash, "/api/health/ai/preview")
    assert pv["est_usd"] >= 0 and pv["model"] == "gpt-6.1-sol" and pv["never_shown"]
    s, r = post(dash, "/api/health/ai/run", {"confirm": True, "est_usd": pv["est_usd"]})
    assert s == 200 and r["status"] == "skipped" and r["cost_usd"] == 0.0
    assert "no API key route" in r["reason"] or r["reason"] == "nothing new"


def test_remediation_from_an_external_dashboard(planted, dash, monkeypatch):
    s, r = post(dash, "/api/health/remediate", {"remedy": "restart_face_link"})
    assert s == 409 and "running JARVES" in r["error"]
    assert post(dash, "/api/health/remediate", {"remedy": "rm"})[0] == 400
    (planted / "cache").mkdir()
    (planted / "cache" / "speech_x.wav").write_bytes(b"RIFF")
    monkeypatch.setattr("jarves.approvals.ask_owner", lambda *a, **k: (False, "not approved"))
    s, r = post(dash, "/api/health/remediate", {"remedy": "clear_speech_cache"})
    assert s == 200 and r["status"] == "denied" and (planted / "cache" / "speech_x.wav").exists()
    monkeypatch.setattr("jarves.approvals.ask_owner", lambda *a, **k: (True, ""))
    s, r = post(dash, "/api/health/remediate", {"remedy": "clear_speech_cache"})
    assert s == 200 and r["status"] == "done" and not (planted / "cache" / "speech_x.wav").exists()


class FakeGh:
    def __init__(self, visibility="PRIVATE", existing=None):
        self.visibility, self.existing, self.calls = visibility, existing or [], []

    def __call__(self, argv, stdin=None):
        self.calls.append((argv, stdin))
        if argv[:2] == ["repo", "view"]:
            return 0, json.dumps({"visibility": self.visibility})
        if argv[:2] == ["issue", "list"]:
            return 0, json.dumps(self.existing)
        if argv[:2] == ["issue", "create"]:
            self.existing = [{"number": 7, "url": "https://github.com/igalgerman/jarves/issues/7"}]
            return 0, "https://github.com/igalgerman/jarves/issues/7\n"
        if argv[:2] == ["issue", "comment"]:
            return 0, "commented"
        return 1, "unexpected"


def test_ticket_draft_then_file_only_after_approval_on_the_private_repo(planted, dash, monkeypatch):
    s, t = post(dash, "/api/health/ticket", {"fingerprint": "version:4.0.1"})
    assert s == 200 and t["status"] == "draft" and "jarves-fingerprint" in t["body"] and "4.0.1" in t["body"]
    gh = FakeGh()
    monkeypatch.setattr(tickets, "gh_runner", gh)
    monkeypatch.setattr(service.tickets, "file", _file_with(gh))
    monkeypatch.setattr("jarves.approvals.ask_owner", lambda *a, **k: (False, "not approved"))
    s, r = post(dash, "/api/health/ticket/file", {"id": t["id"]})
    assert r["status"] == "draft" and "no ticket repo" in r["reason"] and not gh.calls     # V4-RP-2: default = local
    assert post(dash, "/api/health/settings", {"health.ticket_repo": "igalgerman/jarves-tickets"})[0] == 200
    s, r = post(dash, "/api/health/ticket/file", {"id": t["id"]})
    assert r["status"] == "draft" and not [c for c in gh.calls if c[0][:2] == ["issue", "create"]]   # denied: nothing
    prompts = []
    monkeypatch.setattr("jarves.approvals.ask_owner", lambda home, prompt, **k: (prompts.append(prompt) or True, ""))
    s, r = post(dash, "/api/health/ticket/file", {"id": t["id"]})
    assert r["status"] == "filed" and r["issue_url"].endswith("/issues/7")
    assert t["title"] in prompts[0] and "jarves-fingerprint" in prompts[0]             # the full text was shown
    create = [c for c in gh.calls if c[0][:2] == ["issue", "create"]]
    assert len(create) == 1 and "igalgerman/jarves-tickets" in create[0][0]
    # the same finding again: a duplicate fingerprint comments on the open issue instead
    s, t2 = post(dash, "/api/health/ticket", {"fingerprint": "version:4.0.1"})
    s, r2 = post(dash, "/api/health/ticket/file", {"id": t2["id"]})
    assert r2["status"] == "commented" and len([c for c in gh.calls if c[0][:2] == ["issue", "create"]]) == 1


def _file_with(gh):
    orig = tickets.file

    async def f(store, tid, *, ask, repo, by, run=None):
        return await orig(store, tid, ask=ask, repo=repo, by=by, run=gh)
    return f


async def test_a_public_repo_is_refused(home):
    st = Store(home / "store.db")
    try:
        d = tickets.draft({"fingerprint": "x:y", "title": "t", "severity": "low", "evidence": ["sk-abcdefghijklmnop1234"]})
        assert "sk-abcdefghijklmnop1234" not in d["body"]                       # redacted
        tid = tickets.save_draft(st, d)
        asked = []

        async def ask(*a, **k):
            asked.append(1)
            return True
        r = await tickets.file(st, tid, ask=ask, repo="igalgerman/jarves", by="test", run=FakeGh("PUBLIC"))
        assert r["status"] == "refused" and not asked
    finally:
        st.close()


# ---------------------------------------------------------------------- spoken summary + voice tools on a real Core
async def test_voice_tools_on_a_real_core(core):
    r = await core.registry.call("health_summary", {}, origin="model")
    assert r.ok and r.code == "HEALTH_SUMMARY" and r.data["dashboard"] == "#/health"
    assert "100" in r.message
    core.cfg.voice.language = "en"
    r = await core.registry.call("health_summary", {}, origin="model")
    assert r.message.startswith("I'm doing well") or r.message.startswith("Health score")
    r = await core.registry.call("health_ai_review_summary", {}, origin="model")
    assert r.ok and "not run yet" in r.message
    core.cfg.voice.language = "he"
    r = await core.registry.call("health_ai_review_summary", {}, origin="model")
    assert "הבודק" in r.message
    jobs = core.ops.scheduler.jobs()
    assert {j["tool"] for j in jobs} >= {"health_canary_run", "health_ai_review_run"}


# ---------------------------------------------------------------------- CLI parity
def test_cli_health(planted, capsys):
    assert cli.main(["health"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("HEALTH ") and "version report: 4.0.0 -> 4.0.1" in out
    assert cli.main(["health", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["score"] < 100
    assert cli.main(["health", "report"]) == 0 and "evidence:" in capsys.readouterr().out
    assert cli.main(["health", "decide", "trend:ram_mb", "--op", "dismiss"]) == 0
    assert "dismissed" in capsys.readouterr().out
    assert cli.main(["health", "ai-review", "--every", "3d"]) == 0 and "every 3 day" in capsys.readouterr().out
    assert cli.main(["health", "ai-review", "--every", "off"]) == 0 and "off" in capsys.readouterr().out
    assert cli.main(["health", "ai-review", "--preview"]) == 0 and "never shown" in capsys.readouterr().out
    assert cli.main(["health", "remediate", "restart_face_link"]) == 2
    assert "needs the running JARVES" in capsys.readouterr().out
    assert cli.main(["health", "ticket", "version:4.0.1"]) == 0 and "not filed" in capsys.readouterr().out
    assert cli.main(["health", "preflight"]) in (0, 1) and "preflight" in capsys.readouterr().out


def test_cli_diag_ci_bundle(planted, tmp_path, capsys):
    out = tmp_path / "diag-ci"
    assert cli.main(["diag", "--ci", "--out", str(out)]) == 0
    assert (out / "jarves-diag-ci.zip").is_file() and json.loads((out / "health-summary.json").read_text())["findings"]
    # a secret-looking string that survives anywhere: nothing is kept, exit 2
    st = Store(planted / "store.db")
    db.write(st, "UPDATE diag_findings SET title='leak ghp_FAKEabcdefghijklmnopqrstuvwxyz0123' WHERE fingerprint='trend:ram_mb'")
    st.close()
    from jarves import diagbundle
    orig = diagbundle.sanitize
    try:
        diagbundle.sanitize = lambda text: text                                 # simulate a redaction gap
        assert cli.main(["diag", "--ci", "--out", str(out)]) == 2 and not out.exists()
    finally:
        diagbundle.sanitize = orig


# ---------------------------------------------------------------------- registration everywhere
def test_feature_registered_on_every_surface():
    from jarves import capabilities, cli_parity, features as feat, guides, locales, setup_wizard
    assert FEATURE in feat.CATALOG and feat.CATALOG[FEATURE].skills == ["health"]
    assert guides.for_feature(FEATURE).id == "health"
    assert cli_parity.FEATURES[FEATURE] == "health" and cli_parity.TOOL_GROUPS["health"] == "health"
    assert any(FEATURE in ids for _, _, _, ids in setup_wizard.GROUPS)
    rows = {r["id"]: r for r in setup_wizard.check_addons()["rows"]}
    assert rows[FEATURE]["ok"], rows[FEATURE]
    t = feat.CATALOG[FEATURE].targets
    assert t["windows"]["status"] and t["linux_arm64_pi"]["roadmap_issue"]
    for text in (feat.CATALOG[FEATURE].about, t["windows"]["evidence"], t["windows"]["blocker"],
                 t["linux_arm64_pi"]["blocker"]):
        assert locales.text("he", text), text
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'data-page="health"' in html and "/static/health.js" in html
    assert "Object.assign(PAGES, { health: pageHealth })" in (STATIC / "health.js").read_text(encoding="utf-8")
    assert capabilities is not None


def test_cli_canary_profile_option_is_not_the_jarvis_profile(home, monkeypatch, capsys):
    """V4-HE-1: `jarvis health canary [--profile ci|owner]` shares its flag name with the global `--profile NAME`;
    the canary default ("owner") used to be taken as a JARVES profile and the command died with PROFILE ERROR."""
    seen = []

    async def fake_run_all(profile, **kw):
        seen.append((profile, kw.get("real_home")))
        return {"ts": 1.0, "profile": profile, "passed": 1, "failed": 0, "blocked": 0, "ms": 1.0,
                "results": [{"status": "pass", "name": "x", "ms": 1.0, "detail": "ok"}]}

    from jarves.diag import canary
    monkeypatch.setattr(canary, "run_all", fake_run_all)
    monkeypatch.setattr(canary, "save", lambda st, rep: 1)
    assert cli.main(["health", "canary"]) == 0
    assert cli.main(["health", "canary", "--profile", "ci"]) == 0
    assert [p for p, _ in seen] == ["owner", "ci"] and seen[0][1] is not None and seen[1][1] is None
    assert "PROFILE ERROR" not in capsys.readouterr().err


def test_cli_prompts_without_a_terminal_are_a_no_not_a_traceback(home, monkeypatch, capsys):
    """V4-HE-2: `jarvis health ai-review --now` / `remediate` with stdin closed raised EOFError (traceback)."""
    import io
    import sys
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    rc = cli.main(["health", "remediate", "clear_guide_cache"])
    out = capsys.readouterr()
    assert rc != 0 and "denied" in (out.out + out.err).lower()
    rc = cli.main(["health", "ai-review", "--now"])
    out = capsys.readouterr()
    assert "Traceback" not in out.err and "EOFError" not in out.err + out.out
