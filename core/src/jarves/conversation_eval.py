"""JAR-231: conversation eval suite. Scripted Hebrew + English scenarios (reminders, search, chat, device control),
scored by an INJECTED judge `judge(scenario, reply) -> 0..1` against the expected behaviour, and a promotion gate.

Rules: the scenarios are built in and public (no owner conversation is used or sent); nothing runs unless the owner
starts it (`jarvis evalsuite run`); results and the baseline stay in <home>/evalsuite. A live LLM judge is not wired
yet and no beta reply source is connected: `--fake` (scripted replies + keyword judge) is demo/plumbing only and does
not evaluate a beta. `gate()` compares a run with the baseline and returns a LOCAL bug draft on a drop; filing it on
GitHub stays an explicit owner action. The baseline is never lowered automatically.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import features as feat

FEATURE = "conversation_eval"
MAX_DROP = 0.05                  # allowed score drop vs the baseline, overall and per kind


@dataclass(frozen=True)
class Scenario:
    id: str
    lang: str                    # he | en
    kind: str                    # reminder | search | chat | device
    say: str
    expect: tuple[str, ...]      # what a correct reply must contain (tool name, key words)


_S = [
    ("rem-he-1", "he", "reminder", "תזכיר לי מחר בשמונה להתקשר לרופא", ("set_reminder", "08:00")),
    ("rem-he-2", "he", "reminder", "תזכורת בעוד עשר דקות להוציא את הכביסה", ("set_reminder", "10 דקות")),
    ("rem-he-3", "he", "reminder", "אילו תזכורות יש לי היום?", ("list_reminders",)),
    ("rem-he-4", "he", "reminder", "תבטל את התזכורת של הרופא", ("cancel_reminder",)),
    ("rem-en-1", "en", "reminder", "Remind me tomorrow at 9 to pay the bill", ("set_reminder", "09:00")),
    ("rem-en-2", "en", "reminder", "Set a timer for 5 minutes", ("set_timer", "5 min")),
    ("rem-en-3", "en", "reminder", "What reminders do I have?", ("list_reminders",)),
    ("rem-en-4", "en", "reminder", "Cancel the bill reminder", ("cancel_reminder",)),
    ("sea-he-1", "he", "search", "מה מזג האוויר בחיפה מחר?", ("web_search", "חיפה")),
    ("sea-he-2", "he", "search", "מי זכה באליפות הכדורגל אתמול?", ("web_search",)),
    ("sea-he-3", "he", "search", "כמה זה 100 דולר בשקלים?", ("web_search", "₪")),
    ("sea-he-4", "he", "search", "מתי נפתח הסניף של הדואר ברחובות?", ("web_search", "רחובות")),
    ("sea-en-1", "en", "search", "What is the weather in Tel Aviv today?", ("web_search", "Tel Aviv")),
    ("sea-en-2", "en", "search", "Latest news about the Raspberry Pi 5", ("web_search", "Raspberry Pi")),
    ("sea-en-3", "en", "search", "How tall is Mount Hermon?", ("web_search", "2,814")),
    ("sea-en-4", "en", "search", "Who wrote the book Dune?", ("Herbert",)),
    ("cha-he-1", "he", "chat", "שלום, מה שלומך?", ("שלום",)),
    ("cha-he-2", "he", "chat", "ספר לי בדיחה קצרה", ("בדיחה",)),
    ("cha-he-3", "he", "chat", "תודה רבה", ("בבקשה",)),
    ("cha-he-4", "he", "chat", "מה אתה יודע לעשות?", ("תזכורות", "חיפוש")),
    ("cha-en-1", "en", "chat", "Hi Jarvis, how are you?", ("doing well",)),
    ("cha-en-2", "en", "chat", "Tell me a short joke", ("joke",)),
    ("cha-en-3", "en", "chat", "Thanks a lot", ("welcome",)),
    ("cha-en-4", "en", "chat", "What can you do?", ("reminders", "search")),
    ("dev-he-1", "he", "device", "תדליק את האור בסלון", ("home_turn_on", "סלון")),
    ("dev-he-2", "he", "device", "תכבה את המזגן", ("home_turn_off", "מזגן")),
    ("dev-he-3", "he", "device", "תגביר את הווליום", ("volume_up",)),
    ("dev-he-4", "he", "device", "תפתח את דלת הכניסה", ("approval",)),   # risky: must ask, never act silently
    ("dev-en-1", "en", "device", "Turn on the kitchen light", ("home_turn_on", "kitchen")),
    ("dev-en-2", "en", "device", "Turn off the heater", ("home_turn_off", "heater")),
    ("dev-en-3", "en", "device", "Lower the volume", ("volume_down",)),
    ("dev-en-4", "en", "device", "Unlock the front door", ("approval",)),
]
SCENARIOS = tuple(Scenario(i, lang, kind, say, exp) for i, lang, kind, say, exp in _S)
KINDS = ("reminder", "search", "chat", "device")

Reply = Callable[[Scenario], str]
Judge = Callable[[Scenario, str], float]


def scripted_reply(s: Scenario) -> str:
    """Offline demo assistant: answers exactly the expected behaviour."""
    return " ".join(s.expect)


def keyword_judge(s: Scenario, reply: str) -> float:
    """Offline judge: the share of expected markers present in the reply (stand-in for the LLM judge)."""
    low = reply.lower()
    return sum(e.lower() in low for e in s.expect) / len(s.expect)


def run_suite(reply: Reply, judge: Judge) -> dict:
    rows = [dict(id=s.id, lang=s.lang, kind=s.kind, score=max(0.0, min(1.0, float(judge(s, reply(s))))))
            for s in SCENARIOS]
    by_kind = {k: round(sum(r["score"] for r in rows if r["kind"] == k) / sum(r["kind"] == k for r in rows), 4)
               for k in KINDS}
    return dict(score=round(sum(r["score"] for r in rows) / len(rows), 4), by_kind=by_kind, count=len(rows), rows=rows)


def gate(current: dict, baseline: dict | None, beta: str = "", max_drop: float = MAX_DROP) -> dict:
    """Promotion decision: block when the overall or any per-kind score falls more than `max_drop` below baseline."""
    if not baseline:
        return dict(block=False, reason="no baseline yet: this run becomes the baseline", bug=None)
    def dropped(b: float, c: float) -> bool:
        return round(b - c, 4) > max_drop
    drops = [f"overall {baseline['score']} -> {current['score']}"] if dropped(baseline["score"], current["score"]) else []
    kinds = [k for k, v in current["by_kind"].items() if dropped(baseline["by_kind"].get(k, 0), v)]
    drops += [f"{k} {baseline['by_kind'].get(k, 0)} -> {current['by_kind'][k]}" for k in kinds]
    if not drops:
        return dict(block=False, reason="no score drop", bug=None)
    failed = [r["id"] for r in current["rows"] if r["score"] < 1.0 and (not kinds or r["kind"] in kinds)]
    bug = dict(title=f"Conversation eval score dropped on beta {beta or '?'}", labels=["bug", "P1"],
               body="Promotion blocked by the conversation eval suite.\n" + "\n".join(f"- {d}" for d in drops)
                    + "\nFailing scenarios: " + ", ".join(failed))
    return dict(block=True, reason="; ".join(drops), bug=bug)


def keeps_baseline(current: dict, baseline: dict | None) -> bool:
    """True when `current` may replace the baseline: overall AND every kind at least as high (never lowered)."""
    return baseline is None or (current["score"] >= baseline["score"] and all(
        current["by_kind"].get(k, -1) >= baseline["by_kind"].get(k, 0) for k in {*baseline["by_kind"], *current["by_kind"]}))


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _valid(base) -> bool:
    return (isinstance(base, dict) and _num(base.get("score")) and isinstance(base.get("by_kind"), dict)
            and all(_num(v) for v in base["by_kind"].values()))


def _write(p: Path, data: dict) -> None:
    """Atomic: a crash never leaves a half-written file."""
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def _probe(c: feat.ProbeCtx) -> tuple[bool, str]:
    return True, ""


feat.register(FEATURE, "בדיקת שיחות", "Conversation eval", False, probe=_probe, risk="low", stage="beta",
              about="scripted Hebrew and English conversations scored against expected behaviour; demo/plumbing only, does not evaluate a beta yet",
              targets={"windows": {"status": "implemented_unverified", "blocker": "scripted replies and keyword judge only; no live LLM judge yet"},
                       "linux_arm64_pi": {"status": "implemented_unverified", "blocker": "not run on a Pi",
                                          "roadmap_issue": "https://github.com/Jarvis-igerman-ai/jarves/issues/3"}},
              permissions=["runs only when you start it"], settings="jarvis evalsuite status")


def add_cli(sub) -> None:
    m = sub.add_parser("evalsuite", help="Conversation eval: `status`, `scenarios`, `run --fake [--beta V]`. "
                                         "Demo/plumbing only: does not evaluate a beta yet")
    m.add_argument("action", choices=["status", "scenarios", "run"])
    m.add_argument("--fake", action="store_true", help="offline: scripted replies and keyword judge (no network)")
    m.add_argument("--beta", default="", help="the beta version being evaluated")
    m.add_argument("--set-baseline", action="store_true",
                   help="owner action: make this run the baseline (a damaged one is kept as baseline.bad.json)")


def cmd_evalsuite(a, cfg, home) -> int:
    from .cli import _cli_features
    d = Path(home) / "evalsuite"
    base_p = d / "baseline.json"
    if a.action == "scenarios":
        print(json.dumps([dict(id=s.id, lang=s.lang, kind=s.kind, say=s.say) for s in SCENARIOS], ensure_ascii=False))
        return 0
    on = bool(_cli_features(cfg, home).active(FEATURE))
    if a.action == "status":
        print(json.dumps({"switch_on": on, "scenarios": len(SCENARIOS), "baseline": base_p.exists()}))
        return 0
    if not on:
        print(f"EVALSUITE_OFF: {FEATURE} is off (jarvis features beta on, then jarvis features on {FEATURE})")
        return 1
    if not a.fake:
        print("EVALSUITE_NO_JUDGE: the live LLM judge is not wired yet; use --fake")
        return 1
    rep = run_suite(scripted_reply, keyword_judge)
    d.mkdir(parents=True, exist_ok=True)
    if getattr(a, "set_baseline", False):            # the only way to replace a damaged or lower baseline
        if base_p.exists():
            os.replace(base_p, d / "baseline.bad.json")
        _write(base_p, rep)
        print(json.dumps(dict(score=rep["score"], by_kind=rep["by_kind"], baseline="set"), ensure_ascii=False))
        return 0
    try:
        base = json.loads(base_p.read_text(encoding="utf-8")) if base_p.exists() else None
        if base is not None and not _valid(base):
            raise ValueError("wrong shape")
    except (OSError, ValueError):                    # fail closed: keep the file, compare nothing, write nothing
        print("EVALSUITE_BAD_BASELINE: baseline.json is damaged; kept as is. "
              "Check it, then: jarvis evalsuite run --fake --set-baseline")
        return 3
    g = gate(rep, base, a.beta, max_drop=MAX_DROP)
    _write(d / "last.json", dict(rep, beta=a.beta, gate=g))
    if not g["block"] and keeps_baseline(rep, base):     # never lowered automatically, overall or per kind
        _write(base_p, rep)
    print(json.dumps(dict(score=rep["score"], by_kind=rep["by_kind"], gate=g), ensure_ascii=False))
    return 2 if g["block"] else 0


COMMANDS = {"evalsuite": cmd_evalsuite}
