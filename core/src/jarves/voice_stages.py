"""Voice reply latency by stage (issue #122, software part): median / p95 per stage, the true bottleneck and a confidence.

Reads only the existing LIVE_LATENCY log records (numbers and stage names, no transcripts, no IDs): no new timers, no
network, no paid call, no microphone. Stages the live backend does not expose (wake to ASR, ASR to model, TTS first
audio, barge-in) are reported as explicitly unavailable, never guessed. The numbers are only real-device evidence when
they come from a real voice session on the target device; this module never claims that.
"""
from __future__ import annotations

import math
import time
from pathlib import Path

from .speedtest import percentile

CODE = "LIVE_LATENCY"
DAYS = 14
MIN_SAMPLES = 5          # fewer samples: shown, but confidence "low" and no bottleneck verdict
GOOD_SAMPLES = 20
MAX_STAGE_S = 600.0      # a corrupt or absurd value is ignored
STAGE_KEYS = ("to_delegation", "delegation_to_backend", "backend", "backend_to_audio", "delegation_to_audio",
              "model_direct")
# Boundaries that the current backend does not time. Listed so the UI and CLI say so instead of showing nothing.
UNAVAILABLE = {
    "wake_to_asr": "the live backend does not expose it",
    "asr_to_model": "the live backend does not expose it",
    "tts_first_audio": "only the combined reply-to-first-audio time is measured",
    "barge_in": "no stage marker yet",
}


def _num(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    return v if math.isfinite(v) and 0.0 <= v <= MAX_STAGE_S else None


def confidence(n: int) -> str:
    return "high" if n >= GOOD_SAMPLES else "medium" if n >= MIN_SAMPLES else "low"


def summarize(records: list[dict], *, since: float = 0.0) -> dict:
    """Pure: LIVE_LATENCY records in, per-stage statistics out."""
    totals: list[float] = []
    stages: dict[str, list[float]] = {}
    for rec in records:
        if rec.get("code") != CODE:
            continue
        ts = rec.get("_ts")
        if isinstance(ts, (int, float)) and ts < since:
            continue
        total = _num(rec.get("total_s"))
        if total is None:
            continue
        totals.append(total)
        for key in STAGE_KEYS:
            v = _num(rec.get(f"{key}_s"))
            if v is not None:
                stages.setdefault(key, []).append(v)
    n = len(totals)

    def stat(vals: list[float]) -> dict:
        return {"samples": len(vals), "median_s": round(percentile(vals, 50), 2),
                "p95_s": round(percentile(vals, 95), 2)}

    out: dict = {"samples": n, "confidence": confidence(n), "total": stat(totals) if n else None,
                 "stages": {k: stat(v) for k, v in stages.items()}, "bottleneck": None,
                 "unavailable": dict(UNAVAILABLE), "real_device": "UNVERIFIED"}
    if n >= MIN_SAMPLES and out["stages"]:
        # Largest median among stages seen in at least half of the replies.
        common = {k: v for k, v in out["stages"].items() if v["samples"] * 2 >= n}
        pool = common or out["stages"]
        out["bottleneck"] = max(pool, key=lambda k: pool[k]["median_s"])
    return out


def run(home: Path, *, days: int = DAYS, now: float | None = None) -> dict:
    from .logview import LogReader
    since = (time.time() if now is None else now) - days * 86400
    res = summarize(LogReader(Path(home)).records(), since=since)
    res["days"] = days
    return res


def render(res: dict) -> list[str]:
    lines = [f"voice reply latency by stage (last {res.get('days', DAYS)} days): {res['samples']} replies, "
             f"confidence {res['confidence']}"]
    if not res["samples"]:
        lines.append("  no measured replies yet; this fills in after real voice conversations.")
    else:
        t = res["total"]
        lines.append(f"  total (end of speech to first audio): median {t['median_s']} s, p95 {t['p95_s']} s")
        for name, s in res["stages"].items():
            mark = "  <- bottleneck" if name == res["bottleneck"] else ""
            lines.append(f"  {name:<22} median {s['median_s']} s  p95 {s['p95_s']} s  (n={s['samples']}){mark}")
        if res["bottleneck"] is None:
            lines.append(f"  no bottleneck verdict below {MIN_SAMPLES} replies.")
    for name, why in res["unavailable"].items():
        lines.append(f"  {name:<22} unavailable: {why}")
    lines.append("  real device / voice acceptance: UNVERIFIED (owner test on the target device)")
    return lines
