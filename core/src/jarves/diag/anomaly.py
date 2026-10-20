"""Baselines and anomaly detection over the monitor's time series (pure rules, no tokens).

- Baseline: per metric, the previous 14 days of hourly roll-ups; median and MAD (scaled to sigma) per hour-of-day
  bucket for metrics with a daily rhythm (CPU, latencies), one global bucket for the rest.
- Anomaly: the last N consecutive hourly values outside median +/- k*MAD (and beyond a relative floor, so a
  flat baseline cannot make noise an alarm).
- New log-code cluster: an ERROR/WARNING code seen in the window and never in the baseline.
- Rate step: CUSUM on hourly counts against the baseline rate, tuned to a 3x step.
- Trends: least-squares slope over 7 days (RAM creep in MB/h, state-folder growth in MB/day) with r^2 >= 0.5.
- Specific signals: slow-turn outliers (above 1.5x the baseline p99 and 2x the dead-air limit), one tool failing
  in 3 or more conversations, false-action claims, wake false-positive bursts above the J target, a canary
  journey failing twice in a row, red preflight items (e.g. Google token near expiry), configuration drift.
Findings reuse the V3 finding shape (selfcheck.Finding) and the accept / dismiss / snooze state.
Thresholds are tuned on the 30-day boring replay (tests/test_diag_anomaly.py): 0 high, at most 2 low findings.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime

from ..selfcheck import SEV, Finding
from . import db
from .metrics import code_counts, events, series

BASELINE_DAYS = 14
TREND_DAYS = 7
MAD_SIGMA = 1.4826
WAKE_FP_TARGET_PER_DAY = 5            # package J target: at most this many false wakes per day (tuned on the boring replay)
TOOL_FAIL_CONVERSATIONS = 3


@dataclass
class DiagFinding(Finding):
    source: str = "monitor"
    hints: list = field(default_factory=list)   # root-cause hints (logindex.hint dicts)
    codes: list = field(default_factory=list)   # log codes this finding is about
    score: float = 0.0                           # selection weight for the AI review


# metric -> rule. dir: up = only high values are bad. seasonal: hour-of-day buckets.
METRICS = {
    "cpu_pct": dict(k=6.0, n=3, seasonal=True, dir="up", floor=1.0, rel=1.0, sev="medium",
                    en="CPU use of the Core is unusually high", he="השימוש במעבד של ה-Core גבוה מהרגיל"),
    "loop_lag_ms": dict(k=6.0, n=3, seasonal=False, dir="up", floor=50.0, rel=1.0, sev="medium",
                        en="The Core's event loop is lagging", he="לולאת האירועים של ה-Core מתעכבת"),
    "handles": dict(k=6.0, n=3, seasonal=False, dir="up", floor=50.0, rel=0.3, sev="low",
                    en="The Core holds unusually many handles", he="ה-Core מחזיק יותר ידיות מהרגיל"),
    "threads": dict(k=6.0, n=3, seasonal=False, dir="up", floor=5.0, rel=0.3, sev="low",
                    en="The Core runs unusually many threads", he="ה-Core מריץ יותר תהליכונים מהרגיל"),
    "turn_wait_s": dict(k=6.0, n=3, seasonal=True, dir="up", floor=1.0, rel=0.5, sev="medium",
                        en="Replies take longer than usual", he="התשובות לוקחות יותר זמן מהרגיל"),
    "wake_to_first_audio_s": dict(k=6.0, n=3, seasonal=False, dir="up", floor=1.0, rel=0.5, sev="medium",
                                  en="He answers the wake word later than usual",
                                  he="הוא עונה למילת ההפעלה מאוחר מהרגיל"),
    "tool_ms": dict(k=6.0, n=3, seasonal=False, dir="up", floor=500.0, rel=1.0, sev="low",
                    en="Tools take longer than usual", he="הכלים לוקחים יותר זמן מהרגיל"),
    "scheduler_late_s": dict(k=6.0, n=3, seasonal=False, dir="up", floor=30.0, rel=1.0, sev="low",
                             en="Scheduled jobs start late", he="משימות מתוזמנות מתחילות באיחור"),
}
TRENDS = {
    "ram_mb": dict(per="h", limit=1.0, sev="medium", unit="MB/h",
                   en="Memory use keeps growing (possible leak)", he="השימוש בזיכרון ממשיך לגדול (ייתכן דליפה)",
                   fix="Restart JARVES once to free the memory; attach the ticket draft if it grows again.",
                   fix_he="הפעילו מחדש את ג'ארביס פעם אחת כדי לשחרר זיכרון. אם זה גדל שוב, צרפו את טיוטת הכרטיס."),
    "disk_state_mb": dict(per="d", limit=50.0, sev="low", unit="MB/day",
                          en="The JARVES state folder grows fast", he="תיקיית הנתונים של ג'ארביס גדלה מהר",
                          fix="Open the Backup and Costs pages: old backups, logs or caches may pile up.",
                          fix_he="פתחו את עמודי הגיבוי והעלויות: ייתכן שמצטברים גיבויים, יומנים או מטמון."),
}


# ---------------------------------------------------------------------- statistics
def median(v: list[float]) -> float:
    s = sorted(v)
    n = len(s)
    if not n:
        return 0.0
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def robust(v: list[float]) -> tuple[float, float]:
    """(median, MAD scaled to a standard deviation)."""
    m = median(v)
    return m, MAD_SIGMA * median([abs(x - m) for x in v])


def quantile(v: list[float], q: float) -> float:
    if not v:
        return 0.0
    s = sorted(v)
    k = (len(s) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def slope(points: list[tuple[float, float]]) -> tuple[float, float]:
    """Least-squares slope per second and r^2."""
    n = len(points)
    if n < 3:
        return 0.0, 0.0
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    sxx = sum((p[0] - mx) ** 2 for p in points)
    sxy = sum((p[0] - mx) * (p[1] - my) for p in points)
    syy = sum((p[1] - my) ** 2 for p in points)
    if sxx <= 0:
        return 0.0, 0.0
    b = sxy / sxx
    r2 = (sxy * sxy) / (sxx * syy) if syy > 0 else 0.0
    return b, r2


def cusum(counts: list[float], mu: float) -> tuple[float, int]:
    """One-sided CUSUM tuned to a 3x rate step: k = mu (half of the +2mu shift). (max statistic, start index)."""
    s, best, start, best_start = 0.0, 0.0, 0, 0
    k = max(mu, 0.05)
    for i, x in enumerate(counts):
        if s == 0.0:
            start = i
        s = max(0.0, s + x - mu - k)
        if s > best:
            best, best_start = s, start
    return best, best_start


def _hod(ts: float) -> int:
    return datetime.fromtimestamp(ts).hour


def _when(ts: float | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "?"


# ---------------------------------------------------------------------- the detector
class Detector:
    def __init__(self, store, *, clock=time.time, dead_air_s: float = 4.0, index=None) -> None:
        self.store, self.clock, self.dead_air_s = store, clock, float(dead_air_s)
        self.index = index                    # logindex.LogIndex (root-cause hints), optional

    def run(self, window_s: float = 86400.0, now: float | None = None) -> list[DiagFinding]:
        now = self.clock() if now is None else now
        w0 = now - window_s
        b0 = w0 - BASELINE_DAYS * 86400
        out: list[DiagFinding] = []
        for step in (self._metrics, self._trends, self._codes, self._slow_turns, self._tools, self._claims,
                     self._wake, self._canary, self._preflight, self._config):
            step(out, w0, b0, now)
        from .regression import version_findings
        out += version_findings(self.store, now=now)
        for f in out:
            f.score = SEV.get(f.severity, 1) * 2 - 1        # low 1, medium 3, high 5
            if self.index is not None and f.codes:
                f.hints = [h for h in (self.index.hint(c) for c in f.codes[:3]) if h]
        return out

    # ---- metrics: k*MAD for N consecutive hours
    def _metrics(self, out, w0, b0, now) -> None:
        for name, r in METRICS.items():
            base = series(self.store, name, b0, w0)
            win = series(self.store, name, w0, now + 1)
            if len(base) < 48 or len(win) < r["n"]:
                continue
            last = win[-r["n"]:]
            bad = []
            for ts, v in last:
                pool = [x for t, x in base if _hod(t) == _hod(ts)] if r["seasonal"] else [x for _, x in base]
                if len(pool) < 7:
                    pool = [x for _, x in base]
                m, s = robust(pool)
                lim = m + r["k"] * max(s, 1e-9)
                lim = max(lim, m + r["floor"], m * (1 + r["rel"]))
                bad.append(v > lim)
            if all(bad):
                m, s = robust([x for _, x in base])
                out.append(DiagFinding(
                    f"anomaly:metric:{name}", "anomaly", r["sev"], f"{r['en']}: {last[-1][1]:.1f} (usual {m:.1f})",
                    f"{r['he']}: {last[-1][1]:.1f} (בדרך כלל {m:.1f})",
                    detail=f"{name} was above its 14-day baseline (median {m:.2f}, spread {s:.2f}) for the last "
                           f"{r['n']} hours.",
                    detail_he=f"{name} היה מעל הבסיס של 14 הימים האחרונים ב-{r['n']} השעות האחרונות.",
                    evidence=[f"{_when(t)} {name}={v:.2f}" for t, v in last],
                    cause="a change in load, a stuck component or a regression", cause_he="שינוי בעומס, רכיב תקוע או נסיגה",
                    fix="Look at the Health page tiles and the System log for the same hours.",
                    fix_he="בדקו את אריחי עמוד התקינות ואת יומן המערכת באותן שעות.", effort="hour",
                    action={"type": "open", "page": "#/health"}, count=r["n"]))

    # ---- trends: 7-day slope
    def _trends(self, out, w0, b0, now) -> None:
        for name, r in TRENDS.items():
            pts = series(self.store, name, now - TREND_DAYS * 86400, now + 1)
            if len(pts) < 48:
                continue
            b, r2 = slope([(float(t), v) for t, v in pts])
            rate = b * (3600 if r["per"] == "h" else 86400)
            if rate >= r["limit"] and r2 >= 0.5:
                out.append(DiagFinding(
                    f"trend:{name}", "trend", r["sev"], f"{r['en']}: +{rate:.1f} {r['unit']} over 7 days",
                    f"{r['he']}: +{rate:.1f} {r['unit']} ב-7 ימים",
                    detail=f"{name} rose from {pts[0][1]:.0f} to {pts[-1][1]:.0f} (slope {rate:.2f} {r['unit']}, "
                           f"r2 {r2:.2f}).",
                    detail_he=f"{name} עלה מ-{pts[0][1]:.0f} ל-{pts[-1][1]:.0f}.",
                    evidence=[f"{_when(pts[0][0])} {pts[0][1]:.1f}", f"{_when(pts[-1][0])} {pts[-1][1]:.1f}"],
                    cause="something accumulates without being freed", cause_he="משהו מצטבר ולא משתחרר",
                    fix=r["fix"], fix_he=r["fix_he"], effort="hour", action={"type": "open", "page": "#/health"},
                    count=len(pts)))

    # ---- log codes: new clusters and rate steps
    def _codes(self, out, w0, b0, now) -> None:
        base = code_counts(self.store, b0, w0)
        win = code_counts(self.store, w0, now + 1)
        levels = {r["code"]: r["level"] for r in db.rows(
            self.store, "SELECT code, MAX(level) level FROM diag_codes WHERE ts >= ? AND level IN ('ERROR','WARNING') "
                        "GROUP BY code", (int(w0),))}
        hours_b = max(1.0, (w0 - b0) / 3600)
        skip = {"LIVE_FALSE_ACTION_CLAIM"}
        for code, per_hour in sorted(win.items()):
            if code in skip or code.startswith("HEALTH_"):
                continue
            n = sum(per_hour.values())
            nb = sum(base.get(code, {}).values())
            level = levels.get(code, "WARNING")
            if nb == 0:
                if n >= (2 if level == "ERROR" else 3):
                    out.append(DiagFinding(
                        f"anomaly:newcode:{code}", "logs", "medium" if level == "ERROR" else "low",
                        f"New {level.lower()} code {code}: {n}x (never seen in 14 days)",
                        f"קוד {'שגיאה' if level == 'ERROR' else 'אזהרה'} חדש {code}: {n} פעמים (לא נראה ב-14 ימים)",
                        detail=f"{code} appeared {n} time(s) in the window and never in the 14-day baseline.",
                        detail_he=f"{code} הופיע {n} פעמים ולא הופיע בכלל ב-14 הימים הקודמים.",
                        evidence=[f"{_when(t)} {code} x{c}" for t, c in sorted(per_hour.items())[-5:]],
                        cause="new behaviour: a change, an update or a new environment problem",
                        cause_he="התנהגות חדשה: שינוי, עדכון או בעיה חדשה בסביבה",
                        fix="Open the code's source location (hint) and the System log filtered on it.",
                        fix_he="פתחו את מיקום הקוד (הרמז) ואת יומן המערכת מסונן לקוד הזה.", effort="hour",
                        action={"type": "open", "page": "#/logs"}, count=n, codes=[code]))
                continue
            mu = nb / hours_b
            hours = sorted(per_hour)
            if not hours:
                continue
            span = [per_hour.get(h, 0) for h in range(int(w0 // 3600 * 3600), int(now) + 1, 3600)]
            stat, start = cusum([float(x) for x in span], mu)
            expected = mu * len(span)
            h = max(4.0, 4.0 * math.sqrt(max(mu, 0.05) * 24))
            if n >= 10 and n >= 2.5 * expected and stat >= h:
                ratio = n / max(expected, 1e-9)
                out.append(DiagFinding(
                    f"anomaly:step:{code}", "logs", "medium" if level == "ERROR" else "low",
                    f"{code} is {ratio:.1f}x more frequent than usual ({n} vs ~{expected:.0f})",
                    f"{code} תכוף פי {ratio:.1f} מהרגיל ({n} לעומת כ-{expected:.0f})",
                    detail=f"CUSUM rate step (statistic {stat:.1f} >= {h:.1f}); baseline {mu:.2f}/h.",
                    detail_he=f"קפיצה בקצב (CUSUM); הבסיס {mu:.2f} לשעה.",
                    evidence=[f"{_when(t)} {code} x{c}" for t, c in sorted(per_hour.items())[-5:]],
                    cause="a service or component started failing more often", cause_he="שירות או רכיב התחיל להיכשל יותר",
                    fix="Open the System log filtered on this code; compare with the version report.",
                    fix_he="פתחו את יומן המערכת מסונן לקוד הזה והשוו לדוח הגרסה.", effort="hour",
                    action={"type": "open", "page": "#/logs"}, count=n, codes=[code]))

    # ---- slow turns: top-1% outliers
    def _slow_turns(self, out, w0, b0, now) -> None:
        for name, metric, en, he in (("reply_wait", "turn", "Very slow replies", "תשובות איטיות מאוד"),
                                     ("wake_to_first_audio", "turn", "Very slow answers to the wake word",
                                      "תגובות איטיות מאוד למילת ההפעלה")):
            base = [e["value"] for e in events(self.store, metric, b0, w0) if e["name"] == name and e["value"] is not None]
            win = [e for e in events(self.store, metric, w0, now + 1) if e["name"] == name and e["value"] is not None]
            if len(base) < 50 or not win:
                continue
            thr = max(1.5 * quantile(base, 0.99), 2.0 * self.dead_air_s)
            slow = [e for e in win if e["value"] > thr]
            if len(slow) >= 3:
                out.append(DiagFinding(
                    f"anomaly:slow:{name}", "voice", "medium", f"{en}: {len(slow)} turns above {thr:.1f} s",
                    f"{he}: {len(slow)} תורות מעל {thr:.1f} שניות",
                    detail=f"{len(slow)} of {len(win)} turns were above 1.5x the 14-day p99 ({quantile(base, 0.99):.1f} s).",
                    detail_he=f"{len(slow)} מתוך {len(win)} תורות היו איטיים במיוחד.",
                    evidence=[f"{_when(e['ts'])} {name} {e['value']:.1f} s" for e in slow[-5:]],
                    cause="slow backend lookups, network or a stuck tool", cause_he="חיפושים איטיים, רשת או כלי תקוע",
                    fix="Open the slow conversations on the Conversations page.",
                    fix_he="פתחו את השיחות האיטיות בעמוד השיחות.", effort="hour",
                    action={"type": "open", "page": "#/conversations"}, count=len(slow)))

    # ---- one tool failing across conversations
    def _tools(self, out, w0, b0, now) -> None:
        by: dict[str, list[dict]] = {}
        for e in events(self.store, "tool_fail", w0, now + 1):
            by.setdefault(e["name"], []).append(e)
        for tool, es in sorted(by.items()):
            convs = {e["ref"] for e in es}
            if len(convs) >= TOOL_FAIL_CONVERSATIONS:
                out.append(DiagFinding(
                    f"anomaly:tool:{tool}", "tools", "high" if len(convs) >= 5 else "medium",
                    f"Tool {tool} failed in {len(convs)} conversations ({len(es)} calls)",
                    f"הכלי {tool} נכשל ב-{len(convs)} שיחות ({len(es)} קריאות)",
                    detail="The same tool returned ok=false in several separate conversations.",
                    detail_he="אותו כלי נכשל בכמה שיחות נפרדות.",
                    evidence=[f"{_when(e['ts'])} {tool} failed (conversation {e['ref']})" for e in es[-5:]],
                    cause="its service, a connection or a setting is broken", cause_he="השירות, החיבור או ההגדרה שלו שבורים",
                    fix="Check the feature's connection on the Features page; open a failing conversation.",
                    fix_he="בדקו את החיבור של היכולת בעמוד היכולות ופתחו שיחה שבה זה נכשל.", effort="hour",
                    action={"type": "open", "page": "#/features"}, count=len(es), codes=["TOOL_RESULT"]))

    # ---- false action claims
    def _claims(self, out, w0, b0, now) -> None:
        n = sum(sum(v.values()) for c, v in code_counts(self.store, w0, now + 1, levels=("WARNING", "INFO", "ERROR"))
                .items() if c == "LIVE_FALSE_ACTION_CLAIM")
        if n:
            out.append(DiagFinding(
                "anomaly:claim:LIVE_FALSE_ACTION_CLAIM", "voice", "medium",
                f"He claimed an action without doing it: {n}x", f"הוא טען שביצע פעולה בלי לבצע אותה: {n} פעמים",
                detail="LIVE_FALSE_ACTION_CLAIM: a reply said something was done but no tool ran in that turn.",
                detail_he="תשובה אמרה שמשהו בוצע, אבל שום כלי לא רץ באותו תור.",
                evidence=[f"LIVE_FALSE_ACTION_CLAIM x{n} in the window"],
                cause="the voice model skipped the tool call", cause_he="מודל הקול דילג על קריאת הכלי",
                fix="Open the conversation on the Conversations page; attach the ticket draft.",
                fix_he="פתחו את השיחה בעמוד השיחות וצרפו את טיוטת הכרטיס.", effort="hour",
                action={"type": "open", "page": "#/conversations"}, count=n, codes=["LIVE_FALSE_ACTION_CLAIM"]))

    # ---- wake false positives above the J target
    def _wake(self, out, w0, b0, now) -> None:
        lv = ("INFO", "WARNING")
        n = sum(code_counts(self.store, w0, now + 1, levels=lv).get("VOICE_NO_SPEECH", {}).values())
        nb = sum(code_counts(self.store, b0, w0, levels=lv).get("VOICE_NO_SPEECH", {}).values())
        days = max(1e-9, (now - w0) / 86400)
        per_day, base_day = n / days, nb / BASELINE_DAYS
        if per_day > WAKE_FP_TARGET_PER_DAY and per_day >= 3 * max(base_day, 0.5):
            out.append(DiagFinding(
                "anomaly:wake_false", "wake", "medium", f"False wakes: {per_day:.0f} per day (target <= {WAKE_FP_TARGET_PER_DAY})",
                f"התעוררויות לשווא: {per_day:.0f} ביום (היעד עד {WAKE_FP_TARGET_PER_DAY})",
                detail=f"{n} wakes with no speech in the window; the 14-day baseline is {base_day:.1f} per day.",
                detail_he=f"{n} התעוררויות בלי דיבור; הבסיס {base_day:.1f} ביום.",
                evidence=[f"VOICE_NO_SPEECH x{n} in the window"],
                cause="TV or similar words in the room, or a sensitive threshold / custom wake model",
                cause_he="טלוויזיה או מילים דומות בחדר, או סף רגיש / מודל הפעלה מותאם",
                fix="Calibrate: `jarvis wake test --apply`, or switch to the stock wake model (remediation).",
                fix_he="כיילו עם jarvis wake test --apply, או עברו למודל ההפעלה הרגיל (תיקון).", effort="minutes",
                action={"type": "remedy", "remedy": "fallback_stock_wake"}, count=n, codes=["VOICE_NO_SPEECH"]))

    # ---- canary journeys failing twice in a row
    def _canary(self, out, w0, b0, now) -> None:
        runs = db.rows(self.store, "SELECT * FROM diag_canary ORDER BY id DESC LIMIT 2")
        if len(runs) < 2:
            return
        r1, r2 = (db.j(r["results"], []) for r in runs)
        fail1 = {x["name"]: x for x in r1 if x.get("status") == "FAIL"}
        fail2 = {x["name"] for x in r2 if x.get("status") == "FAIL"}
        for name in sorted(set(fail1) & fail2):
            out.append(DiagFinding(
                f"canary:{name}", "canary", "high", f"Nightly canary {name} failed twice in a row",
                f"בדיקת הלילה {name} נכשלה פעמיים ברציפות",
                detail=f"Last error: {fail1[name].get('error', '?')}.", detail_he="נכשלה בשני הלילות האחרונים.",
                evidence=[f"{_when(runs[0]['ts'])} {name} FAIL", f"{_when(runs[1]['ts'])} {name} FAIL"],
                cause="that user journey is broken on this install", cause_he="המסלול הזה שבור בהתקנה הזאת",
                fix="Run `jarvis health canary --now` and open the journey's page.",
                fix_he="הריצו jarvis health canary --now ופתחו את העמוד של המסלול.", effort="hour",
                action={"type": "open", "page": "#/health"}, count=2, codes=[f"CANARY_{name.upper()}"]))

    # ---- red preflight items of the latest start
    def _preflight(self, out, w0, b0, now) -> None:
        r = db.rows(self.store, "SELECT * FROM diag_preflight ORDER BY id DESC LIMIT 1")
        if not r:
            return
        for it in db.j(r[0]["items"], []):
            if it.get("status") not in ("red", "amber"):
                continue
            out.append(DiagFinding(
                f"preflight:{it['id']}", "preflight", "high" if it.get("fatal") else
                ("medium" if it.get("status") == "red" else "low"),
                f"Start-up check: {it.get('title', it['id'])}: {it.get('detail', '')}"[:160],
                f"בדיקת הפעלה: {it.get('title_he', it['id'])}: {it.get('detail', '')}"[:160],
                detail=f"Preflight item {it['id']} at {_when(r[0]['ts'])}; component {it.get('component', '-')} "
                       f"{'degraded' if not it.get('fatal') else 'blocked the start'}.",
                detail_he=f"בדיקת הפעלה {it['id']}; הרכיב {it.get('component', '-')} פועל חלקית.",
                evidence=[f"{_when(r[0]['ts'])} preflight {it['id']}: {it.get('status')} - {it.get('detail', '')}"],
                cause=it.get("detail", ""), cause_he=it.get("detail", ""),
                fix=it.get("fix", ""), fix_he=it.get("fix_he", it.get("fix", "")), effort="minutes",
                action=it.get("action"), count=1, codes=[f"PREFLIGHT_{it['id'].upper()}"]))

    # ---- configuration drift (non-secret keys)
    def _config(self, out, w0, b0, now) -> None:
        snaps = db.rows(self.store, "SELECT * FROM diag_config WHERE ts < ? ORDER BY ts", (now + 1,))
        before = [s for s in snaps if s["ts"] < w0]
        inside = [s for s in snaps if s["ts"] >= w0]
        if not before or not inside:
            return
        old, new = db.j(before[-1]["snapshot"], {}), db.j(inside[-1]["snapshot"], {})
        changed = sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))
        if changed:
            out.append(DiagFinding(
                "config:drift", "config", "low", f"{len(changed)} setting(s) changed: {', '.join(changed[:4])}",
                f"{len(changed)} הגדרות השתנו: {', '.join(changed[:4])}",
                detail="Configuration changed in the window (non-secret keys only).",
                detail_he="ההגדרות השתנו (רק מפתחות לא סודיים).",
                evidence=[f"{k}: {old.get(k)!s:.40} -> {new.get(k)!s:.40}" for k in changed[:5]],
                cause="a settings change (yours, the wizard or an update)", cause_he="שינוי הגדרות (שלכם, האשף או עדכון)",
                fix="If a problem started at the same time, revert the setting on the Settings page.",
                fix_he="אם בעיה התחילה באותו זמן, החזירו את ההגדרה בעמוד ההגדרות.", effort="minutes",
                action={"type": "open", "page": "#/settings"}, count=len(changed)))


# ---------------------------------------------------------------------- persistence + state
def save(store, found: list[DiagFinding], now: float, source: str = "monitor") -> None:
    """Upsert the findings of one detection pass; a finding of this source not seen again becomes inactive."""
    db.ensure(store)
    seen = set()
    with store._lock:
        for f in found:
            seen.add(f.fingerprint)
            store._db.execute(
                "INSERT INTO diag_findings (fingerprint, source, category, severity, title, title_he, detail, "
                "detail_he, evidence, hints, cause, cause_he, fix, fix_he, effort, action, count, first_seen, "
                "last_seen, active) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1) ON CONFLICT(fingerprint) DO "
                "UPDATE SET severity=excluded.severity, title=excluded.title, title_he=excluded.title_he, "
                "detail=excluded.detail, detail_he=excluded.detail_he, evidence=excluded.evidence, "
                "hints=excluded.hints, cause=excluded.cause, cause_he=excluded.cause_he, fix=excluded.fix, "
                "fix_he=excluded.fix_he, effort=excluded.effort, action=excluded.action, count=excluded.count, "
                "last_seen=excluded.last_seen, active=1",
                (f.fingerprint, getattr(f, "source", source), f.category, f.severity, f.title, f.title_he, f.detail,
                 f.detail_he, db.dumps(f.evidence[:5]), db.dumps(getattr(f, "hints", [])), f.cause, f.cause_he,
                 f.fix, f.fix_he, f.effort, db.dumps(f.action) if f.action else None, f.count, now, now))
        old = [r[0] for r in store._db.execute("SELECT fingerprint FROM diag_findings WHERE active=1 AND source=?",
                                               (source,))]
        for fp in old:
            if fp not in seen:
                store._db.execute("UPDATE diag_findings SET active=0 WHERE fingerprint=?", (fp,))
        store._db.commit()


def findings(store, *, now: float | None = None, active_only: bool = True) -> list[dict]:
    """Ranked findings (state merged; an expired snooze is open again)."""
    now = time.time() if now is None else now
    out = []
    q = "SELECT * FROM diag_findings" + (" WHERE active=1" if active_only else "")
    for r in db.rows(store, q):
        d = dict(r)
        for k in ("evidence", "hints"):
            d[k] = db.j(d[k], [])
        d["action"] = db.j(d["action"])
        if d["state"] == "snoozed" and (d["until"] or 0) <= now:
            d["state"] = "open"
        out.append(d)
    from ..selfcheck import CATEGORY_WEIGHT
    out.sort(key=lambda d: (-SEV.get(d["severity"], 0), -CATEGORY_WEIGHT.get(d["category"], 3), -d["count"],
                            d["fingerprint"]))
    for i, d in enumerate(out, 1):
        d["rank"] = i
    return out


def decide(store, fingerprint: str, op: str, *, by: str, days: float = 7, now: float | None = None) -> dict:
    """accept | dismiss | snooze | reopen one monitor finding (accepting never applies anything)."""
    if op not in ("accept", "dismiss", "snooze", "reopen"):
        raise ValueError("op must be accept, dismiss, snooze or reopen")
    now = time.time() if now is None else now
    if not db.rows(store, "SELECT 1 FROM diag_findings WHERE fingerprint=?", (fingerprint,)):
        raise KeyError(fingerprint)
    state = {"accept": "accepted", "dismiss": "dismissed", "snooze": "snoozed", "reopen": "open"}[op]
    until = now + max(1.0, float(days)) * 86400 if op == "snooze" else None
    db.write(store, "UPDATE diag_findings SET state=?, until=?, decided_at=?, decided_by=? WHERE fingerprint=?",
             (state, until, now, by, fingerprint))
    return {"fingerprint": fingerprint, "state": state, "until": until}
