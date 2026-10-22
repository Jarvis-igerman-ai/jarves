"""SW-V4 M: the CI pipeline keeps its shape. Every job has a timeout, superseded runs are cancelled, docs-only changes skip
the heavy jobs, the performance gate and nightly workflow exist with the right layers, diagnostics are uploaded redacted,
downloads are cached. Offline: parses the workflow files and runs the small CI helper tools."""
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WF = ROOT / ".github" / "workflows"
sys.path.insert(0, str(ROOT / "core" / "tools"))


def load(name):
    return yaml.safe_load((WF / name).read_text(encoding="utf-8"))


def triggers(doc):
    return doc.get("on") or doc.get(True)


def steps_text(job):
    return "\n".join(f"{s.get('uses', '')} {s.get('run', '')} {s.get('name', '')}" for s in job.get("steps", []))


def test_every_job_in_every_workflow_has_a_timeout():
    for f in sorted(WF.glob("*.yml")):
        for jid, job in load(f.name)["jobs"].items():
            if "uses" in job:
                continue
            t = job.get("timeout-minutes")
            assert isinstance(t, int) and 1 <= t <= 180, f"{f.name}:{jid} needs a sane timeout-minutes"


def test_checks_cancel_superseded_runs_and_keep_the_core_timeout():
    doc = load("jarves-checks.yml")
    assert doc["concurrency"]["cancel-in-progress"] is True
    assert doc["jobs"]["core"]["timeout-minutes"] == 40
    t = triggers(doc)
    assert "pull_request" in t and "main" in t["push"]["branches"] and t["push"]["tags"]


def test_docs_only_changes_skip_the_heavy_jobs():
    doc = load("jarves-checks.yml")
    jobs = doc["jobs"]
    assert "changes" in jobs and "ci_plan.py classify" in steps_text(jobs["changes"])     # rules: core/tools/ci_plan.py
    heavy = {"core": "code", "browser": "full", "e2e": "e2e", "installer": "installer", "perf": "perf", "cad": "cad",
             "blender": "blender", "godot": "godot", "whatsapp-bridge": "whatsapp"}
    for jid, out in heavy.items():
        job = jobs[jid]
        assert "changes" in (job["needs"] if isinstance(job["needs"], list) else [job["needs"]]), jid
        assert f"needs.changes.outputs.{out} == 'true'" in job["if"], jid
    # main pushes, tags and manual runs always run everything
    script = jobs["changes"]["steps"][-1]["run"]
    assert '!= "pull_request"' in script and "full matrix" in script


def test_performance_gate_job_runs_the_gate_on_windows():
    job = load("jarves-checks.yml")["jobs"]["perf"]
    assert job["runs-on"] == "windows-2025" and "perf_gate.py --class ci" in steps_text(job)
    assert any(s.get("with", {}).get("name") == "perf-results" for s in job["steps"] if "upload-artifact" in s.get("uses", ""))


def test_downloads_are_cached():
    jobs = load("jarves-checks.yml")["jobs"]
    for jid in ("core", "installer", "cad", "blender", "godot", "e2e"):
        assert "actions/cache@" in steps_text(jobs[jid]), jid
    for jid in ("core", "e2e", "perf"):
        assert "setup-uv" in steps_text(jobs[jid]) and "enable-cache" in str(jobs[jid]), jid


def test_failures_upload_redacted_diagnostics_never_raw_logs():
    jobs = load("jarves-checks.yml")["jobs"]
    for jid in ("core", "e2e"):
        txt = steps_text(jobs[jid])
        assert "ci_diagnostics.py" in txt and "upload-artifact" in txt, jid
        for s in jobs[jid]["steps"]:
            if "upload-artifact" in s.get("uses", "") and "diagnostics" in s["with"]["name"]:
                assert s["if"] == "failure()" and s["with"]["path"].endswith("diag")


def test_core_suite_xdist_is_file_isolated_flake_retry_is_per_file_and_zero_skips_is_enforced():
    # 4.0.1 policy (supersedes "serial on purpose"): xdist only with --dist loadfile and never "-n auto"; the only retry
    # is ci_flake.py re-running the FAILED FILES of known transient errors (no pytest-rerunfailures, no whole-job retry).
    runs = "\n".join(s.get("run", "") for j in load("jarves-checks.yml")["jobs"].values() for s in j.get("steps", []))
    assert "-n auto" not in runs and "--dist loadfile" in runs and "--reruns" not in runs
    assert "ci_flake.py failed-files" in runs and "tests were skipped" in runs
    nightly = "\n".join(s.get("run", "") for j in load("nightly.yml")["jobs"].values() for s in j.get("steps", []))
    assert "-n auto" not in nightly and "--reruns" not in nightly and "tests were skipped" in nightly
    assert "xdist" not in (ROOT / "core" / "pyproject.toml").read_text(encoding="utf-8")   # pinned via `uv run --with`


def test_nightly_workflow_shape():
    doc = load("nightly.yml")
    t = triggers(doc)
    assert t["schedule"][0]["cron"] == "0 2 * * *" and "workflow_dispatch" in t
    jobs = doc["jobs"]
    assert {"suite", "flake", "perf", "audits"} <= set(jobs)
    assert jobs["suite"]["strategy"]["matrix"]["os"] == ["ubuntu-24.04", "windows-2025"]
    assert jobs["suite"]["timeout-minutes"] == 120
    assert "perf_gate.py --class ci" in steps_text(jobs["perf"])
    assert "synthetic_canaries.py" in steps_text(jobs["audits"]) and "test_privacy.py" in steps_text(jobs["audits"])
    assert steps_text(jobs["flake"]).count("pytest") >= 1 and "1 2 3" in str(jobs["flake"])
    assert "ci_diagnostics.py" in steps_text(jobs["suite"])
    assert doc["permissions"] == {"contents": "read"}


