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
    assert "pull_request" in str(doc["concurrency"]["cancel-in-progress"])   # PRs cancel; main runs queue
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
    assert sorted(i["shard"] for i in inc if i["os"] == "ubuntu-24.04") == ["1/3", "2/3", "3/3"]
    assert sorted(i["shard"] for i in inc if i["os"] == "windows-2025") == ["1/4", "2/4", "3/4", "4/4"]   # S3, #160
    run = "\n".join(s.get("run", "") for s in jobs["core"]["steps"])
    assert "--browser exclude --shard-files" in run and "--shard ${{ matrix.shard }}" in run and "skipped" in run
    b = jobs["browser"]
    assert set(b["needs"]) == {"changes", "validate"} and "retry" in steps_text(b) and "--browser only" in steps_text(b)
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
    assert "pull_request" in str(doc["concurrency"]["cancel-in-progress"])   # PRs cancel; main runs queue
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


def test_fast_validation_blocks_expensive_jobs_and_reports_to_ci_ok():
    jobs = load("jarves-checks.yml")["jobs"]
    validation = steps_text(jobs["validate"])
    assert "tools/prepush.py" in validation               # the quick gate IS the local pre-push command
    sys.path.insert(0, str(ROOT / "core" / "tools"))
    import prepush
    gated = {Path(t).name for c in prepush.CHECKS for t in c.tests}
    for file in ("test_ci_workflows.py", "test_ci_pipeline.py", "test_ci_speed_tools.py", "test_docs_links.py",
                 "test_p_perf.py", "test_guides.py", "test_cli_parity.py", "test_hebrew_texts.py", "test_netguard.py"):
        assert file in gated, file
    assert "--all-extras" not in validation
    for jid in ("core", "browser", "installer", "whatsapp-bridge", "perf", "cad", "blender", "godot", "e2e"):
        assert "validate" in jobs[jid]["needs"], jid
    assert "validate" in jobs["ci-ok"]["needs"]


def test_bridge_publish_workflow_is_manual_least_privilege_and_scopes_secrets_to_one_step():
    """The manual WhatsApp bridge publish workflow: dispatch only, dry run by default, build job has no secrets,
    the signing/upload secrets exist only on the single real-publish step, which runs only from main."""
    import re
    path = WF / "bridge-publish.yml"
    doc = load("bridge-publish.yml")
    t = triggers(doc)
    assert set(t) == {"workflow_dispatch"}                                  # never push/PR/schedule
    inputs = t["workflow_dispatch"]["inputs"]
    assert inputs["dry_run"]["default"] is True and inputs["dry_run"]["type"] == "boolean"
    assert inputs["version"]["required"] is False
    assert doc["permissions"] == {} and doc["concurrency"]["group"] == "bridge-publish"
    build, publish = doc["jobs"]["build"], doc["jobs"]["publish"]
    assert build["runs-on"] == "windows-2025" and publish["runs-on"] == "ubuntu-24.04"
    assert publish["needs"] == "build"
    assert build["permissions"] == {"contents": "read"} and publish["permissions"] == {"contents": "read"}
    for job in (build, publish):
        assert 1 <= job["timeout-minutes"] <= 30
        for step in job["steps"]:
            if "uses" in step:
                assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", step["uses"]), step["uses"]   # full-SHA pins
    # same reproducible build as the whatsapp-bridge CI job: pinned Go, -Verify, compared with the pin
    btxt = steps_text(build)
    assert "1.27.0" in str(build["steps"])
    assert "-Verify bridge/whatsapp/dist/whatsapp_bridge.exe.sha256" in btxt and "bridge_pin.json" in btxt
    # secrets: nowhere except the one real-publish step
    raw = path.read_text(encoding="utf-8")
    assert raw.count("secrets.") == 2 and "set -x" not in raw
    holders = [(j, s) for j, job in doc["jobs"].items() for s in job["steps"] if "secrets." in str(s)]
    assert len(holders) == 1 and holders[0][0] == "publish"
    step = holders[0][1]
    assert set(step["env"]) == {"RELEASES_REPO", "RELEASES_TOKEN", "JARVES_UPDATE_SIGNING_KEY"}
    assert "bridge-publish --dir bridge-dist" in step["run"] and "--dry-run" not in step["run"]
    assert "!inputs.dry_run" in step["if"] and "refs/heads/main" in step["if"]
    dry = next(s for s in publish["steps"] if "--dry-run" in s.get("run", ""))
    assert "env" not in dry and "inputs.dry_run" in dry["if"] and "!" not in dry["if"]


