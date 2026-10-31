"""Every GitHub Actions workflow must be valid YAML with the basic shape GitHub requires.

A single unquoted `name: Foo (Linux: bar)` makes GitHub reject the WHOLE workflow file (it never runs, and the
failure is only visible on github.com). This catches that locally, offline.
"""
from __future__ import annotations

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
