"""Issue #544: a publish step must independently require a real tag ref."""
from pathlib import Path

import yaml


def test_release_secret_step_requires_tag_ref():
    workflow = Path(__file__).resolve().parents[2] / ".github/workflows/release.yml"
    doc = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    job = doc["jobs"]["stable"]
    assert "secrets." not in str(doc.get("env", {}))
    assert "secrets." not in str(job.get("env", {}))
    secret_steps = [step for step in job["steps"] if "secrets." in str(step)]
    assert len(secret_steps) == 1
    condition = secret_steps[0].get("if", "")
    assert "github.ref_type == 'tag'" in condition, (
        "#544: the secret-bearing step must require a tag independently of DRY_RUN"
    )
    assert "env.DRY_RUN != 'true'" in condition