def test_autolabel_has_label_write_permission_and_never_masks_creation_failure():
    """Regression: PRs #112/#113/#115 had green CI but autolabel failed with a missing label."""
    doc = load("autolabel.yml")
    assert doc["permissions"]["issues"] == "write"
    assert doc["permissions"]["pull-requests"] == "write"
    assert doc["permissions"]["contents"] == "read"
    assert doc["jobs"]["label"]["name"] == "Auto-label policy"
    assert doc["jobs"]["label"]["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert "pull_request_target" in triggers(doc)
    step = steps_text(doc["jobs"]["label"])
    assert "gh label create" in step
    assert "gh api" in step and "labels/$l" in step
    assert "Cannot create label" in step and "exit 1" in step
    assert "2>/dev/null || true" not in step
    assert "gh pr edit" in step   # the original safe risk classifier / relabel flow remains


def test_codeql_prunes_its_own_overlay_caches_only():
    jobs = load("codeql.yml")["jobs"]
    prune = jobs["prune-caches"]
    assert prune["permissions"] == {"actions": "write"}
    run = steps_text(prune)
    assert "--key codeql-overlay-base-database-" in run and "--ref refs/heads/main" in run and "'.[2:][].id'" in run
    assert prune["if"] == "github.event_name != 'pull_request'"


def test_both_required_checks_report_in_the_merge_queue():
    """#161: with a ruleset that requires the merge queue, BOTH required checks must run on `merge_group`, or the queue
    waits until its timeout. jarves-checks runs the full matrix there (event != pull_request) and builds the installer as
    the main channel, so the push run on main reuses it by tree hash. Autolabel reports a pass under the same name."""
    checks = load("jarves-checks.yml")
    assert checks[True]["merge_group"] == {"types": ["checks_requested"]}
    assert "github.event_name == 'merge_group'" in checks["jobs"]["installer"]["env"]["CHANNEL"]
    sys.path.insert(0, str(ROOT / "core" / "tools"))
    import ci_plan
    assert not any(ci_plan.classify(["docs/x.md"], "merge_group").values())       # docs-only group: no heavy job
    assert all(ci_plan.classify([".github/workflows/jarves-checks.yml"], "merge_group").values())   # CI change: everything
    assert ci_plan.classify(["core/src/jarves/cli.py"], "merge_group")["full"]          # code: the full matrix
    auto = load("autolabel.yml")
    assert auto[True]["merge_group"] == {"types": ["checks_requested"]}
    names = {j["name"]: j for j in auto["jobs"].values()}
    assert list(names) == ["Auto-label policy"] and len(auto["jobs"]) == 2
    assert auto["jobs"]["queue-pass"]["if"] == "github.event_name == 'merge_group'"
    assert "github.event_name == 'pull_request_target'" in auto["jobs"]["label"]["if"]
    assert "|| github.ref" in auto["concurrency"]["group"]       # merge groups must not cancel each other's check


def test_merge_queue_cancels_only_superseded_runs_of_the_same_pr():
    """#160: a rebuilt merge group left the old merge_group run of the same PR running (two full matrices per PR).
    The supersede job cancels only OLDER merge_group runs whose temp branch has the same pr-<N>- prefix."""
    job = load("jarves-checks.yml")["jobs"]["supersede"]
    assert job["if"] == "github.event_name == 'merge_group'" and job["permissions"] == {"actions": "write"}
    run = steps_text(job)
    assert "event=merge_group" in run and ".id < $GITHUB_RUN_ID" in run and 'startswith(\\"$prefix\\")' in run
    assert "gh-readonly-queue/*/pr-*-" in run and "gh run cancel" in run
    assert not any("checkout" in s.get("uses", "") for s in job["steps"])
    assert "supersede" not in load("jarves-checks.yml")["jobs"]["ci-ok"]["needs"]   # never part of the required check


def test_merge_group_reuses_the_green_pr_proof_for_the_same_tree():
    """Lean process item 1: a queue group whose docs-free tree equals a green PR run's tree runs no heavy job."""
    script = next(s["run"] for s in load("jarves-checks.yml")["jobs"]["changes"]["steps"] if s.get("id") == "c")
    block = script[script.index('"$GITHUB_EVENT_NAME" = "merge_group"'):script.index('args=(--event')]
    assert "ci_reuse.py resolve " in block and "ci_reuse.py resolve-tests" in block and "reused=true" in block
    sys.path.insert(0, str(ROOT / "core" / "tools"))
    import ci_plan
    assert not any(ci_plan.classify(["core/src/jarves/cli.py"], "merge_group", reused=True).values())
    assert ci_plan.classify(["core/src/jarves/cli.py"], "merge_group")["full"]            # no proof: the full matrix
