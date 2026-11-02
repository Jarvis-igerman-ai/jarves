"""Bounded read-only GitHub reconciliation; issue closure is never test evidence."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

REPO = "Jarvis-igerman-ai/jarves"
PLAN = Path(__file__).with_name("version_plan.json")
MAX_PAGES = 10
MAX_OPEN_ROWS = 150
BUG_LABELS = ("bug", "P0", "P1", "P2", "P3", "showstopper")
VERSION_LABELS = ("4.5.1", "5", "5.0", "6", "6.0", "7", "7.0")


class SameGitHubRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if target.scheme != "https" or target.hostname != "api.github.com":
            raise ValueError("GitHub redirect outside the approved API refused")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def public_get(path: str):
    """Public metadata only; no token, shell, arbitrary URL or write endpoint."""
    if not path.startswith(f"/repos/{REPO}/"):
        raise ValueError("repository outside the workflow scope")
    request = urllib.request.Request("https://api.github.com" + path,
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": "JARVES-workflow"})
    with urllib.request.build_opener(SameGitHubRedirect()).open(request, timeout=15) as response:
        body = response.read(4 * 1024 * 1024 + 1)
        if len(body) > 4 * 1024 * 1024:
            raise ValueError("GitHub metadata response exceeds the limit")
        return json.loads(body)


MAX_FILE_FETCHES = 30
_FILES_CACHE: dict[tuple[int, str], list[str]] = {}


def pr_files(get, number: int, sha: str) -> list[str]:
    """Changed file names of one open PR, cached by head sha. Unknown or too large -> [] (callers treat as Tier R)."""
    key = (number, sha)
    if sha and key in _FILES_CACHE:
        return _FILES_CACHE[key]
    names: list[str] = []
    try:
        for page in range(1, 4):
            rows = get(f"/repos/{REPO}/pulls/{number}/files?per_page=100&page={page}")
            if not isinstance(rows, list):
                return []
            names += [str(r["filename"]) for r in rows if isinstance(r, dict) and r.get("filename")]
            if len(rows) < 100:
                break
        else:
            return []  # 300+ files: unsure, leave unknown
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError):
        return []  # rate limit or outage: not cached, unknown
    if sha:
        if len(_FILES_CACHE) > 500:
            _FILES_CACHE.clear()
        _FILES_CACHE[key] = names
    return names


def main_ci_state(get, sha: str) -> str:
    """Read-only three-way state of the main commit checks: success | failure | pending | unknown.

    failure = a completed run failed. pending = runs still queued/in progress and none failed.
    unknown = fetch error, no runs, more than one page of runs, or odd conclusions (e.g. cancelled).
    """
    try:
        data = get(f"/repos/{REPO}/commits/{sha}/check-runs?per_page=100")
        runs = data.get("check_runs") if isinstance(data, dict) else None
        if not runs or int(data.get("total_count", 0)) > len(runs):
            return "unknown"
        if any(r.get("status") == "completed" and r.get("conclusion") in ("failure", "timed_out", "startup_failure") for r in runs):
            return "failure"
        if any(r.get("status") != "completed" for r in runs):
            return "pending"
        ok = all(r.get("conclusion") in ("success", "skipped", "neutral") for r in runs)
        return "success" if ok else "unknown"
    except (OSError, ValueError, KeyError, TypeError, AttributeError, urllib.error.URLError):
        return "unknown"


def main_ci_green(get, sha: str) -> bool:
    """Every check run on the main commit finished and none failed. Any doubt -> False."""
    return main_ci_state(get, sha) == "success"


def collect(get=public_get) -> dict:
    issues, prs = [], []
    truncated = False
    for collection, dest in (("issues", issues), ("pulls", prs)):
        for page in range(1, MAX_PAGES + 1):
            rows = get(f"/repos/{REPO}/{collection}?state=all&per_page=100&page={page}")
            if not isinstance(rows, list):
                raise ValueError("GitHub did not return a metadata list")
            for row in rows:
                if collection == "issues" and "pull_request" in row:
                    continue
                item = {"number": row["number"], "title": str(row.get("title", ""))[:250],
                        "state": row.get("state", "unknown"), "url": row.get("html_url", ""),
                        "updated_at": row.get("updated_at"),
                        "labels": [x.get("name", "") for x in row.get("labels", [])]}
                if collection == "pulls":
                    # A closed PR is not necessarily merged. Never infer merge from state alone.
                    item["merged_at"] = row.get("merged_at")
                    item["head_sha"] = (row.get("head") or {}).get("sha", "")
                    item["draft"] = bool(row.get("draft"))
                    item["auto_merge"] = bool(row.get("auto_merge"))
                    item["linked_issues"] = sorted({int(n) for n in re.findall(
                        r"(?i)\b(?:fix(?:e[sd])?|close[sd]?|resolve[sd]?)\s+#(\d+)\b", row.get("body") or "")})
                dest.append(item)
            if len(rows) < 100:
                break
        else:
            truncated = True
    branch = get(f"/repos/{REPO}/branches/main")
    fetched = 0
    for item in prs:
        if item["state"] != "open":
            continue
        if fetched >= MAX_FILE_FETCHES and (item["number"], item["head_sha"]) not in _FILES_CACHE:
            item["files"] = []
            continue
        if (item["number"], item["head_sha"]) not in _FILES_CACHE:
            fetched += 1
        item["files"] = pr_files(get, item["number"], item["head_sha"])
    sha = branch["commit"]["sha"]
    committed = ((branch["commit"].get("commit") or {}).get("committer") or {}).get("date", "")
    state = main_ci_state(get, sha)
    return {"issues": issues, "prs": prs, "sha": sha, "truncated": truncated, "ci_green": state == "success", "ci_state": state,
            "main_committed_at": committed, "latest_beta": latest_beta(get)}


def latest_beta(get) -> dict | None:
    """Read-only: newest published prerelease. None = unknown (no beta finding is raised)."""
    try:
        rows = get(f"/repos/{REPO}/releases?per_page=20")
        if not isinstance(rows, list):
            return None
        for r in rows:
            if r.get("prerelease") and not r.get("draft"):
                return {"tag": str(r.get("tag_name", ""))[:60], "published_at": r.get("published_at") or ""}
        return {"tag": "", "published_at": ""}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, urllib.error.URLError):
        return None


def tracked_issue(item: dict, bug_labels=BUG_LABELS) -> bool:
    """An open issue the console must show: a defect label (not only `bug`) or a version label."""
    lowered = {x.strip().lower() for x in item["labels"]}
    return item["state"] == "open" and bool(lowered & {x.lower() for x in bug_labels}
                                            or {x.lstrip("v") for x in lowered} & set(VERSION_LABELS))


def apply_snapshot(store, snapshot: dict, plan: dict | None = None, bug_labels=BUG_LABELS) -> dict:
    plan = plan or json.loads(PLAN.read_text(encoding="utf-8"))
    store.seed_plan(plan)
    issues = {int(x["number"]): x for x in snapshot.get("issues", [])}
    mapped, unmapped = set(), []
    existing = {t["id"]: t for t in store.snapshot()["tasks"]}
    for version in plan["versions"]:
        for requirement in version["requirements"]:
            numbers = requirement.get("issue_numbers", [])
            mapped.update(numbers)
            rows = [issues[n] for n in numbers if n in issues]
            missing = [n for n in numbers if n not in issues]
            if not numbers or missing:
                unmapped.append(requirement["id"])
            linked = [p for p in snapshot.get("prs", []) if set(p.get("linked_issues", [])) & set(numbers)]
            changes = {
                "source_issues": rows, "source_prs": linked,
                "source_closed": bool(numbers and len(rows) == len(numbers) and all(x["state"] == "closed" for x in rows)),
                "source_synced_at": store.clock(),
                "mapping_note": "No granular issue mapped" if not numbers else
                                "Missing GitHub metadata; state remains unknown" if missing else
                                "Issue/PR metadata is not installed-app verification",
            }
            current = existing.get(requirement["id"], {})
            if any(current.get(k) != v for k, v in changes.items() if k != "source_synced_at"):
                store.update_metadata(requirement["id"], changes)
    source = {"mode": "github", "sha": snapshot.get("sha", ""), "synced_at": store.clock(),
              "unmapped": unmapped, "truncated": bool(snapshot.get("truncated")),
              "issue_count": len(issues), "open_bugs": sum(x["state"] == "open" and bool({l.lower() for l in x["labels"]} & {b.lower() for b in bug_labels})
                               for x in issues.values()),
              "open_issues": [{**x, "title": x["title"][:120]} for x in sorted(issues.values(), key=lambda i: i["number"])
                              if tracked_issue(x, bug_labels)][:MAX_OPEN_ROWS],
              "open_prs": [{**x, "title": x["title"][:120]} for x in snapshot.get("prs", []) if x["state"] == "open"],
              "ci_green": bool(snapshot.get("ci_green")), "ci_state": snapshot.get("ci_state", "unknown"),
              "main_committed_at": snapshot.get("main_committed_at", ""), "latest_beta": snapshot.get("latest_beta"),
              "scope_note": plan.get("scope_note", ""), "error": ""}
    store.set_source(source)
    return source


def sync(store, get=public_get) -> dict:
    """On failure retain the last snapshot and prominently mark its freshness/error."""
    try:
        return apply_snapshot(store, collect(get))
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError):
        old = store.snapshot().get("source", {})
        source = {**old, "error": "GitHub sync unavailable; last known snapshot retained", "failed_at": store.clock()}
        store.set_source(source)
        return source
