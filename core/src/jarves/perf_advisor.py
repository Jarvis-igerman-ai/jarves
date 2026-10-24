"""JARVES Performance Advisor: evidence-gated recommendations and optional autonomous GitHub issues.

Reuses scheduled speed tests, selfspeed history, issue_registry and the existing public developer repository.
No new timers, background polls, cloud AI calls or sensitive diagnostics. The owner opts in once on the Dashboard
to publish numeric, content-free performance findings. Nothing is sent by default.
"""
from __future__ import annotations

import json
import math
import os
import re
import statistics
import time
from pathlib import Path
from typing import Callable

CONFIG = "perf_advisor.json"
MIN_RECENT = 3
MAX_OPEN_PAGE = 100
AUTO_COOLDOWN_S = 24 * 3600  # at most one aggregate issue update per day; normal measurement remains weekly
PERF_MARKER = "jarves-performance-health:v1"
PERF_TITLE = "[Performance] JARVES performance health"

# Advice is deliberately a diagnostic hypothesis, not a claim that the cause is known.
SUGGESTIONS = {
    "tool_p95_ms": ("Investigate the slowest tool calls and external I/O; cache only stable responses", "בדוק קריאות כלים איטיות וקלט/פלט; שמור במטמון רק תשובות יציבות"),
    "tool_p50_ms": ("Profile common tool overhead and synchronous work in the request path", "מדוד את התקורה בכלים ועבודה סינכרונית בנתיב הבקשה"),
    "wake_to_first_audio_s": ("Profile wake detection, live-session setup and first audio separately on real hardware", "מדוד בנפרד זיהוי מילת הפעלה, חיבור שיחה ושמע ראשון בחומרה אמיתית"),
    "dead_air_p95_s": ("Check turn-end detection, network jitter and speech streaming, without changing safety timeouts blindly", "בדוק זיהוי סיום תור, עיכובי רשת והזרמת דיבור בלי לשנות תזמונים בעיוורון"),
    "core_start_ms": ("Profile startup imports and initial service registration; defer noncritical work only after tests", "בדוק טעינת מודולים ואתחול שירותים; דחה עבודה לא חיונית רק לאחר בדיקות"),
    "spawn_to_ready_s": ("Inspect process startup and antivirus/disk variance on the same device", "בדוק אתחול תהליך והשפעת דיסק או אנטי־וירוס באותו מחשב"),
    "dashboard_first_load_ms": ("Profile initial dashboard assets and blocking page requests", "מדוד קובצי פתיחה וקריאות חוסמות בלוח הבקרה"),
    "dashboard_api_max_ms": ("Identify the slowest page endpoint and bound disk/database reads", "אתר את API העמוד האיטי וצמצם קריאות דיסק או מסד נתונים"),
    "idle_cpu_pct": ("Inspect idle polling frequency and busy loops; preserve responsiveness", "בדוק תדירות polling ולולאות פעילות במנוחה"),
    "idle_disk_read_kb_per_min": ("Profile unexpected disk readers while idle; avoid eager imports or repeated scans", "מדוד קריאות דיסק במנוחה ובדוק סריקות חוזרות"),
    "idle_disk_write_kb_per_min": ("Look for redundant state writes and excessive log flushes", "בדוק כתיבות מצב מיותרות וריקון יומנים תכוף"),
    "private_mb": ("Profile retained objects and cache limits; do not change cache behavior without a baseline", "בדוק עצמים שנשמרים בזיכרון וגבולות מטמון"),
    "steady_rss_mb": ("Compare process memory after warm-up and under a similar workload", "השווה זיכרון תהליך לאחר חימום ובעומס דומה"),
    "threads": ("Review background thread lifecycle and unnecessary service workers", "בדוק חיי threads ועובדי רקע מיותרים"),
    "openai_roundtrip_ms": ("Measure network availability and region; do not attribute transport latency to the model", "מדוד השהיית רשת בנפרד מזמן תגובת המודל"),
}
FALLBACK = ("Repeat the metric on the same device and profile the owning operation before optimizing",
            "מדוד שוב באותו מכשיר ואתר את הפעולה האיטית לפני שינוי קוד")


