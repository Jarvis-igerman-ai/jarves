"""V5 #190: dev status against REAL payload shapes of the public repo Jarvis-igerman-ai/jarves (recorded read-only on
24-25 Oct 2026 with `gh api`; trimmed to the fields the module may read, no bodies, no user data). No network."""
from __future__ import annotations

import json
from pathlib import Path

from jarves import devstatus as ds

FX = Path(__file__).parent / "fixtures" / "devstatus_real"
REPO = "Jarvis-igerman-ai/jarves"
NOW = 1_800_000_000.0


def load(name):
    return json.loads((FX / name).read_text(encoding="utf-8"))


class RealGitHub:
    """Serves the recorded payloads; check-runs are paged like GitHub (per_page / page) from the recorded list."""

    def __init__(self, repo=REPO, *, moved_from=None):
        self.calls, self.repo, self.moved_from = [], repo, moved_from
        self.pulls, self.issues = load("pulls.json"), load("issues.json")
        self.checks = {p["head"]["sha"]: load(f"check_runs_{p['number']}.json") for p in self.pulls
                       if (FX / f"check_runs_{p['number']}.json").exists()}

    def __call__(self, path):
        self.calls.append(path)
        if self.moved_from and path.startswith(f"/repos/{self.moved_from}/"):
            return 301, {"message": "Moved Permanently"}, {}
        base = f"/repos/{self.repo}"
        if path.startswith(f"{base}/pulls?state=open"):
            return 200, self.pulls, {"x-ratelimit-remaining": "4900"}
        if path.startswith(f"{base}/issues?state=open"):
            return 200, self.issues, {"x-ratelimit-remaining": "4900"}
        if path.startswith(f"{base}/commits/") and "/check-runs" in path:
            sha = path.split("/commits/")[1].split("/")[0]
            d = self.checks.get(sha)
            if d is None:
                return 200, {"total_count": 0, "check_runs": []}, {}
            per = int(path.split("per_page=")[1].split("&")[0])
            page = int(path.split("&page=")[1]) if "&page=" in path else 1
            return 200, {"total_count": d["total_count"], "check_runs": d["check_runs"][(page - 1) * per:page * per]}, {}
        return 404, None, {}


def status(home, gh=None, repo=REPO):
    return ds.build(home, repo, fetch=gh or RealGitHub(), now=NOW)


def fresh(tmp_path, name):
    """The status caches GitHub answers in the profile home: a changed payload needs its own home."""
    d = tmp_path / name
    d.mkdir()
    return d


def test_real_payload_shapes_parse(home):
    st = status(home)
    prs = {i["number"]: i for i in st["items"] if i["type"] == "pr"}
    assert set(prs) == {195, 196, 197, 198}
    assert all(len(i["commit"]) == 10 and i["branch"] for i in prs.values())
    issues = [i for i in st["items"] if i["type"] == "issue"]
    assert len(issues) == 26                       # 30 listed, 4 of them are pull requests (GitHub lists PRs as issues)
    assert not {i["number"] for i in issues} & set(prs)
    assert st["read_only"] is True and st["notes"] == []


def test_queued_checks_with_null_conclusion_mean_ci_running(home):
    st = status(home)
    pr = next(i for i in st["items"] if i["type"] == "pr" and i["number"] == 198)
    assert pr["state"] == "ci_running"


def test_a_cancelled_run_is_not_green(home, tmp_path):
    """Real data: a PR whose runs were cancelled (and one job failed, or all cancelled) was shown 'green' before."""
    st = status(home)
    pr195 = next(i for i in st["items"] if i["type"] == "pr" and i["number"] == 195)
    assert pr195["state"] == "ci_failed" and "failed:" in pr195["blocker"]
    # same payload but without the real failure: only cancelled + success + skipped
    gh = RealGitHub()
    sha = next(p["head"]["sha"] for p in gh.pulls if p["number"] == 195)
    for c in gh.checks[sha]["check_runs"]:
        if c["conclusion"] == "failure":
            c["conclusion"] = "success"
    pr = next(i for i in status(fresh(tmp_path, 'a'), gh)["items"] if i["type"] == "pr" and i["number"] == 195)
    assert pr["state"] == "ci_cancelled" and pr["suggestion"]["kind"] == "rerun"
    assert pr["proposals"][0]["action"] == "rerun" and pr["proposals"][0]["needs_approval"] is True
    assert pr["proposals"][0]["argv"][-2:] == ["-R", REPO]
    assert not any(p["action"] == "merge" for p in pr["proposals"])


def test_checks_beyond_the_first_page_are_read(home, tmp_path):
    """Real PRs carry 47-50+ checks: a failure on the second page must not be missed."""
    gh = RealGitHub()
    sha = next(p["head"]["sha"] for p in gh.pulls if p["number"] == 195)
    runs = gh.checks[sha]["check_runs"]
    fail = next(c for c in runs if c["conclusion"] == "failure")
    runs.remove(fail)
    runs[:] = [{**c, "conclusion": "success" if c["conclusion"] == "cancelled" else c["conclusion"]} for c in runs]
    runs += [dict(runs[0]) for _ in range(110 - len(runs) - 1)] + [fail]       # the failure lands on page 2 (per_page=100)
    gh.checks[sha]["total_count"] = len(runs)
    pr = next(i for i in status(fresh(tmp_path, 'b'), gh)["items"] if i["type"] == "pr" and i["number"] == 195)
    assert pr["state"] == "ci_failed"
    assert any("page=2" in c for c in gh.calls)


def test_the_repo_is_not_hard_coded_and_follows_the_move(home, monkeypatch):
    assert ds.DEFAULT_REPO == REPO and ds.repo_of(home) == REPO
    monkeypatch.setenv(ds.REPO_ENV, "someone/else")
    assert ds.repo_of(home) == "someone/else"                   # env override
    monkeypatch.setenv(ds.REPO_ENV, "not a repo")
    assert ds.repo_of(home) == REPO                             # bad value ignored
    # an old address that answers 301 is reported as "moved", never as an empty-but-fine status or a crash
    st = ds.build(home, "igalgerman/jarves", fetch=RealGitHub(moved_from="igalgerman/jarves"), now=NOW)
    assert st["items"] == [] and "moved" in st["notes"]


def test_default_fetch_follows_a_redirect_for_a_moved_repo(monkeypatch):
    import httpx
    seen = {}

    def fake_get(url, **kw):
        seen.update(kw)
        return httpx.Response(200, json=[], request=httpx.Request("GET", url))
    monkeypatch.setenv("GH_TOKEN", "x" * 20)
    monkeypatch.setattr(httpx, "get", fake_get)
    assert ds.default_fetch("/repos/igalgerman/jarves/pulls")[0] == 200
    assert seen["follow_redirects"] is True


def test_unsupported_routes_are_labelled_and_nothing_writes(home):
    gh = RealGitHub()
    st = status(home, gh)
    labels = {u["route"]: u["label"] for u in st["unsupported"]}
    assert labels == {"live_push": "unsupported", "attach_session": "unsupported"}
    assert all(c.startswith("/repos/") for c in gh.calls)       # GET paths only: this fake has no write route at all
