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
TRACKER_MARKER = "<!-- jarves-performance-health:v1 -->"
TRACKER_TITLE = "[Performance] JARVES performance health"

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
    "greeting_missed_pct": ("Greeting audio is missing after nudge and retry; check the live session logs and provider status before changing timeouts", "שמע הברכה חסר גם אחרי תזכורת וניסיון חוזר; בדוק יומני שיחה חיה ומצב הספק לפני שינוי זמנים"),
    "openai_roundtrip_ms": ("Measure network availability and region; do not attribute transport latency to the model", "מדוד השהיית רשת בנפרד מזמן תגובת המודל"),
}
FALLBACK = ("Repeat the metric on the same device and profile the owning operation before optimizing",
            "מדוד שוב באותו מכשיר ואתר את הפעולה האיטית לפני שינוי קוד")


def options(home: Path) -> dict:
    """Local, explicit opt-in and tiny stable tracker state. No tokens are stored."""
    defaults = {"auto_github": False, "last_sent": 0.0, "last_signature": "",
                "last_active": [], "tracker_url": ""}
    try:
        data = json.loads((Path(home) / CONFIG).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            sent = _numeric(data.get("last_sent"))
            return {**defaults, "auto_github": data.get("auto_github") is True,
                    "last_sent": sent or 0.0,
                    "last_signature": str(data.get("last_signature") or "")[:2000],
                    "last_active": [x for x in data.get("last_active", [])[:40]
                                    if isinstance(x, str) and x.isidentifier()],
                    "tracker_url": str(data.get("tracker_url") or "")[:256]}
    except (OSError, ValueError, TypeError, OverflowError):
        pass
    return defaults


def _save_options(home: Path, state: dict) -> None:
    p = Path(home) / CONFIG
    p.parent.mkdir(parents=True, exist_ok=True)
    temp = p.with_suffix(".tmp")
    temp.write_text(json.dumps(state, separators=(",", ":")), encoding="utf-8")
    os.replace(temp, p)


def configure(home: Path, enabled: bool) -> dict:
    """Only an authenticated owner action can enable public GitHub reporting."""
    state = options(home)
    state["auto_github"] = enabled is True
    _save_options(home, state)
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
    from . import speedtest
    allowed = set(speedtest.load_budgets()) | set(SUGGESTIONS)
    out = []
    if not rows:
        return out
    recent = rows[-1]
    # Compact selfspeed history uses `cls`; full SpeedResult uses `class`.
    machine_class = recent.get("class") or recent.get("cls")
    if machine_class not in ("owner", "ci"):
        return out
    for metric in sorted((recent.get("values") or {})):
        if not isinstance(metric, str) or metric not in allowed:
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
                            observation=json.dumps({"metric": f["metric"], "severity": f["severity"],
                                                    "class": f["class"], "change_band":
                                                    round((f["change_pct"] or 0) / 25)}, sort_keys=True))
        f["issue_id"] = record["id"] if record else ""
        f["reported_url"] = record.get("reported_url", "") if record else ""
    return findings


def _signature(findings: list[dict]) -> str:
    """Stable enough not to churn over tiny numeric variance, but reacts to severity changes."""
    return json.dumps(sorted((f["metric"], f["class"], f["severity"],
                              round((f.get("change_pct") or 0) / 25))
                             for f in findings if f.get("metric") in _known_metrics()),
                      separators=(",", ":"))


def _known_metrics() -> set[str]:
    from . import speedtest
    return set(speedtest.load_budgets()) | set(SUGGESTIONS)


def _recovered(home: Path, names: list[str]) -> bool:
    """Never announce recovery based on one good or missing measurement."""
    from . import selfspeed
    rows = selfspeed.History(home).rows("speedtest", 30)
    for metric in names:
        samples = [r.get("statuses", {}).get(metric, "pass") for r in rows
                   if metric in r.get("values", {}) and (r.get("class") or r.get("cls")) == "owner"]
        if len(samples) < 3 or samples[-3:] != ["pass", "pass", "pass"]:
            return False
    return bool(names)


def _tracker_body(findings: list[dict], *, recovered: bool) -> str:
    """An intentionally bounded, numeric-only public issue, no device identity or free-text logs."""
    lines = [
        "## JARVES Performance Tracker",
        "Automatically updated from repeated *local* measurements after owner opt-in.",
        "Each metric is a measured investigation candidate, **not a proven root cause**.",
        "This is a single tracker; routine unchanged measurements do not trigger edits.",
        "",
    ]
    if recovered:
        lines += ["**Status: recovered** — at least three comparable successful measurements",
                  "were observed for every previously tracked metric.", ""]
    else:
        lines += [f"**Repeated regressions currently tracked: {len(findings)}**", ""]
        for f in findings:
            metric = f["metric"]
            if metric not in _known_metrics():
                continue
            numbers = {k: _numeric(f.get(k)) for k in ("median", "baseline", "change_pct")}
            measured = ", ".join(f"{k}: {v:g}" for k, v in numbers.items() if v is not None)
            advice = SUGGESTIONS.get(metric, FALLBACK)[0]
            lines += [f"### {metric} ({f['severity']}, {f['class']})",
                      f"Samples: {int(f['samples'])}; repeated warnings: {int(f['recent_bad'])}; {measured}",
                      f"Suggested investigation: {advice}", ""]
    lines += ["Only static metric identifiers, aggregated numeric evidence and canned suggestions are published.",
              "No speech, prompts, user content, file paths, IPs, credentials or private logs are included.",
              TRACKER_MARKER]
    return "\n".join(lines)


