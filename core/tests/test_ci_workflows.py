"""Every GitHub Actions workflow must be valid YAML with the basic shape GitHub requires.

A single unquoted `name: Foo (Linux: bar)` makes GitHub reject the WHOLE workflow file (it never runs, and the
failure is only visible on github.com). This catches that locally, offline.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
FILES = sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])


def test_workflows_exist():
    assert FILES, f"no workflow files found under {WORKFLOWS}"


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_workflow_is_valid_yaml(path: Path):
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(doc, dict), f"{path.name}: top level must be a mapping"
    # PyYAML (YAML 1.1) reads the key `on` as boolean True.
    assert "on" in doc or True in doc, f"{path.name}: missing 'on' trigger"
    jobs = doc.get("jobs")
    assert isinstance(jobs, dict) and jobs, f"{path.name}: 'jobs' must be a non-empty mapping"
    for job_id, job in jobs.items():
        assert isinstance(job, dict), f"{path.name}: job {job_id!r} must be a mapping"
        assert "runs-on" in job or "uses" in job, f"{path.name}: job {job_id!r} needs runs-on or uses"
        for i, step in enumerate(job.get("steps") or []):
            assert isinstance(step, dict), f"{path.name}: {job_id} step {i} must be a mapping"
            assert "run" in step or "uses" in step, f"{path.name}: {job_id} step {i} needs run or uses"
            if "name" in step:
                assert isinstance(step["name"], str), f"{path.name}: {job_id} step {i} name must be a string"


def _release():
    doc = yaml.safe_load((WORKFLOWS / "release.yml").read_text(encoding="utf-8"))
    return doc, doc["jobs"]["stable"]


def test_release_dispatch_checks_out_triggering_ref_not_input_tag():
    doc, job = _release()
    checkout = next(s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout"))
    assert checkout["with"]["ref"] == "${{ github.ref }}"
    assert checkout["with"]["fetch_depth" if "fetch_depth" in checkout["with"] else "fetch-depth"] == 0
    assert "inputs.tag" not in str(checkout["with"])


def test_release_publish_guarded_and_dry_run_default_true():
    doc, job = _release()
    on = doc.get("on", doc.get(True))
    assert on["workflow_dispatch"]["inputs"]["dry_run"]["default"] is True
    assert on["push"]["tags"] == ["v[0-9]+.[0-9]+.[0-9]+"]
    assert "pull_request_target" not in on
    assert doc["permissions"] == {} and job["permissions"] == {"contents": "read", "actions": "read"}
    # DRY_RUN is forced true on any dispatch that is not a real tag ref with dry_run unticked
    assert "workflow_dispatch" in job["env"]["DRY_RUN"] and "ref_type" in job["env"]["DRY_RUN"]
    publish = [s for s in job["steps"] if "publish" in str(s.get("run", "")) and "--dry-run" not in str(s.get("run", ""))]
    assert publish, "publish step missing"
    for s in publish:
        assert "DRY_RUN != 'true'" in s["if"]
    # secrets only in the publish step, never in the dry-run path or job env
    assert "secrets." not in str(job["env"]).replace("github.token", "")
    for s in job["steps"]:
        if s not in publish:
            assert "secrets." not in str(s)
    # the gate gets the tag and the checked-out commit; no tag is ever created or pushed
    gate = next(s for s in job["steps"] if "release_gate.py" in str(s.get("run", "")))
    assert '--tag "$TAG"' in gate["run"] and 'git rev-parse HEAD' in gate["run"]
    text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    assert "git tag" not in text and "git push" not in text


def test_release_tag_comes_from_the_ref_on_a_tag_and_a_differing_input_fails():
    """#598: on a tag-ref dispatch the free-text input cannot name another version than the commit being released."""
    doc, job = _release()
    assert "github.ref_type == 'tag' && github.ref_name" in job["env"]["TAG"]
    find = next(s for s in job["steps"] if s.get("id") == "find")
    assert '[ "$INPUT_TAG" != "$REF_NAME" ]' in find["run"] and 'REF_TYPE" = "tag"' in find["run"]
    assert job["env"]["INPUT_TAG"] == "${{ github.event.inputs.tag }}"


def test_automerge_runs_risk_class_before_enqueue():
    """#906: automerge.yml must classify changed files and label Tier R PRs no-automerge before `gh pr merge`."""
    text = (WORKFLOWS / "automerge.yml").read_text(encoding="utf-8")
    assert "core/tools/risk_class.py" in text
    assert "^none: high-risk path" in text
    assert "--add-label no-automerge" in text
    doc = yaml.safe_load(text)
    steps = doc["jobs"]["enqueue"]["steps"]
    run = steps[-1]["run"]
    assert run.index("risk_class.py --files-from") < run.index("gh pr merge")
    assert any("checkout" in str(s.get("uses", "")) and "ref" not in s.get("with", {}) for s in steps)
    co = steps[0]
    assert co["with"]["persist-credentials"] is False
    assert re.search(r"@[0-9a-f]{40}$", co["uses"].split()[0])