def options(home: Path) -> dict:
    """Explicit opt-in with a small local reporting cursor; no credentials or personal data."""
    base = {"auto_github": False, "last_sent": 0.0, "issue_number": 0, "issue_url": "", "published": []}
    try:
        data = json.loads((Path(home) / CONFIG).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return base
        ts = float(data.get("last_sent") or 0)
        num = data.get("issue_number")
        published = data.get("published")
        return {
            "auto_github": data.get("auto_github") is True,
            "last_sent": ts if math.isfinite(ts) and ts >= 0 else 0.0,
            "issue_number": num if type(num) is int and num > 0 else 0,
            "issue_url": data.get("issue_url") if isinstance(data.get("issue_url"), str) and
                         data.get("issue_url", "").startswith("https://github.com/igalgerman/jarves/issues/") else "",
            "published": [x for x in published if isinstance(x, dict)][:len(SUGGESTIONS)]
                         if isinstance(published, list) else [],
        }
    except (OSError, ValueError, TypeError, OverflowError):
        return base


def _store(home: Path, state: dict) -> None:
    p = Path(home) / CONFIG
    p.parent.mkdir(parents=True, exist_ok=True)
    temp = p.with_suffix(".tmp")
    temp.write_text(json.dumps(state, separators=(",", ":")), encoding="utf-8")
    os.replace(temp, p)


def configure(home: Path, enabled: bool) -> dict:
    """The authenticated owner enables/disables GitHub reporting; local recommendations always work."""
    state = options(home)
    state["auto_github"] = enabled is True
    _store(home, state)
    return state


def _numeric(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    return float(value)


def recommendations(rows: list[dict], *, max_results: int | None = None) -> list[dict]:
    """Sustained like-for-like regressions, not one noisy reading or an unmeasured metric.

    Last three comparable samples must include 2+ genuine warnings/failures, including the latest.
    An over-budget failure is enough only when repeated; a warning requires a >=25% increase
    against >=3 older comparable runs. No baseline -> warn-only findings are NOT escalated.
    """
    out = []
    if not rows:
        return out
    recent = rows[-1]
    # Compact selfspeed history uses `cls`; full SpeedResult uses `class`.
    machine_class = recent.get("class") or recent.get("cls")
    if machine_class not in ("owner", "ci"):
        return out
    for metric in sorted((recent.get("values") or {})):
        if not isinstance(metric, str) or metric not in SUGGESTIONS:
            continue
        samples = [(r.get("values", {}).get(metric), r.get("statuses", {}).get(metric, "pass"))
                   for r in rows if (r.get("class") or r.get("cls")) == machine_class
                   and _numeric(r.get("values", {}).get(metric)) is not None]
        if len(samples) < MIN_RECENT:
            continue
        last = samples[-3:]
        bad = sum(status in ("warn", "fail") for _, status in last)
        failures = sum(status == "fail" for _, status in last)
        if bad < 2 or last[-1][1] not in ("warn", "fail"):
            continue
        current = statistics.median(float(v) for v, _ in last)
        older = samples[:-3][-5:]
        baseline = statistics.median(float(v) for v, _ in older) if len(older) >= 3 else None
        change = round((current / baseline - 1) * 100, 1) if baseline and baseline > 0 else None
        if failures < 2 and (change is None or change < 25):
            continue
        advice_en, advice_he = SUGGESTIONS.get(metric, FALLBACK)
        out.append({"metric": metric, "class": machine_class, "status": "fail" if failures >= 2 else "warn",
                    "severity": "P2" if failures >= 2 else "P3", "recent_bad": bad,
                    "samples": len(samples), "median": round(current, 3),
                    "baseline": round(baseline, 3) if baseline is not None else None,
                    "change_pct": change, "advice": advice_en, "advice_he": advice_he,
                    "confidence": "repeated"})
    sorted_findings = sorted(out, key=lambda a: (a["severity"], -a["recent_bad"], a["metric"]))
    return sorted_findings[:max_results] if max_results is not None else sorted_findings


def detect(home: Path, *, rows: list[dict] | None = None, registry=None) -> list[dict]:
    from . import issue_registry, selfspeed
    series = rows if rows is not None else selfspeed.History(home).rows("speedtest", 30)
    findings = recommendations(series, max_results=len(SUGGESTIONS))
    reg = registry or issue_registry.Registry(home)
    for f in findings:
        record = reg.record("speed", f"PERF_{f['metric'].upper()}", f["severity"],
                            {"metric": f["metric"], "cls": f["class"], "median": f["median"],
                             "baseline": f["baseline"], "change_pct": f["change_pct"],
                             "samples": f["samples"], "recent_bad": f["recent_bad"]},
                            title=f"Performance: sustained {f['metric']} regression",
                            title_he=f"ביצועים: האטה חוזרת במדד {f['metric']}",
                            source="perf_advisor",
                            # repeated identical observations must not count as new incidents
                            observation=json.dumps({"metric": f["metric"], "median": f["median"],
                                                    "class": f["class"], "samples": f["samples"]}, sort_keys=True))
        f["issue_id"] = record["id"] if record else ""
        f["reported_url"] = record.get("reported_url", "") if record else ""
    return findings


def _snapshot(findings: list[dict]) -> list[dict]:
    """Bounded, allowlisted metric summaries only; external strings never enter issue text."""
    result = []
    keys = set()
    for f in findings:
        metric, cls = f.get("metric"), f.get("class")
        if metric not in SUGGESTIONS or cls not in ("ci", "owner"):
            continue
        key = (metric, cls)
        if key in keys:
            continue
        val = _numeric(f.get("median"))
        if val is None:
            continue
        keys.add(key)
        result.append({"metric": metric, "class": cls,
                       "severity": "P2" if f.get("severity") == "P2" else "P3",
                       "median": round(val, 3),
                       "samples": min(max(int(f.get("samples") or 0), 0), 10000)})
    return sorted(result, key=lambda x: (x["class"], x["metric"]))


def _meaningful(previous: list[dict], current: list[dict]) -> bool:
    a = {(r.get("metric"), r.get("class")): r for r in previous}
    b = {(r["metric"], r["class"]): r for r in current}
    if a.keys() != b.keys():
        return True  # new regression, or one has recovered
    for key, item in b.items():
        prior = a[key]
        if item["severity"] != prior.get("severity"):
            return True
        old = _numeric(prior.get("median"))
        if old is None or abs(item["median"] - old) > max(old, 1e-6) * MEANINGFUL_SHIFT:
            return True
    return False


def _body(snapshot: list[dict]) -> str:
    """Consolidated current-state body: no raw text, identifiers, locations or logs."""
    lines = [
        "## JARVES Performance — measured findings",
        "",
        "Automatically maintained **only after owner opt-in** and a completed speed test.",
        "Data contains allowlisted metric names and numeric summaries only. "
        "It is an investigation queue, **not a diagnosed root cause**.",
        "",
    ]
    if snapshot:
        lines.extend(["| Metric | Class | Severity | Recent median | Samples |",
                      "| --- | --- | --- | ---: | ---: |"])
        for f in snapshot:
            lines.append(f"| \`{f['metric']}\` | {f['class']} | {f['severity']} | {f['median']} | {f['samples']} |")
        lines.extend(["", "## Suggested investigations"])
        for f in snapshot:
            advice = SUGGESTIONS[f["metric"]][0]
            lines.append(f"- **{f['metric']}**: {advice}")
    else:
        lines.append("No persistent slowdown currently detected in the latest comparable measurements.")
    lines.extend(["", "Updated only when a metric appears, recovers, changes severity, or shifts >20%.",
                  "The existing scheduled weekly speed test supplies measurements; there is no added poller.",
                  "Never attach conversation text, prompts, transcripts, file paths or tokens.", "",
                  SUMMARY_MARKER])
    return "\n".join(lines)


def auto_report(home: Path, findings: list[dict], *, token_fn: Callable[[], str] | None = None,
                request=None, clock: Callable[[], float] = time.time) -> dict:
    """Upsert ONE tracked performance issue; update its body only for meaningful new evidence.

    A single issue contains ALL proven metrics. No comment per sample, no issue per metric,
    no new background schedule. At most one GitHub write per 24 h, and no write at all
    when no relevant trend has changed. GitHub lookup failure never creates duplicates.
    """
    from . import issue_registry as ir
    state = options(home)
    if not state["auto_github"]:
        return {"status": "disabled"}
    snapshot = _snapshot(findings)
    if not snapshot and not state["published"]:
        return {"status": "no_finding"}
    if not _meaningful(state["published"], snapshot):
        return {"status": "unchanged", "url": state["issue_url"]}
    now = clock()
    if state["last_sent"] > 0 and now - state["last_sent"] < AUTO_COOLDOWN_S:
        return {"status": "cooldown", "url": state["issue_url"]}
    token = (token_fn or ir.find_token)()
    if not token:
        return {"status": "no_token"}
    call = request or ir._http
    issue = None
    if state["issue_number"]:
        code, found = call("GET", f"{ir.API_BASE}/repos/{ir.REPO}/issues/{state['issue_number']}", token)
        if code != 200 or not isinstance(found, dict) or SUMMARY_MARKER not in str(found.get("body") or ""):
            return {"status": "lookup_unavailable"}  # never replace or fork a possibly existing issue
        issue = found
    else:
        code, existing = call("GET", f"{ir.API_BASE}/repos/{ir.REPO}/issues?labels={ir.LABEL}&state=all&per_page=100", token)
        if code != 200 or not isinstance(existing, list) or len(existing) >= MAX_OPEN_PAGE:
            return {"status": "lookup_unavailable"}
        matches = [x for x in existing if isinstance(x, dict) and SUMMARY_MARKER in str(x.get("body") or "")]
        if len(matches) > 1:
            return {"status": "ambiguous_existing"}
        issue = matches[0] if matches else None
    body = _body(snapshot)
    if issue:
        if issue.get("state") == "closed":
            return {"status": "closed_issue"}  # owner closed it; do not reopen automatically
        n = issue.get("number")
        if type(n) is not int or n <= 0:
            return {"status": "lookup_unavailable"}
        status, output = call("PATCH", f"{ir.API_BASE}/repos/{ir.REPO}/issues/{n}", token,
                              {"body": body})
        outcome = "updated"
    else:
        status, output = call("POST", f"{ir.API_BASE}/repos/{ir.REPO}/issues", token,
                              {"title": REPORT_TITLE, "body": body, "labels": [ir.LABEL]})
        outcome = "filed"
    if status not in (200, 201) or not isinstance(output, dict) or not output.get("html_url") or type(output.get("number")) is not int:
        return {"status": "send_failed", "http_status": status}
    url = str(output["html_url"])
    state.update(last_sent=now, issue_number=output["number"], issue_url=url, published=snapshot)
    _store(home, state)
    # Reflect one shared public issue URL on ALL locally tracked performance metrics.
    reg = ir.Registry(home)
    for f in findings:
        iid = f.get("issue_id")
        if iid:
            reg.mark_reported(iid, url=url)
    return {"status": outcome, "issue_url": url, "metrics": len(snapshot)}


def on_speedtest(home: Path, *, registry=None) -> list[dict]:
    """Called only after a completed, recorded speed test: never from an idle timer."""
    from . import logs
    try:
        findings = detect(home, registry=registry)
        result = auto_report(home, findings)
        if findings or result["status"] in ("updated", "filed"):
            logs.info("perf_advisor", "PERF_ADVICE", "performance recommendations ready",
                      count=len(findings), reporting=result["status"])
        return findings
    except Exception as exc:
        # Advisors must never turn a successful measured test into a failed run.
        logs.warn("perf_advisor", "PERF_ADVISOR_FAILED", "performance advisor unavailable",
                  error_type=type(exc).__name__)
        return []