def auto_report(home: Path, findings: list[dict], *, token_fn: Callable[[], str] | None = None,
                request=None, clock: Callable[[], float] = time.time) -> dict:
    """Update ONE stable GitHub performance tracker after sustained material changes.

    No new scheduled runner. One GitHub write per >=24h, only if a meaningful status or
    trend bucket changed; confirmed recovery is written only after three green readings.
    Fail closed if pagination/auth/permission leaves duplicate lookup uncertain.
    """
    from . import issue_registry as ir
    state = options(home)
    if not state["auto_github"]:
        return {"status": "disabled"}

    # Caller-visible advice can never insert arbitrary strings into a public GitHub body.
    clean = [f for f in findings if f.get("metric") in _known_metrics()
             and f.get("class") in ("owner", "ci")
             and f.get("severity") in ("P2", "P3")
             and _numeric(f.get("median")) is not None]
    clean = sorted(clean, key=lambda f: (f["severity"], f["metric"]))[:32]
    active = sorted({f["metric"] for f in clean})
    recovered = not clean and bool(state["last_active"]) and _recovered(home, state["last_active"])
    if not clean and not recovered:
        return {"status": "awaiting_confirmation" if state["last_active"] else "no_finding"}
    signature = "recovered" if recovered else _signature(clean)
    if signature == state["last_signature"]:
        return {"status": "unchanged"}
    now = clock()
    if state["last_sent"] > 0 and now - state["last_sent"] < AUTO_COOLDOWN_S:
        return {"status": "cooldown"}

    token = (token_fn or ir.find_token)()
    if not token:
        return {"status": "no_token"}
    call = request or ir._http
    code, listing = call("GET", f"{ir.API_BASE}/repos/{ir.REPO}/issues?labels={ir.LABEL}&state=all&per_page=100", token)
    if code != 200 or not isinstance(listing, list) or len(listing) >= MAX_OPEN_PAGE:
        return {"status": "lookup_unavailable"}
    trackers = [x for x in listing if TRACKER_MARKER in str(x.get("body") or "")
                and not x.get("pull_request")]
    if len(trackers) > 1:
        return {"status": "duplicate_trackers"}
    body = _tracker_body(clean, recovered=recovered)
    if trackers:
        tracker = trackers[0]
        if tracker.get("state") == "closed":
            return {"status": "tracker_closed"}  # respect owner's decision; no silent reopen
        number = tracker.get("number")
        if not isinstance(number, int) or number < 1:
            return {"status": "invalid_tracker"}
        rc, out = call("PATCH", f"{ir.API_BASE}/repos/{ir.REPO}/issues/{number}", token, {"body": body})
        operation = "updated"
        url = str(tracker.get("html_url") or "")
    else:
        if recovered:
            return {"status": "no_tracker"}
        rc, out = call("POST", f"{ir.API_BASE}/repos/{ir.REPO}/issues", token,
                       {"title": TRACKER_TITLE, "body": body, "labels": [ir.LABEL]})
        operation = "filed"
        url = ""
    if rc not in (200, 201) or not isinstance(out, dict):
        return {"status": "send_failed", "http_status": rc}
    url = str(out.get("html_url") or url)
    if not url.startswith(f"https://github.com/{ir.REPO}/issues/"):
        return {"status": "invalid_response"}

    state.update(last_sent=now, last_signature=signature,
                 last_active=[] if recovered else active, tracker_url=url)
    _save_options(home, state)
    registry = ir.Registry(home)
    for f in clean:
        if f.get("issue_id"):
            registry.mark_reported(f["issue_id"], url=url)
    return {"status": operation, "issue_url": url, "metrics": active,
            "recovered": recovered}




def on_speedtest(home: Path, *, registry=None) -> list[dict]:
    """Called only after a completed, recorded speed test: never from an idle timer."""
    from . import logs
    try:
        findings = detect(home, registry=registry)
        result = auto_report(home, findings)
        if findings or result["status"] in ("updated", "filed"):
            logs.info("perf_advisor", "PERF_ADVICE", "performance recommendations ready",
                      count=len(findings), status=result["status"])
        return findings
    except Exception as exc:
        # Advisors must never turn a successful measured test into a failed run.
        logs.warn("perf_advisor", "PERF_ADVISOR_FAILED", "performance advisor unavailable",
                  error_type=type(exc).__name__)
        return []
