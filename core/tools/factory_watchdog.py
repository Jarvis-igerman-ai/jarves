"""Factory watchdog: one pass, safe to schedule every ~20 min. Standard library + `gh` only.

    python core/tools/factory_watchdog.py [--dry-run | --apply] [--repo OWNER/NAME] [--state-file PATH]

Default is --dry-run (prints the actions, changes nothing, writes no files). --apply performs the two safe actions:
  * re-queue a Ready green PR that dropped from the merge queue:  gh pr merge N --auto   (skips PRs labelled no-automerge)
  * toggle the no-automerge label (remove + add) when the required "Auto-label policy" check is missing on a green PR
It only REPORTS: main red, PR red > 2 h, PR stuck BLOCKED, open P0-P2 above 5, no beta after the latest green main
commit for 60 min, the same PR re-queued 3 times.
It NEVER merges, closes, reopens, pushes, reruns CI, edits issues, or touches tags/settings: every `gh` call goes through
an allow-list (GhRunner.check) and anything else raises before it runs.
Exit 0 = nothing needs attention, 1 = needs attention.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_REPO = "Jarvis-igerman-ai/jarves"
RELEASES_REPO = "igalgerman/jarvis-releases"
DEFAULT_STATE = Path(r"C:\Users\97254\.claude\projects\C--work-jarvis\STATE.md")
NO_AUTOMERGE = "no-automerge"
POLICY_CHECK = "Auto-label policy"
RED_PR_HOURS = 2.0
BETA_GRACE_MIN = 60
P012_LIMIT = 5
REQUEUE_LIMIT = 3
GOOD = {"SUCCESS", "SKIPPED", "NEUTRAL"}
BAD = {"FAILURE", "TIMED_OUT", "CANCELLED", "STARTUP_FAILURE", "ACTION_REQUIRED", "ERROR"}
NOW_START, NOW_END = "<!-- WATCHDOG-NOW-START -->", "<!-- WATCHDOG-NOW-END -->"
METRIC_HEADER = "| date | open P0-P2 | PRs merged today | CI re-runs | hours merge->beta |\n|---|---|---|---|---|"


class NeverActError(RuntimeError):
    pass


class GhRunner:
    """Runs `gh`. Allow-list only: read-only list calls plus the two safe write actions."""

    READ = {("pr", "list"), ("run", "list"), ("issue", "list"), ("release", "list")}

    def __init__(self, dry_run: bool = True, exe: str = "gh"):
        self.dry_run = dry_run
        self.exe = exe
        self.writes: list[list[str]] = []

    @staticmethod
    def is_write(args: list) -> bool:
        a = [str(x) for x in args]
        if len(a) == 5 and a[:2] == ["pr", "merge"] and a[2].isdigit() and a[3] == "--auto" and a[4].startswith("--repo="):
            return True
        return (len(a) == 6 and a[:2] == ["pr", "edit"] and a[2].isdigit()
                and a[3] in ("--add-label", "--remove-label") and a[4] == NO_AUTOMERGE and a[5].startswith("--repo="))

    def check(self, args: list) -> bool:
        """Return True for an allowed write, False for an allowed read, raise otherwise."""
        a = [str(x) for x in args]
        if self.is_write(a):
            return True
        if tuple(a[:2]) in self.READ:
            return False
        raise NeverActError(f"gh call not on the allow-list: {' '.join(a)}")

    def run(self, args: list) -> str:
        is_write = self.check(args)
        if is_write:
            if self.dry_run:
                return ""
            self.writes.append([str(x) for x in args])
        proc = subprocess.run([self.exe, *map(str, args)], capture_output=True, text=True, encoding="utf-8", timeout=120)
        if proc.returncode != 0:
            raise RuntimeError(f"gh {' '.join(map(str, args))} failed: {proc.stderr.strip()[:300]}")
        return proc.stdout

    def json(self, args: list):
        out = self.run(args)
        return json.loads(out) if out.strip() else []


def parse_ts(s: str | None) -> dt.datetime | None:
    if not s or s.startswith("0001"):
        return None
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def classify_checks(rollup: list[dict]) -> tuple[str, list[str], dt.datetime | None]:
    """-> (state green|red|pending|none, check names, earliest failure time)."""
    names: list[str] = []
    red_at = None
    pending = bad = False
    for c in rollup or []:
        names.append(c.get("name") or c.get("context") or "")
        status = (c.get("status") or "COMPLETED").upper()
        concl = (c.get("conclusion") or c.get("state") or "").upper()
        if status != "COMPLETED" or concl in ("", "PENDING", "EXPECTED"):
            pending = True
        elif concl in BAD:
            bad = True
            t = parse_ts(c.get("completedAt"))
            if t and (red_at is None or t < red_at):
                red_at = t
        elif concl not in GOOD:
            pending = True
    if not names:
        return "none", names, None
    return ("red" if bad else "pending" if pending else "green"), names, red_at


def collect(gh: GhRunner, repo: str, today: dt.date) -> dict:
    d = today.isoformat()
    return {
        "prs": gh.json(["pr", "list", "--repo", repo, "--state", "open", "--limit", "100", "--json",
                        "number,title,isDraft,labels,mergeStateStatus,autoMergeRequest,statusCheckRollup,updatedAt"]),
        "main": (gh.json(["run", "list", "--repo", repo, "--branch", "main", "--limit", "1", "--json",
                          "conclusion,status,headSha,updatedAt,createdAt"]) or [None])[0],
        "issues": gh.json(["issue", "list", "--repo", repo, "--state", "open", "--limit", "500", "--json", "number,labels"]),
        "releases": gh.json(["release", "list", "--repo", RELEASES_REPO, "--limit", "20", "--json",
                             "tagName,isPrerelease,publishedAt"]),
        "merged": gh.json(["pr", "list", "--repo", repo, "--state", "merged", "--search", f"merged:>={d}",
                           "--limit", "100", "--json", "number,mergedAt"]),
        "reruns": gh.json(["run", "list", "--repo", repo, "--created", f">={d}", "--limit", "200", "--json", "attempt"]),
    }


def beta_of(rels: list[dict]) -> dict | None:
    betas = [r for r in rels if r.get("isPrerelease") or "beta" in (r.get("tagName") or "").lower()]
    betas.sort(key=lambda r: r.get("publishedAt") or "", reverse=True)
    return betas[0] if betas else None


def analyse(data: dict, now: dt.datetime, requeues: dict) -> dict:
    actions, alerts, queue, red_prs = [], [], [], []
    for p in data["prs"]:
        n = p["number"]
        state, names, red_at = classify_checks(p.get("statusCheckRollup") or [])
        lab = {x["name"] for x in p.get("labels", [])}
        if p.get("isDraft"):
            continue
        queued = bool(p.get("isInMergeQueue") or p.get("autoMergeRequest"))
        if queued:
            queue.append(n)
        if state == "red":
            red_prs.append(n)
            if red_at and (now - red_at).total_seconds() > RED_PR_HOURS * 3600:
                alerts.append(f"PR #{n} red for more than {RED_PR_HOURS:g} h")
        if p.get("mergeStateStatus") == "BLOCKED" and state != "red" and state != "pending":
            alerts.append(f"PR #{n} stuck BLOCKED")
        if state == "green" and POLICY_CHECK not in names:
            actions.append({"kind": "toggle-label", "pr": n, "had": NO_AUTOMERGE in lab,
                            "why": f"'{POLICY_CHECK}' check missing on a green PR"})
        elif state == "green" and NO_AUTOMERGE not in lab and not queued and p.get("mergeStateStatus") != "DIRTY":
            if requeues.get(str(n), 0) >= REQUEUE_LIMIT:
                alerts.append(f"PR #{n} re-queued {requeues[str(n)]} times, not re-queueing again")
            else:
                actions.append({"kind": "requeue", "pr": n, "why": "green, Ready, dropped from the queue"})
    main = data.get("main")
    concl = ((main or {}).get("conclusion") or "").lower()
    done = bool(main) and main.get("status") == "completed"
    if done and concl in ("failure", "timed_out"):
        alerts.append("main is red")
    counts = {f"P{i}": 0 for i in range(4)}
    for i in data["issues"]:
        for x in i.get("labels", []):
            if x["name"] in counts:
                counts[x["name"]] += 1
    p012 = counts["P0"] + counts["P1"] + counts["P2"]
    if p012 > P012_LIMIT:
        alerts.append(f"open P0-P2 is {p012} (limit {P012_LIMIT}): fix only bugs until <= 3")
    beta = beta_of(data["releases"])
    beta_t = parse_ts(beta.get("publishedAt")) if beta else None
    green_t = parse_ts(main.get("updatedAt")) if done and concl == "success" else None
    if green_t and (beta_t is None or beta_t < green_t) and (now - green_t).total_seconds() > BETA_GRACE_MIN * 60:
        alerts.append(f"no beta newer than the green main commit after {BETA_GRACE_MIN} min")
    hours_beta = None
    if green_t and beta_t and beta_t >= green_t:
        hours_beta = round((beta_t - green_t).total_seconds() / 3600, 1)
    return {"actions": actions, "alerts": alerts, "queue": queue, "red_prs": red_prs, "counts": counts, "p012": p012,
            "merged_today": len(data["merged"]), "reruns": sum(1 for r in data["reruns"] if (r.get("attempt") or 1) > 1),
            "beta": beta, "beta_age_h": round((now - beta_t).total_seconds() / 3600, 1) if beta_t else None,
            "hours_beta": hours_beta}


def load_sidecar(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def execute(gh: GhRunner, repo: str, actions: list[dict], sidecar: dict) -> list[str]:
    log = []
    rq = sidecar.setdefault("requeues", {})
    for a in actions:
        n = a["pr"]
        if a["kind"] == "requeue":
            log.append(f"re-queue #{n}")
            gh.run(["pr", "merge", n, "--auto", f"--repo={repo}"])
            if not gh.dry_run:
                rq[str(n)] = rq.get(str(n), 0) + 1
        else:
            log.append(f"toggle {NO_AUTOMERGE} on #{n} ({a['why']})")
            flags = ["--remove-label", "--add-label"] if a["had"] else ["--add-label", "--remove-label"]
            for flag in flags:
                gh.run(["pr", "edit", n, flag, NO_AUTOMERGE, f"--repo={repo}"])
    return log


def write_state(state_file: Path, res: dict, log: list[str], now: dt.datetime) -> None:
    today = now.date().isoformat()
    ids = lambda xs: ", ".join(f"#{n}" for n in xs) or "none"  # noqa: E731
    block = "\n".join([
        NOW_START, f"## Watchdog now ({now.strftime('%Y-%m-%d %H:%M')} UTC)",
        f"- queue: {ids(res['queue'])}", f"- red PRs: {ids(res['red_prs'])}",
        "- open P0-P2: {} (P0 {P0}, P1 {P1}, P2 {P2}, P3 {P3})".format(res["p012"], **res["counts"]),
        f"- newest beta: {(res['beta'] or {}).get('tagName', 'none')} ({res['beta_age_h']} h old)",
        f"- actions: {'; '.join(log) or 'none'}", f"- alerts: {'; '.join(res['alerts']) or 'none'}", NOW_END, ""])
    try:
        text = state_file.read_text(encoding="utf-8")
    except OSError:
        text = ""
    text = re.sub(re.escape(NOW_START) + r".*?" + re.escape(NOW_END) + r"\n?", "", text, flags=re.S).lstrip("\n")
    if METRIC_HEADER not in text:
        text = text.rstrip("\n") + ("\n\n" if text.strip() else "") + "## Daily metrics\n\n" + METRIC_HEADER + "\n"
    if not re.search(rf"^\| {today} \|", text, flags=re.M):
        hb = res["hours_beta"] if res["hours_beta"] is not None else "n/a"
        text = text.rstrip("\n") + f"\n| {today} | {res['p012']} | {res['merged_today']} | {res['reruns']} | {hb} |\n"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(block + "\n" + text, encoding="utf-8")


def summary(res: dict, log: list[str], dry: bool) -> list[str]:
    return [f"DONE: {'would do: ' if dry else 'did: '}{'; '.join(log) or 'no actions'}",
            f"NEXT: queue {len(res['queue'])}, red PRs {len(res['red_prs'])}, open P0-P2 {res['p012']}",
            f"NEEDS: {'; '.join(res['alerts']) or 'nothing'}"]


def main(argv: list[str] | None = None, gh: GhRunner | None = None, now: dt.datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description="Factory watchdog (one pass)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print actions only (default)")
    mode.add_argument("--apply", action="store_true", help="perform the safe actions and write STATE.md")
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--state-file", default=str(DEFAULT_STATE))
    args = ap.parse_args(argv)
    dry = not args.apply
    gh = gh or GhRunner(dry_run=dry)
    gh.dry_run = dry
    now = now or dt.datetime.now(dt.timezone.utc)
    state_file = Path(args.state_file)
    sidecar_path = state_file.with_suffix(".watchdog.json")
    sidecar = load_sidecar(sidecar_path)
    try:
        data = collect(gh, args.repo, now.date())
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"DONE: nothing (could not read state)\nNEXT: retry next pass\nNEEDS: gh read failed: {exc}")
        return 1
    res = analyse(data, now, sidecar.get("requeues", {}))
    open_nums = {str(p["number"]) for p in data["prs"]}
    sidecar["requeues"] = {k: v for k, v in sidecar.get("requeues", {}).items() if k in open_nums}
    log = execute(gh, args.repo, res["actions"], sidecar)
    if not dry:
        write_state(state_file, res, log, now)
        sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
    print("\n".join(summary(res, log, dry)))
    return 1 if res["alerts"] else 0


if __name__ == "__main__":
    sys.exit(main())