def test_automerge_risk_guard_fails_closed():
    """#906 review blocker 1: queue only on a well-formed verdict and a non-empty file list; no `|| true` pass-through."""
    run = yaml.safe_load((WORKFLOWS / "automerge.yml").read_text(encoding="utf-8"))["jobs"]["enqueue"]["steps"][-1]["run"]
    assert "risk_class.py --files-from \"files-$n.txt\" --mode all 2>&1 || true" not in run
    assert "|| true)" not in run
    assert '[ -s "files-$n.txt" ]' in run
    assert "grep -qE '^(automerge [a-z]+|none): '" in run
    assert "risk check failed" in run
    assert run.index("risk check failed") < run.index("gh pr merge \"$n\" -R \"$REPO\" --auto")


def test_automerge_tier_r_disables_existing_auto_merge():
    """#906 review blocker 2: a Tier R PR that already has auto-merge on must have it turned off."""
    run = yaml.safe_load((WORKFLOWS / "automerge.yml").read_text(encoding="utf-8"))["jobs"]["enqueue"]["steps"][-1]["run"]
    assert "--disable-auto" in run
    assert ".autoMergeRequest != null" in run[: run.index("--disable-auto")]
    assert run.index("--add-label no-automerge") < run.index("--disable-auto") < run.index("--auto --merge")


def test_risk_class_flags_release_and_bootstrap_paths(tmp_path):
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[2]
    f = tmp_path / "files.txt"
    f.write_text(".github/workflows/release.yml\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(root / "core/tools/risk_class.py"), "--files-from", str(f), "--mode", "all"],
                       capture_output=True, text=True)
    assert "high-risk path" in r.stdout + r.stderr


def test_automerge_enqueue_survives_risk_class_exit_1():
    """GitHub runs `bash -e`; risk_class.py exits 1 for Tier N, so the script must not die on it silently."""
    run = yaml.safe_load((WORKFLOWS / "automerge.yml").read_text(encoding="utf-8"))["jobs"]["enqueue"]["steps"][-1]["run"]
    assert "set +e" in run
    assert re.search(r"risk_class\.py[^\n]*2>&1\) \|\| true", run)


def test_release_secrets_step_runs_only_on_a_tag_ref_after_a_fail_closed_guard():
    """#544: the only step reading RELEASES_TOKEN / JARVES_UPDATE_SIGNING_KEY needs a real tag ref (a branch named
    like v4.5.0 is refused), and a guard right before it fails a real publish attempted from any other ref."""
    import os
    import shutil
    import subprocess
    doc, job = _release()
    steps = job["steps"]
    idx = [i for i, s in enumerate(steps) if "secrets." in str(s)]
    assert len(idx) == 1 and "secrets." not in str(doc.get("env", {})) + str(job.get("env", {}))
    publish, guard = steps[idx[0]], steps[idx[0] - 1]
    for part in ("env.DRY_RUN != 'true'", "github.ref_type == 'tag'", "startsWith(github.ref, 'refs/tags/v')"):
        assert part in publish["if"], part
    assert guard.get("id") == "publish_guard" and "env.DRY_RUN != 'true'" in guard["if"] and "if" in guard
    assert "secrets." not in str(guard) and job["env"]["REF_TYPE"] == "${{ github.ref_type }}"
    bash = shutil.which("bash")
    if os.name == "nt" or not bash:
        return  # the structure above is checked everywhere; the script itself runs on the Linux runner
    def run(**env):
        full = {"PATH": os.environ.get("PATH", ""), "TAG": "v4.5.0", **env}
        return subprocess.run([bash, "-e", "-c", guard["run"]], env=full, capture_output=True, text=True, timeout=30)
    ok = run(REF_TYPE="tag", GITHUB_REF="refs/tags/v4.5.0")
    assert ok.returncode == 0, ok.stdout + ok.stderr
    for bad in ({"REF_TYPE": "branch", "GITHUB_REF": "refs/heads/v4.5.0"}, {"REF_TYPE": "tag", "GITHUB_REF": "refs/tags/v4.5.1"},
                {"REF_TYPE": "", "GITHUB_REF": ""}):
        r = run(**bad)
        assert r.returncode != 0 and "::error::" in r.stdout, bad
