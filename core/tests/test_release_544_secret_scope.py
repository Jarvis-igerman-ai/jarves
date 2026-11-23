"""Issue #544: a publish step must independently require a real tag ref."""
from pathlib import Path

import yaml

PUBLISH_IF = "${{ github.ref_type == 'tag' && env.DRY_RUN != 'true' }}"


def test_release_secret_step_requires_tag_ref():
    workflow = Path(__file__).resolve().parents[2] / ".github/workflows/release.yml"
    doc = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    assert "secrets." not in str(doc.get("env", {}))
    # every job: no secrets at job level, and exactly one step in the whole workflow reads secrets
    secret_steps = []
    for name, job in doc["jobs"].items():
        assert "secrets." not in str({k: v for k, v in job.items() if k != "steps"}), name
        secret_steps += [step for step in job.get("steps", []) if "secrets." in str(step)]
    assert len(secret_steps) == 1
    step = secret_steps[0]
    # exact match: a widened condition such as "... || true" must fail
    assert step.get("if") == PUBLISH_IF, (
        "#544: the secret-bearing step must require a tag independently of DRY_RUN"
    )
    # both release secrets live only in this step's env
    assert step["env"]["RELEASES_TOKEN"] == "${{ secrets.RELEASES_TOKEN }}"
    assert step["env"]["JARVES_UPDATE_SIGNING_KEY"] == "${{ secrets.JARVES_UPDATE_SIGNING_KEY }}"
