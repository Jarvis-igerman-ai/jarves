"""JARVES Performance Advisor: evidence-gated recommendations and optional autonomous GitHub issues.

Reuses scheduled speed tests, selfspeed history, issue_registry and the existing public developer repository.
No new timers, background polls, cloud AI calls or sensitive diagnostics. The owner opts in once on the Dashboard
to publish numeric, content-free performance findings. Nothing is sent by default.
"""
from __future__ import annotations

import json
import math
import os
import statistics
import time
from pathlib import Path
from typing import Callable

CONFIG = "perf_advisor.json"
MIN_RECENT = 3
MAX_OPEN_PAGE = 100
AUTO_COOLDOWN_S = 7 * 86400

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
    """Owner consent is local and durable; do not store credentials here."""
    try:
        data = json.loads((Path(home) / CONFIG).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {"auto_github": data.get("auto_github") is True,
                    "last_sent": float(data.get("last_sent") or 0)}
    except (OSError, ValueError, TypeError, OverflowError):
        pass
    return {"auto_github": False, "last_sent": 0.0}


def configure(home: Path, enabled: bool) -> dict:
    """Explicit opt-in from an authenticated owner Dashboard POST. No token or private data persisted."""
    state = options(home)
    state["auto_github"] = enabled is True
    p = Path(home) / CONFIG
    p.parent.mkdir(parents=True, exist_ok=True)
    temp = p.with_suffix(".tmp")
    temp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(temp, p)
    return state


def _numeric(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    return float(value)


def recommendations(rows: list[dict], *, max_results: int = 4) -> list[dict]:
    """Sustained like-for-like regressions, not one noisy reading or an unmeasured metric.

    Last three comparable samples must include 2+ genuine warnings/failures, including the latest.
    An over-budget failure is enough only when repeated; a warning requires a >=25% increase
    against >=3 older comparable runs. No baseline -> warn-only findings are NOT escalated.
    """
    out = []
    if not rows:
        return out
    recent = rows[-1]
    machine_class = recent.get("class")
    if machine_class not in ("owner", "ci"):
        return out
    for metric in sorted((recent.get("values") or {})):
        if not isinstance(metric, str) or metric not in SUGGESTIONS:
            continue
        samples = [(r.get("values", {}).get(metric), r.get("statuses", {}).get(metric, "pass"))
                   for r in rows if r.get("class") == machine_class
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
    return sorted(out, key=lambda a: (a["severity"], -a["recent_bad"], a["metric"]))[:max_results]


def detect(home: Path, *, rows: list[dict] | None = None, registry=None) -> list[dict]:
    from . import issue_registry, selfspeed
    series = rows if rows is not None else selfspeed.History(home).rows("speedtest", 30)
    findings = recommendations(series)
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


def auto_report(home: Path, findings: list[dict], *, token_fn: Callable[[], str] | None = None,
                request=None, clock: Callable[[], float] = time.time) -> dict:
    """Autonomous *performance-only* issue reporting, once owner enabled the durable setting.

    One new issue at most every seven days. Check all existing labeled issues first (fail closed
    on errors, 100+ results, or a missing auth token). Report only sanitized, fixed-key numbers.
    Never send logs, paths, transcripts, prompts, or a full local diagnostic bundle.
    """
    from . import issue_registry as ir
    state = options(home)
    if not state["auto_github"]:
        return {"status": "disabled"}
    if not findings:
        return {"status": "no_finding"}
    now = clock()
    if now - state["last_sent"] < AUTO_COOLDOWN_S:
        return {"status": "cooldown"}
    token = (token_fn or ir.find_token)()
    if not token:
        return {"status": "no_token"}
    call = request or ir._http
    code, existing = call("GET", f"{ir.API_BASE}/repos/{ir.REPO}/issues?labels={ir.LABEL}&state=all&per_page=100", token)
    if code != 200 or not isinstance(existing, list) or len(existing) >= MAX_OPEN_PAGE:
        return {"status": "lookup_unavailable"}
    for candidate in findings:
        iid = candidate.get("issue_id", "")
        if not iid or candidate.get("reported_url"):
            continue
        marker = f"jarves-issue: {iid}"
        duplicate = next((x for x in existing if marker in str(x.get("body") or "")), None)
        if duplicate:
            url = str(duplicate.get("html_url") or "")
            if url:
                ir.Registry(home).mark_reported(iid, url=url)
            continue
        ev, _ = ir.clean_evidence({k: candidate.get(k) for k in
                                   ("metric", "class", "median", "baseline", "change_pct", "samples", "recent_bad")})
        body = (f"**Automated JARVES Performance Advisor (owner opt-in).**\n\n"
                f"**Metric:** {candidate['metric']} · **Class:** {candidate['class']}\n"
                f"**Repeated evidence (numeric only):**\n"
                + "\n".join(f"- {k}: {v}" for k, v in sorted(ev.items()))
                + f"\n\n**Suggested investigation:** {candidate['advice']}\n\n"
                  "This is a measured investigation candidate, not a proven root cause. "
                  "No personal text, local file paths, or private logs were uploaded.\n"
                  f"<!-- {marker} -->")
        payload = {"title": f"[Performance] {candidate['metric']} repeatedly slow",
                   "body": body, "labels": [ir.LABEL]}
        status, outcome = call("POST", f"{ir.API_BASE}/repos/{ir.REPO}/issues", token, payload)
        if status != 201 or not isinstance(outcome, dict) or not outcome.get("html_url"):
            return {"status": "send_failed", "http_status": status}
        url = str(outcome["html_url"])
        ir.Registry(home).mark_reported(iid, url=url)
        state["last_sent"] = now
        dest = Path(home) / CONFIG
        temp = dest.with_suffix(".tmp")
        temp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(temp, dest)
        return {"status": "filed", "issue_id": iid, "url": url}
    return {"status": "already_reported"}


def on_speedtest(home: Path, *, registry=None) -> list[dict]:
    """Called only after a completed, recorded speed test: never from an idle timer."""
    from . import logs
    try:
        findings = detect(home, registry=registry)
        if findings:
            result = auto_report(home, findings)
            logs.info("perf_advisor", "PERF_ADVICE", "performance recommendations ready",
                      findings=len(findings), auto_status=result["status"])
        return findings
    except Exception as exc:
        # Advisors must never turn a successful measured test into a failed run.
        logs.warn("perf_advisor", "PERF_ADVISOR_FAILED", "performance advisor unavailable",
                  error_type=type(exc).__name__)
        return []
