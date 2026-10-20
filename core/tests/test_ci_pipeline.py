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
    assert "changes" in jobs and "docs/" in steps_text(jobs["changes"])
    heavy = {"core": "code", "e2e": "code", "installer": "installer", "perf": "perf", "cad": "cad", "blender": "blender",
             "godot": "godot"}
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


def test_core_suite_stays_serial_and_zero_skips_is_enforced():
    for name in ("jarves-checks.yml", "nightly.yml"):
        doc = load(name)
        runs = "\n".join(s.get("run", "") for j in doc["jobs"].values() for s in j.get("steps", []))
        assert "-n auto" not in runs and "--dist" not in runs and "xdist" not in runs   # serial on purpose (see the M report)
        assert "--reruns" not in runs                                                   # a flake is a bug, never retried
        assert "tests were skipped" in runs
    assert "xdist" not in (ROOT / "core" / "pyproject.toml").read_text(encoding="utf-8")


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
