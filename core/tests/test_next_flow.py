"""Contracts of the `next` integration-branch flow (.github/workflows/next-flow.yml, docs/NEXT_FLOW.md)."""
from pathlib import Path

import yaml

WF = Path(__file__).resolve().parents[2] / ".github" / "workflows"


def load(name):
    doc = yaml.safe_load((WF / name).read_text(encoding="utf-8"))
    doc["on"] = doc.get("on", doc.get(True))
    return doc


def test_batch_runs_on_next_queue_and_never_cancel():
    doc = load("jarves-checks.yml")
    assert "next" in doc["on"]["push"]["branches"] and "main" in doc["on"]["push"]["branches"]
    assert doc["on"]["pull_request"]["branches-ignore"] == ["next"]       # PRs into next get the light run instead
    assert "pull_request" in str(doc["concurrency"]["cancel-in-progress"])  # push runs on main/next queue


def test_light_run_targets_next_and_has_the_single_check_name():
    doc = load("next-flow.yml")
    assert doc["on"]["pull_request"]["branches"] == ["next"]
    assert doc["jobs"]["ci-ok"]["name"] == load("jarves-checks.yml")["jobs"]["ci-ok"]["name"]
    assert "pull_request" in str(doc["concurrency"]["cancel-in-progress"])
    text = (WF / "next-flow.yml").read_text(encoding="utf-8")
    assert "ci_affected.py" in text and "any skip" in text.lower() or "skipped" in text


def test_automations_are_off_unless_enable_next_flow():
    doc = load("next-flow.yml")
    for job in ("automerge-into-next", "fast-forward-main"):
        assert "vars.ENABLE_NEXT_FLOW == 'true'" in doc["jobs"][job]["if"], job
        assert doc["jobs"][job]["concurrency"]["cancel-in-progress"] is False
    ff = (WF / "next-flow.yml").read_text(encoding="utf-8")
    assert "AUTOMERGE_TOKEN" in ff and "--force" not in ff and "merge-base --is-ancestor" in ff


def test_default_pr_base_is_not_switched():
    # automerge.yml (the queue for main) still targets main
    assert "--base main" in (WF / "automerge.yml").read_text(encoding="utf-8")
