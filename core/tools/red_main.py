"""Fix-forward for a red main (owner decision 24 Oct 2026, #160): name the failing tests and the suspect PRs and hand
the fix to the suspect PR's agent. Auto-revert stays off.

    python core/tools/red_main.py report --repo O/R --run RUN_ID [--out body.md]
        Markdown body for the "main is red" issue: failing tests (from the failed jobs' logs), the merges since the
        last green main run (suspect PRs), and two machine-readable markers:
        <!-- red-main-tests: test_a.py,test_b.py -->   <!-- red-main-suspects: 12,13 -->

With the merge queue (#161) every merge group contains main's failure until it is fixed, so the fix PR (label
`main-fix`) is put at the front of the queue ("Jump to top" / `gh pr merge --auto` then jump) and merged first.
Standard library + gh; main-health.yml runs it from the default branch checkout, never from PR code.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from flake_scan import failed_tests  # noqa: E402

MERGE_PR = re.compile(r"^Merge pull request #(\d+)")


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True, encoding="utf-8").stdout


def failing_test_files(tests: dict[str, str]) -> list[str]:
    return sorted({t.split("::", 1)[0] for t in tests})


def suspects_from_messages(messages: list[str]) -> list[int]:
    out: list[int] = []
    for m in messages:
        hit = MERGE_PR.search(m.splitlines()[0] if m else "")
        if hit and int(hit.group(1)) not in out:
            out.append(int(hit.group(1)))
    return out


def render(run_url: str, sha: str, tests: dict[str, str], suspects: list[int]) -> str:
    rows = "\n".join(f"- `{t}`: `{line.replace('`', '')}`" for t, line in sorted(tests.items())) or \
        "- (no test name in the log: installer, perf gate or a hang; see the run)"
    sus = ", ".join(f"#{n}" for n in suspects) or "(none found: direct push?)"
    return (f"jarves-checks failed on `main` at {sha[:7]}, also after one automatic re-run of the failed jobs: {run_url}\n\n"
            f"**Failing tests**\n{rows}\n\n**Suspect PRs** (merged since the last green main run): {sus}\n\n"
            "Fix-forward (owner decision 24 Oct, #160): the agent of the suspect PR opens a fix PR labelled `main-fix`; "
            "it goes to the front of the merge queue (every merge group contains this failure until it is fixed). "
            "No auto-revert. This issue closes itself when `main` is green again.\n\n"
            f"<!-- red-main-tests: {','.join(failing_test_files(tests))} -->\n"
            f"<!-- red-main-suspects: {','.join(map(str, suspects))} -->\n")


def _report(repo: str, run_id: str) -> str:
    run = json.loads(gh("api", f"repos/{repo}/actions/runs/{run_id}"))
    sha = run["head_sha"]
    try:
        log = gh("run", "view", str(run_id), "-R", repo, "--log-failed")
    except subprocess.CalledProcessError:
        log = ""
    # the newest green main run that started BEFORE this red one: the merges in between are the suspects
    green = gh("api", f"repos/{repo}/actions/workflows/jarves-checks.yml/runs?branch=main&event=push&status=success"
                      f"&created=<{run['created_at']}&per_page=1", "--jq", ".workflow_runs[0].head_sha // empty").strip()
    messages: list[str] = []
    if green and green != sha:
        raw = gh("api", f"repos/{repo}/compare/{green}...{sha}",
                 "--jq", ".commits[]|select(.parents|length > 1)|.commit.message|@json").splitlines()
        messages = [json.loads(x) for x in raw if x.strip()]
    return render(run.get("html_url", ""), sha, failed_tests(log), suspects_from_messages(messages))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report")
    r.add_argument("--repo", required=True)
    r.add_argument("--run", required=True)
    r.add_argument("--out")
    a = ap.parse_args(argv)
    text = _report(a.repo, a.run)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
