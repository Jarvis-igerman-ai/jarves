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
                    item["linked_issues"] = sorted({int(n) for n in re.findall(
                        r"(?i)\b(?:fix(?:e[sd])?|close[sd]?|resolve[sd]?)\s+#(\d+)\b", row.get("body") or "")})
                dest.append(item)
            if len(rows) < 100:
                break
        else:
            truncated = True
    branch = get(f"/repos/{REPO}/branches/main")
    return {"issues": issues, "prs": prs, "sha": branch["commit"]["sha"], "truncated": truncated}


def apply_snapshot(store, snapshot: dict, plan: dict | None = None) -> dict:
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
              "issue_count": len(issues), "open_bugs": sum(x["state"] == "open" and "bug" in x["labels"] for x in issues.values()),
              "open_prs": [x for x in snapshot.get("prs", []) if x["state"] == "open"],
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
