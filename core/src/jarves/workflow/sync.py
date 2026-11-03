"""Bounded read-only GitHub reconciliation; issue closure is never test evidence."""
from __future__ import annotations

import json
import os
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
ISSUE_TASK_CAP = 10
ISSUE_TASK_ENV = "JARVES_WORKFLOW_ISSUE_TASKS"  # set to 0/off/false to disable issue->task creation
DEFECT_LABELS = {"bug", "p0", "p1", "p2", "p3", "showstopper"}
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
                    item["head_repo"] = str(((row.get("head") or {}).get("repo") or {}).get("full_name", ""))[:200]
                    item["head_ref"] =str((row.get("head") or {}).get("ref", ""))[:200]
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


LABEL_VERSIONS = (("4.5.1", "V4.5.1"), ("v5", "V5"), ("v6", "V6"), ("v7", "V7"))


def with_label_scope(plan: dict, snapshot: dict) -> dict:
    """Add open version-labelled issues that no requirement maps as new, unauthorised requirements.

    Labels only place scope; imported requirements keep execution_allowed=false.
    """
    mapped = {n for v in plan["versions"] for r in v["requirements"] for n in r.get("issue_numbers", [])}
    extra: dict[str, list] = {}
    for row in sorted(snapshot.get("issues", []), key=lambda x: int(x["number"])):
        number = int(row["number"])
        labels = set(row.get("labels", []))
        if row.get("state") != "open" or number in mapped:
            continue
        version = next((v for label, v in LABEL_VERSIONS if label in labels), None)
        if not version:
            continue
        extra.setdefault(version, []).append({
            "id": f"ISSUE-{number}", "title": str(row.get("title", ""))[:250] or f"Issue {number}",
            "lane": "bugs" if "bug" in labels else "features", "issue_numbers": [number],
            "required_validation": "app"})
    if not extra:
        return plan
    versions = [{**v, "requirements": list(v["requirements"])} for v in plan["versions"]]
    by_id = {v["id"]: v for v in versions}
    for version, reqs in extra.items():
        if version not in by_id:
            by_id[version] = {"id": version, "title": version, "requirements": []}
            versions.append(by_id[version])
        by_id[version]["requirements"].extend(reqs)
    return {**plan, "versions": versions}


# Labels that mean the defect needs a real device (microphone, wake word, voice, phone, car, hardware): app-level
# evidence is not enough, so the task requires device validation (OPERATING_MODEL: ear/device checks stay unknown).
DEVICE_LABELS = frozenset({"device", "mic", "microphone", "wake", "wake-word", "voice", "audio", "phone", "android",
                           "car", "hardware", "ear", "pi5", "raspberry-pi"})


def _norm_label(x) -> str:
    return str(x).strip().lower().replace("_", "-").replace(" ", "-")


def issue_validation(labels) -> str:
    """`device` for device-type labels (or a `device:*`/`device-*` label), else `app`."""
    norm = {_norm_label(x) for x in labels}
    return "device" if norm & DEVICE_LABELS or any(x.startswith(("device-", "device:")) for x in norm) else "app"


def owner_wait_label(labels) -> bool:
    """Owner-gated, owner-accepted known issues and anything waiting for the owner are never queued for a worker."""
    return any(x.startswith(("owner-gated", "known-issue", "needs-owner", "owner-wait", "waiting-owner", "needs-info"))
               for x in (_norm_label(y) for y in labels))


def issue_tasks_enabled(environ=None) -> bool:
    value = str((environ if environ is not None else os.environ).get(ISSUE_TASK_ENV, "1")).strip().lower()
    return value not in {"0", "off", "false", "no", "disabled"}


def authorized_versions(store) -> set[str]:
    """V4.5.1 plus the owner's autopilot authorized_versions; a bad or missing policy means V4.5.1 only."""
    allowed = {"V4.5.1"}
    try:
        from . import autopilot
        policy, error = autopilot.load(Path(store.path).parent)
        if not error:
            allowed |= {str(v) for v in policy.get("authorized_versions", [])}
    except Exception:  # noqa: BLE001  fail closed
        pass
    return allowed


def create_issue_tasks(store, snapshot: dict, cap: int = ISSUE_TASK_CAP, environ=None) -> list[str]:
    """Queue gh-N tasks for open agent-doable defects so ready work is not empty while issues are open.

    Metadata only: it never approves, runs a worker or changes an issue. Existing tasks are never
    touched, so a closed issue or an archived/finished gh-N task is never recreated. Imported scope
    (execution_allowed=false) does not count as coverage, but stays unauthorised itself.
    """
    if not issue_tasks_enabled(environ):
        return []
    tasks = store.snapshot()["tasks"]
    taken = {t["id"] for t in tasks}
    covered: set[int] = set()
    for t in tasks:  # any task linked to the issue covers it, whatever its state or authorisation
        covered.update(int(n) for n in t.get("issue_numbers", []) if str(n).isdigit())
        covered.update(int(i["number"]) for i in t.get("source_issues", [])
                       if isinstance(i, dict) and str(i.get("number", "")).isdigit())
    authorized = authorized_versions(store)
    created = []
    for row in sorted(snapshot.get("issues", []), key=lambda x: int(x["number"])):
        if len(created) >= cap:
            break
        number = int(row["number"])
        labels = {str(x).strip().lower() for x in row.get("labels", [])}
        if row.get("state") != "open" or f"gh-{number}" in taken or number in covered:
            continue
        if owner_wait_label(labels):
            continue
        defect = bool(labels & DEFECT_LABELS)
        if not defect and "agent-doable" not in labels:
            continue
        priority = next((i for i in range(4) if f"p{i}" in labels), 2)
        version = next((v for label, v in LABEL_VERSIONS if label in labels), "V4.5.1")
        if version not in authorized:
            continue  # autopilot.step decides for versions the owner has not authorised
        store.create_task({
            "id": f"gh-{number}", "title": f"#{number} {str(row.get('title', ''))[:200]}".strip(),
            "lane": "bugs" if defect else "features", "priority": priority, "version": version,
            "required_validation": issue_validation(labels), "execution_allowed": True,
            "acceptance": [{"id": "C1", "label": "Fix merged with a regression test and evidence recorded"}],
            "issue_numbers": [number], "source_url": row.get("url", ""),
            "scope_note": "Created by GitHub issue sync; issue state is not verification."})
        created.append(f"gh-{number}")
    return created


def apply_snapshot(store, snapshot: dict, plan: dict | None = None, bug_labels=BUG_LABELS, issue_tasks=False) -> dict:
    plan = plan or json.loads(PLAN.read_text(encoding="utf-8"))
    plan = with_label_scope(plan, snapshot)
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
    if issue_tasks:
        create_issue_tasks(store, snapshot)
    store.set_source(source)
    return source


def sync(store, get=public_get) -> dict:
    """On failure retain the last snapshot and prominently mark its freshness/error."""
    try:
        source = apply_snapshot(store, collect(get), issue_tasks=True)
        try:  # #764: owned PRs with a red check, conflict or review comment -> ledger message (public reads, no hook)
            from .pr_routing import route
            route(store, source.get("open_prs", []), get)
        except Exception:  # noqa: BLE001  routing is best effort and never fails the sync
            pass
        return source
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError):
        old = store.snapshot().get("source", {})
        source = {**old, "error": "GitHub sync unavailable; last known snapshot retained", "failed_at": store.clock()}
        store.set_source(source)
        return source
