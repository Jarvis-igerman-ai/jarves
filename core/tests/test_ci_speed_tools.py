"""4.0.1 fast CI + release: the planner, flake retry, installer reuse, release status and dependabot gate. Offline: fake `gh`
callables, temp dirs, no network, no skips."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core" / "tools"))
sys.path.insert(0, str(ROOT / "packaging"))

import ci_flake  # noqa: E402
import ci_plan  # noqa: E402
import ci_reuse  # noqa: E402
import dependabot_gate  # noqa: E402
import release  # noqa: E402
import release_status  # noqa: E402

WF = ROOT / ".github" / "workflows"


# ------------------------------------------------------------------------------------------------ change classification
def test_docs_claude_md_and_readme_changes_need_no_job():
    f = ci_plan.classify(["docs/HANDOFF.md", "CLAUDE.md", "README.md"])
    assert not any(f.values())


def test_guides_only_runs_just_the_linux_shard():
    f = ci_plan.classify(["core/src/jarves/guides/data/foo.toml", "docs/x.md"])
    assert f["code"] and not f["full"] and not any(f[k] for k in ("installer", "perf", "e2e", "cad", "blender", "godot", "whatsapp"))


def test_job_specific_paths():
    assert ci_plan.classify(["bridge/whatsapp/main.go"])["whatsapp"]
    assert not ci_plan.classify(["core/src/jarves/cli.py"])["whatsapp"]
    assert ci_plan.classify(["packaging/bridge_pin.json"])["whatsapp"]
    g = ci_plan.classify(["godot/face/face.gd"])
    assert g["godot"] and g["blender"] and g["installer"] and not g["cad"] and not g["whatsapp"]
    assert ci_plan.classify(["cad/build_pebble.py"])["cad"] and not ci_plan.classify(["core/src/jarves/cli.py"])["cad"]
    c = ci_plan.classify(["core/src/jarves/cli.py"])
    assert c["code"] and c["full"] and c["installer"] and c["perf"] and not c["cad"] and not c["blender"] and not c["godot"]


def test_main_push_and_workflow_changes_run_everything_and_a_proven_tree_runs_nothing():
    assert all(ci_plan.classify([], "push").values())
    assert all(ci_plan.classify([".github/workflows/jarves-checks.yml"]).values())
    assert not any(ci_plan.classify([], "push", reused=True).values())


# ----------------------------------------------------------------------------------------------------- shard planning
def _tests_dir(tmp_path, sizes):
    d = tmp_path / "tests"
    d.mkdir()
    for name, n in sizes.items():
        (d / name).write_text("\n".join(f"def test_{i}():\n    pass" for i in range(n)) + "\n", encoding="utf-8")
    return d


def test_plan_covers_every_file_once_excludes_browser_files_and_uses_measured_seconds(tmp_path):
    d = _tests_dir(tmp_path, {"test_a.py": 50, "test_b.py": 5, "test_c.py": 5, "test_d.py": 5, "test_browser.py": 9})
    by_count = ci_plan.plan(d, {}, windows=2)
    flat = [f for s in by_count["windows"] for f in s]
    assert sorted(flat) == ["test_a.py", "test_b.py", "test_c.py", "test_d.py"]
    # measured data says the 5-test files are the slow ones: the plan changes accordingly, slowest first in a shard
    measured = ci_plan.plan(d, {"test_a.py": 10, "test_b.py": 300, "test_c.py": 290, "test_d.py": 280}, windows=2)
    assert measured["windows"][0][0] == "test_b.py" and "test_b.py" not in measured["windows"][1]
    assert max(measured["expected_s"]["windows"]) <= 580
    assert ci_plan.shard_files(measured, "windows", "1/2") == measured["windows"][0]
    with pytest.raises(SystemExit):
        ci_plan.shard_files(measured, "windows", "1/3")


def test_real_test_directory_plan_is_balanced():
    p = ci_plan.plan(windows=3)
    flat = [f for s in p["windows"] for f in s]
    assert len(flat) == len(set(flat)) and not set(flat) & set(ci_plan.BROWSER_FILES)
    assert len(flat) >= 100
    linux_flat = [f for shard in p["linux"] for f in shard]
    assert len(p["linux"]) == 3 and sorted(linux_flat) == sorted(flat)
    assert len(linux_flat) == len(set(linux_flat))
    exp = p["expected_s"]["windows"]
    assert max(exp) <= min(exp) * 1.35 + 5


def test_durations_merge_from_junit_blends_and_keeps_the_slowest_sample(tmp_path):
    def junit(name, times):
        cases = "".join(f'<testcase classname="tests.{f[:-3]}" name="t{i}" time="{t}"/>' for i, (f, t) in enumerate(times))
        (tmp_path / name).write_text(f'<testsuites><testsuite name="p">{cases}</testsuite></testsuites>', encoding="utf-8")
    junit("win.xml", [("test_x.py", 40), ("test_x.py", 20), ("test_y.py", 5)])
    junit("lin.xml", [("test_x.py", 10), ("test_y.py", 2)])
    into = tmp_path / "durations.json"
    into.write_text(json.dumps({"test_x.py": 100}), encoding="utf-8")
    assert ci_plan.main(["durations-merge", "--junit", str(tmp_path / "win.xml"), str(tmp_path / "lin.xml"), "--into", str(into)]) == 0
    d = json.loads(into.read_text(encoding="utf-8"))
    assert d == {"test_x.py": 80.0, "test_y.py": 5.0}          # x: 0.5*100 + 0.5*max(60, 10); y: new


def test_classify_cli_prints_outputs_and_a_plan(tmp_path, capsys):
    f = tmp_path / "files.txt"
    f.write_text("core/src/jarves/cli.py\n", encoding="utf-8")
    assert ci_plan.main(["classify", "--event", "pull_request", "--files-from", str(f)]) == 0
    out = capsys.readouterr().out.splitlines()
    kv = dict(line.split("=", 1) for line in out)
    assert kv["code"] == "true" and kv["cad"] == "false"
    plan = json.loads(kv["plan"])
    assert len(plan["windows"]) == 3 and len(plan["linux"]) == 3


def test_conftest_runs_exactly_the_planned_files_slowest_first():
    sys.path.insert(0, str(ROOT / "core" / "tests"))
    import conftest

    class Item:
        def __init__(self, f):
            self.fspath = f"/x/tests/{f}"

        def get_closest_marker(self, name):
            return None

    class Cfg:
        def __init__(self, **opts):
            self.opts = {"--browser": "include", "--shard": "1/1", "--shard-files": "", "--durations-file": "", **opts}
            self.hook = self
            self.gone = []

        def getoption(self, k):
            return self.opts[k]

        def pytest_deselected(self, items):
            self.gone += items

    items = [Item(f) for f in ("test_a.py", "test_a.py", "test_b.py", "test_c.py", "test_c.py")]
    cfg = Cfg(**{"--shard-files": "test_c.py,test_a.py"})
    conftest.pytest_collection_modifyitems(cfg, items)
    assert [i.fspath[-9:] for i in items] == ["test_c.py", "test_c.py", "test_a.py", "test_a.py"] and len(cfg.gone) == 1


# -------------------------------------------------------------------------------------------------------- flake retry
JUNIT = """<testsuites><testsuite>{cases}</testsuite></testsuites>"""


def _case(cls, name, msg=None):
    body = f'<failure message="{msg}">trace</failure>' if msg else ""
    return f'<testcase classname="tests.{cls}" name="{name}">{body}</testcase>'


def test_only_known_transient_failures_are_retried_and_only_their_files(tmp_path):
    p = tmp_path / "j.xml"
    p.write_text(JUNIT.format(cases=_case("test_a", "t1", "OSError: [WinError 10048] address in use") + _case("test_b", "t2")
                              + _case("test_c", "t3", "ConnectionResetError: [WinError 10054]")), encoding="utf-8")
    assert ci_flake.retry_files(p) == ["test_a.py", "test_c.py"]
    p.write_text(JUNIT.format(cases=_case("test_a", "t1", "OSError: [WinError 10048]") + _case("test_b", "t2", "AssertionError: 1 != 2")),
                 encoding="utf-8")
    assert ci_flake.retry_files(p) is None                  # one real failure: nothing is retried
    p.write_text(JUNIT.format(cases=_case("test_a", "t1")), encoding="utf-8")
    assert ci_flake.retry_files(p) is None                  # green: nothing to retry
    many = "".join(_case(f"test_{i}", "t", "timed out") for i in range(ci_flake.MAX_FILES + 1))
    p.write_text(JUNIT.format(cases=many), encoding="utf-8")
    assert ci_flake.retry_files(p) is None                  # a wave of failures is a regression, not a flake


def test_flake_report_lists_what_passed_only_on_retry(tmp_path):
    first, second = tmp_path / "1.xml", tmp_path / "2.xml"
    first.write_text(JUNIT.format(cases=_case("test_a", "t1", "WinError 10054")), encoding="utf-8")
    second.write_text(JUNIT.format(cases=_case("test_a", "t1")), encoding="utf-8")
    out = tmp_path / "flake.json"
    assert ci_flake.main(["report", "--first", str(first), "--second", str(second), "--out", str(out)]) == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert [f["test"] for f in doc["flaky_passed_on_retry"]] == ["tests.test_a::t1"] and doc["failed_again"] == []
    assert ci_flake.main(["failed-files", "--junit", str(second)]) == 3


# ------------------------------------------------------------------------------------------------ installer reuse
TREE = "a" * 40


def _api(artifacts, runs):
    def api(path):
        if "/actions/artifacts" in path:
            return {"artifacts": artifacts}
        return runs[int(path.rsplit("/", 1)[1])]
    return api


def _run(rid, **kw):
    base = {"id": rid, "name": "jarves-checks", "status": "completed", "conclusion": "success", "event": "pull_request",
            "head_repository": {"full_name": "o/r"}, "head_sha": "s" * 40, "run_number": rid}
    return {**base, **kw}


def test_resolve_picks_the_newest_green_unexpired_artifact_of_this_repo():
    arts = [{"id": 1, "name": ci_reuse.artifact_name(TREE), "created_at": "2026-10-01", "workflow_run": {"id": 10}},
            {"id": 2, "name": ci_reuse.artifact_name(TREE), "created_at": "2026-10-02", "workflow_run": {"id": 11}}]
    hit = ci_reuse.resolve("o/r", TREE, _api(arts, {10: _run(10), 11: _run(11)}))
    assert hit["run_id"] == 11 and hit["name"] == "jarves-installer-" + TREE


@pytest.mark.parametrize("bad", [dict(conclusion="failure"), dict(status="in_progress", conclusion=None),
                                 dict(head_repository={"full_name": "evil/fork"}), dict(name="other-workflow")])
def test_resolve_refuses_failed_unfinished_forked_or_foreign_runs(bad):
    arts = [{"id": 1, "name": ci_reuse.artifact_name(TREE), "created_at": "2026-10-01", "workflow_run": {"id": 10}}]
    assert ci_reuse.resolve("o/r", TREE, _api(arts, {10: _run(10, **bad)})) is None


def test_resolve_skips_expired_and_short_trees():
    arts = [{"id": 1, "name": "x", "created_at": "2026-10-01", "expired": True, "workflow_run": {"id": 10}}]
    assert ci_reuse.resolve("o/r", TREE, _api(arts, {10: _run(10)})) is None
    assert ci_reuse.resolve("o/r", "abc", _api(arts, {})) is None


def test_content_tree_ignores_documentation_only():
    code = ["100644 blob 1111\tcore/src/a.py", "100644 blob 2222\tpackaging/build_windows.ps1"]
    base = ci_reuse.content_tree(code + ["100644 blob 3333\tdocs/x.md", "100644 blob 4444\tREADME.md"])
    assert base == ci_reuse.content_tree(code + ["100644 blob 9999\tdocs/x.md"]) == ci_reuse.content_tree(code)
    assert base != ci_reuse.content_tree(["100644 blob 1112\tcore/src/a.py"] + code[1:])
    assert ci_reuse.git_tree("HEAD")        # works in a real checkout


# ---------------------------------------------------------------------------- stamping instead of rebuilding
def _dist(tmp_path, tree=TREE, channel="pr", commit="p" * 40):
    import hashlib
    exe = tmp_path / "JARVES-Setup-4.0.1.77.exe"
    exe.write_bytes(b"installer-bytes")
    sha = hashlib.sha256(b"installer-bytes").hexdigest()
    (tmp_path / (exe.name + ".sha256")).write_text(f"{sha}  {exe.name}\n", encoding="ascii")
    (tmp_path / "build-info.json").write_text(json.dumps({
        "version": "4.0.1.77", "commit": commit, "channel": channel, "tree": tree, "built": "2026-10-22T00:00:00Z",
        "installer": exe.name, "sha256": sha, "size": 15, "python": "3.12", "godot": "4.7.2"}), encoding="utf-8")
    return tmp_path, exe


def test_reused_pr_installer_is_verified_by_tree_and_stamped_without_touching_the_exe(tmp_path):
    d, exe = _dist(tmp_path)
    before = exe.read_bytes()
    with pytest.raises(SystemExit):
        release.verify(d, "m" * 40)                                  # a PR build is not a main build...
    assert release.verify(d, "m" * 40, TREE)["version"] == "4.0.1.77"    # ...but its tree is proven
    with pytest.raises(SystemExit):
        release.verify(d, "m" * 40, "b" * 40)                        # another tree is refused
    info = release.stamp(d, "m" * 40, TREE)
    assert info["commit"] == "m" * 40 and info["built_from_commit"] == "p" * 40 and info["channel"] == "main"
    assert info["version"] == "4.0.1.77" and exe.read_bytes() == before
    assert release.verify(d, "m" * 40)["channel"] == "main"          # the normal main check now passes
    out = tmp_path / "cand"
    assert release.candidate(d, "m" * 40, out, env={})["version"] == "4.0.1.77"


def test_dev_builds_are_never_published_even_with_a_tree(tmp_path):
    d, _ = _dist(tmp_path, channel="dev")
    with pytest.raises(SystemExit):
        release.verify(d, "m" * 40, TREE)


# ------------------------------------------------------------------------------------------------- release status
def test_release_status_reports_state_and_blockers():
    sha, tree = "c" * 40, TREE

    def api(path):
        if path.endswith("commits/main"):
            return {"sha": sha, "commit": {"tree": {"sha": "0" * 40}}}
        if "actions/runs?branch=main" in path:
            return {"workflow_runs": [{"name": "jarves-checks", "run_number": 9, "status": "completed", "conclusion": "success",
                                       "html_url": "u"}]}
        if "workflows/package.yml/runs" in path:
            return {"workflow_runs": []}
        if "/actions/artifacts" in path and "release-candidate" in path:
            return {"artifacts": []}
        if "/actions/artifacts" in path:
            return {"artifacts": [{"id": 1, "name": "x", "created_at": "2026-10-01", "workflow_run": {"id": 5}}]}
        if path.endswith("/actions/runs/5"):
            return _run(5, head_repository={"full_name": "o/r"})
        if "releases?per_page" in path:
            return [{"tag_name": "win-v4.0.0.12", "prerelease": False, "assets": [{"name": "latest.json"}]},
                    {"tag_name": "beta-v4.0.1.3", "prerelease": True, "assets": []}]
        if "variables/ENABLE_BETA_PUBLISH" in path:
            raise RuntimeError("404")
        raise AssertionError(path)

    s = release_status.build_status("o/r", "o/rel", api)
    assert s["stable"] == "win-v4.0.0.12" and s["beta"] == "beta-v4.0.1.3" and s["beta_publishing"] is False
    assert s["installer_artifact"]["run_id"] == 5
    assert any("release candidate" in b for b in s["blocking"]) and not any("main CI" in b for b in s["blocking"])
    text = release_status.render(s)
    assert "BLOCKING" in text and "stable gate" in text


# ------------------------------------------------------------------------------------------------- dependabot gate
@pytest.mark.parametrize("title,kind,ok", [
    ("Bump uvicorn from 0.30.1 to 0.30.2 in /core", "patch", True),
    ("Bump actions/checkout from 6.0.1 to 6.1.0", "minor", True),
    ("Bump pytest from 8.4.2 to 9.0.0 in /core", "major", False),
    ("Bump numpy from 0.9.1 to 0.10.0", "major", False),            # 0.x minor is breaking
    ("Update something odd", "unknown", False),
])
def test_dependabot_gate_title_rules(title, kind, ok):
    assert dependabot_gate.classify(title) == kind and dependabot_gate.eligible(kind) is ok


def test_dependabot_group_updates_are_judged_by_the_body():
    t = "Bump the pip group in /core with 2 updates"
    minor = "Updates `a` from 1.0.0 to 1.0.1\nUpdates `b` from 2.1.0 to 2.2.0"
    major = minor + "\nUpdates `c` from 3.9.0 to 4.0.0"
    assert dependabot_gate.classify(t, minor) == "group" and dependabot_gate.classify(t, major) == "major"
    assert dependabot_gate.classify(t, "") == "unknown"


# --------------------------------------------------------------------------------------------- workflow invariants
def _wf(name):
    return yaml.safe_load((WF / name).read_text(encoding="utf-8"))


def test_installer_runs_in_parallel_with_tests_and_publishing_waits_for_the_whole_run():
    checks = _wf("jarves-checks.yml")
    inst = checks["jobs"]["installer"]
    assert set(inst["needs"]) == {"changes", "validate"}  # no dependency on Core tests
    assert any("jarves-installer-" in str(s.get("with", {}).get("name", "")) for s in inst["steps"])
    pkg = _wf("package.yml")
    cond = pkg["jobs"]["candidate"]["if"]
    assert "conclusion == 'success'" in cond and "workflow_dispatch" in cond      # whole checks run green (tests + installer)
    assert "ci_reuse.py resolve" in "\n".join(s.get("run", "") for s in pkg["jobs"]["candidate"]["steps"])
    assert "build_windows" not in (WF / "package.yml").read_text(encoding="utf-8")   # publishing never builds


def test_no_duplicate_runs_per_commit():
    t = _wf("jarves-checks.yml")[True]
    assert t["push"]["branches"] == ["main", "next"] and "pull_request" in t           # branch pushes have no push trigger
    conc = _wf("jarves-checks.yml")["concurrency"]
    assert "pull_request.number" in conc["group"] and conc["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"


def test_ci_ok_aggregates_every_job_and_job_names_are_stable():
    jobs = _wf("jarves-checks.yml")["jobs"]
    assert set(jobs["ci-ok"]["needs"]) == set(jobs) - {"ci-ok", "durations", "failure-summary"}
    assert jobs["core"]["name"] == "Python Core tests (${{ matrix.os }} shard ${{ matrix.shard }})"
    assert jobs["ci-ok"]["if"] == "always()"


def test_dependabot_workflow_never_checks_out_pr_code_and_has_a_kill_switch():
    doc = _wf("dependabot-automerge.yml")
    job = doc["jobs"]["label"]
    assert "dependabot[bot]" in job["if"] and "DEPENDABOT_AUTOMERGE" in job["if"]
    for s in job["steps"]:
        assert "ref" not in s.get("with", {})                           # base branch only (pull_request_target)
    assert "dependabot_gate.py" in "\n".join(s.get("run", "") for s in job["steps"])


def test_docs_describe_the_pipeline():
    perf = (ROOT / "docs" / "CI_PERFORMANCE.md").read_text(encoding="utf-8")
    rel = (ROOT / "docs" / "RELEASING.md").read_text(encoding="utf-8")
    for needle in ("Before / after", "branch protection", "5 minutes", "Version stamping"):
        assert needle in perf, needle
    assert "release_status.py" in rel and "ci-ok" in perf


def test_platform_measurements_balance_different_hot_files_without_losing_coverage(tmp_path):
    d = _tests_dir(tmp_path, {"test_a.py": 1, "test_b.py": 1, "test_c.py": 1, "test_d.py": 1})
    windows = {"test_a.py": 100, "test_b.py": 2, "test_c.py": 40, "test_d.py": 3}
    linux = {"test_a.py": 2, "test_b.py": 100, "test_c.py": 3, "test_d.py": 40}
    p = ci_plan.plan(d, windows, windows=2, linux=2, linux_durations=linux)
    assert p["windows"][0][0] == "test_a.py" and p["linux"][0][0] == "test_b.py"
    for platform, times in (("windows", windows), ("linux", linux)):
        flat = [f for shard in p[platform] for f in shard]
        assert sorted(flat) == sorted(times) and len(flat) == len(set(flat))
        assert p["expected_s"][platform] == [round(sum(times[f] for f in shard)) for shard in p[platform]]


def test_committed_platform_seeds_are_measured_and_plan_the_current_suite():
    windows = ci_plan.load_durations(ROOT / "core/tests/durations-windows.json")
    linux = ci_plan.load_durations(ROOT / "core/tests/durations-linux.json")
    assert len(windows) >= 100 and len(linux) >= 100 and windows != linux
    p = ci_plan.plan(durations=windows, linux_durations=linux)
    expected = sorted(f.name for f in ci_plan.TESTS.glob("test_*.py") if f.name not in ci_plan.BROWSER_FILES)
    for platform in ("windows", "linux"):
        assert sorted(f for shard in p[platform] for f in shard) == expected
        loads = p["expected_s"][platform]
        weights = ci_plan.file_weights(ci_plan.TESTS, windows if platform == "windows" else linux)
        assert max(loads) - min(loads) <= max(weights.values()) + 2


def test_timing_refresh_separates_platforms_and_includes_successful_same_repo_prs():
    jobs = _wf("jarves-checks.yml")["jobs"]
    refresh = jobs["durations"]
    assert refresh["strategy"]["matrix"]["platform"] == ["linux", "windows"]
    condition = refresh["if"]
    assert "needs.core.result == 'success'" in condition and "needs.changes.outputs.full == 'true'" in condition
    assert "github.event.pull_request.head.repo.full_name == github.repository" in condition
    steps = refresh["steps"]
    download = next(s for s in steps if "download-artifact" in s.get("uses", ""))
    assert download["with"]["pattern"] == "junit-${{ matrix.platform }}-*"
    save = next(s for s in steps if "cache/save" in s.get("uses", ""))
    assert save["with"]["path"] == "core/tests/durations-${{ matrix.platform }}.json"
    assert "v2-${{ matrix.platform }}-" in save["with"]["key"]
    restores = [s["with"]["path"] for s in jobs["changes"]["steps"] if "cache/restore" in s.get("uses", "")]
    assert restores == ["core/tests/durations-linux.json", "core/tests/durations-windows.json"]


def test_reused_main_push_feeds_the_proof_runs_timings_into_mains_cache():
    """The normal merge reuses the PR proof and runs no Core shards; main's cache (the only one every PR can restore)
    must still learn the PR run's timings, else every PR plans from the committed seed and new files stay unmeasured."""
    jobs = _wf("jarves-checks.yml")["jobs"]
    assert jobs["changes"]["outputs"]["proof_run"] == "${{ steps.c.outputs.proof_run }}"
    script = next(s["run"] for s in jobs["changes"]["steps"] if s.get("id") == "c")
    assert "proof_run=$(sed -n 's/^run_id=//p' reuse.out" in script and 'echo "proof_run=$proof_run"' in script
    refresh = jobs["durations"]
    cond = " ".join(refresh["if"].split())
    assert "needs.changes.outputs.reused == 'true' && needs.changes.outputs.proof_run != ''" in cond
    assert "github.ref == 'refs/heads/main'" in cond
    assert refresh["continue-on-error"] is True                      # timing data never turns a run red
    assert refresh["permissions"] == {"contents": "read", "actions": "read"}
    downloads = [s for s in refresh["steps"] if "download-artifact" in s.get("uses", "")]
    assert [s["if"] for s in downloads] == ["needs.changes.outputs.reused != 'true'", "needs.changes.outputs.reused == 'true'"]
    proof = downloads[1]["with"]
    assert proof["run-id"] == "${{ needs.changes.outputs.proof_run }}" and proof["github-token"] == "${{ github.token }}"
    assert proof["pattern"] == downloads[0]["with"]["pattern"] == "junit-${{ matrix.platform }}-*"
    assert "durations" not in jobs["ci-ok"]["needs"]


def test_plan_names_unmeasured_files_and_the_summary_shows_them(tmp_path, capsys):
    d = _tests_dir(tmp_path, {"test_a.py": 2, "test_b.py": 1, "test_new.py": 3})
    p = ci_plan.plan(d, {"test_a.py": 10, "test_b.py": 5}, windows=2, linux=2, linux_durations={"test_a.py": 4})
    assert p["unmeasured"] == {"windows": ["test_new.py"], "linux": ["test_b.py", "test_new.py"]}
    line = ci_plan.summary(p)
    assert line.startswith("shard plan: windows shards expected ") and "1 unmeasured file(s) (test_new.py)" in line
    assert "linux shards expected" in line and "2 unmeasured file(s) (test_b.py, test_new.py)" in line
    assert ci_plan.main(["summary", "--plan-line", "plan=" + json.dumps(p)]) == 0
    assert capsys.readouterr().out.strip() == line
    assert ci_plan.main(["summary", "--plan-line", ""]) == 0      # no plan line: an empty summary, never an error