def test_synthetic_canary_runner():
    import synthetic_canaries as sc
    empty = sc.run({})
    assert empty["registered"] == 0 and "package O" in empty["note"]
    rep = sc.run({"ok": lambda: None, "bad": lambda: (_ for _ in ()).throw(ValueError("x"))})
    assert (rep["passed"], rep["failed"]) == (1, 1) and rep["results"][1]["error"] == "ValueError"
    # package O registers its journeys; a journey that cannot run here is BLOCKED (reported, not failed)
    rep = sc.run({"blocked": lambda: (_ for _ in ()).throw(sc.CanaryBlocked("no browser"))})
    assert (rep["passed"], rep["failed"], rep["blocked"]) == (0, 0, 1) and rep["results"][0]["reason"] == "no browser"
    sc.register_package_o()
    assert {"voice_stack", "music", "calendar_read", "dashboard_load", "installer_check", "scheduler"} <= set(sc.CANARIES)


def test_ci_minutes_summary():
    import ci_minutes as cm
    runs = {1: {"name": "jarves-checks"}, 2: {"name": "jarves-nightly"}}
    jobs = [{"run_id": 1, "labels": ["windows-2025"], "started_at": "2026-10-20T10:00:00Z",
             "completed_at": "2026-10-20T10:30:00Z", "conclusion": "success"},
            {"run_id": 1, "labels": ["ubuntu-24.04"], "started_at": "2026-10-20T10:00:00Z",
             "completed_at": "2026-10-20T10:10:00Z", "conclusion": "success"},
            {"run_id": 2, "labels": ["ubuntu-24.04"], "started_at": "2026-10-20T10:00:00Z",
             "completed_at": "2026-10-20T10:00:00Z", "conclusion": "skipped"}]
    s = cm.summarize(jobs, runs)
    assert s == {("jarves-checks", "windows"): 30.0, ("jarves-checks", "linux"): 10.0}
    assert "| **total** | | 40.0 |" in cm.table(s)


# ---------------------------------------------------------------------- 4.0.1: faster CI + continuous release
def test_windows_core_tests_run_in_three_shards_and_browser_tests_are_their_own_job():
    jobs = load("jarves-checks.yml")["jobs"]
    inc = jobs["core"]["strategy"]["matrix"]["include"]
    assert sorted(i["shard"] for i in inc if i["os"] == "windows-2025") == ["1/3", "2/3", "3/3"]
    run = "\n".join(s.get("run", "") for s in jobs["core"]["steps"])
    assert "--browser exclude --shard-files" in run and "--shard ${{ matrix.shard }}" in run and "skipped" in run
    b = jobs["browser"]
    assert b["needs"] == "changes" and "retry" in steps_text(b) and "--browser only" in steps_text(b)
    assert "skipped" in steps_text(b)


def test_shards_cover_every_test_file_exactly_once():
    sys.path.insert(0, str(ROOT / "core" / "tests"))
    import conftest
    counts = {f"f{i}.py": (i * 7) % 23 + 1 for i in range(60)}
    parts = conftest.shard_files(counts, 3)
    flat = [f for p in parts for f in p]
    assert sorted(flat) == sorted(counts)
    loads = [sum(counts[f] for f in p) for p in parts]
    assert max(loads) - min(loads) <= max(counts.values())


def test_caches_and_cancellation():
    doc = load("jarves-checks.yml")
    text = (WF / "jarves-checks.yml").read_text(encoding="utf-8")
    assert doc["concurrency"]["cancel-in-progress"] is True
    assert "cache-dependency-glob: core/uv.lock" in text and "cache-dependency-path: bridge/whatsapp/go.sum" in text


def test_every_green_main_makes_a_candidate_and_beta_is_disabled_by_default():
    doc = load("package.yml")
    assert triggers(doc)["workflow_run"]["branches"] == ["main"]
    text = (WF / "package.yml").read_text(encoding="utf-8")
    assert "release.py candidate" in text and "public-publish" not in text and "gh release create" not in text
    beta = doc["jobs"]["beta"]
    assert beta["if"].strip() == "${{ vars.ENABLE_BETA_PUBLISH == 'true' }}"       # unset variable = off
    assert "beta-publish" in steps_text(beta)


def test_stable_release_is_tag_driven_and_gated():
    doc = load("release.yml")
    assert triggers(doc)["push"]["tags"] == ["v[0-9]+.[0-9]+.[0-9]+"]
    text = steps_text(doc["jobs"]["stable"])
    assert text.index("release_gate.py") < text.index("public-publish")


def test_security_files_exist():
    assert (ROOT / "SECURITY.md").is_file()
    dep = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    assert {u["package-ecosystem"] for u in dep["updates"]} == {"pip", "gomod", "github-actions"}
    assert all(u["schedule"]["interval"] == "weekly" for u in dep["updates"])
    cq = (WF / "codeql.yml").read_text(encoding="utf-8")
    assert "languages: python" in cq
    ss = yaml.safe_load((ROOT / ".github" / "secret_scanning.yml").read_text(encoding="utf-8"))
    assert set(ss["paths-ignore"]) == {"core/tools/redaction_audit.py", "core/tools/campaign_stages_v4.py"}
    assert all((ROOT / p).is_file() for p in ss["paths-ignore"])
