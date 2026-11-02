"""External release-health watch (V451-WATCHDOG): read-only findings from the GitHub sync snapshot.

Pure function plus one alert wrapper. It never merges, re-queues, reruns checks, tags or
approves anything: it only names a fallen merge queue, a stuck PR, a red main and a missing beta.
"""
from __future__ import annotations

import datetime

STUCK_PR_S = 24 * 3600
QUEUE_STALL_S = 2 * 3600
BETA_LAG_S = 6 * 3600


def _ts(value) -> float | None:
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def release_health(source: dict, now: float) -> list[dict]:
    """Findings {kind, key, summary}. Unknown data yields no finding, except an explicit red main."""
    out: list[dict] = []
    sha = str(source.get("sha") or "")
    # Only a COMPLETED failure alerts. Running, unknown, fetch-error and >100-run states stay silent.
    if sha and source.get("ci_state") == "failure" and not source.get("error"):
        out.append({"kind": "main_not_green", "key": sha[:8],
                    "summary": f"main {sha[:8]} has a failed check: only the fix merges"})
    for pr in source.get("open_prs", []):
        if pr.get("draft") or "no-automerge" in (pr.get("labels") or []):
            continue                      # Tier R / draft PRs wait for a human by design
        age = now - (_ts(pr.get("updated_at")) or now)
        n = pr.get("number")
        key = f"{n}@{str(pr.get('head_sha'))[:8]}"
        if pr.get("auto_merge") and age > QUEUE_STALL_S:
            out.append({"kind": "queue_stalled", "key": key,
                        "summary": f"PR #{n} has auto-merge on but no activity for {int(age // 3600)} h: queue may have dropped it"})
        elif not pr.get("auto_merge") and age > STUCK_PR_S:
            # LIMITATION: REST shows auto_merge, not mergeQueue entries (OPERATING_MODEL). A PR waiting
            # in the merge queue can look unqueued here, so this is a hint to check, not proof.
            out.append({"kind": "pr_stuck", "key": key,
                        "summary": f"PR #{n} idle for {int(age // 3600)} h with no auto-merge set (merge-queue entries are not visible here; check)"})
    beta = source.get("latest_beta")
    if sha and source.get("ci_state") == "success" and isinstance(beta, dict):
        built = _ts(source.get("main_committed_at"))
        released = _ts(beta.get("published_at"))
        if built is not None and now - built > BETA_LAG_S and (released is None or released < built):
            out.append({"kind": "beta_missing", "key": sha[:8],
                        "summary": f"green main {sha[:8]} has no newer beta (last {beta.get('tag') or 'none'})"})
    return out


SEEN_KEY = "release_health_seen"
MAX_RAISE_PER_TICK = 5


def watch_release_health(store, source: dict, notify=None) -> list[str]:
    """Raise one deduplicated alert per finding; returns the kinds newly raised.

    Dedup uses its own key set, pruned to the findings still active, so other autopilot alerts
    (shared 50-entry list) cannot evict it and cause re-raising. A cleared finding may alert again
    later. At most MAX_RAISE_PER_TICK new alerts per tick.
    """
    from . import autopilot_watch as watch
    findings = release_health(source, store.clock())
    active = {f"release_{f['kind']}|{f['key']}" for f in findings}
    seen = [k for k in watch._load(store, SEEN_KEY, []) if k in active]
    raised = []
    for f in findings:
        k = f"release_{f['kind']}|{f['key']}"
        if k in seen or len(raised) >= MAX_RAISE_PER_TICK:
            continue
        watch.raise_alert(store, f"release_{f['kind']}", f["summary"], task_id=f["key"], notify=notify, dedupe=False)
        seen.append(k)
        raised.append(f["kind"])
    watch._save(store, SEEN_KEY, seen)
    return raised
