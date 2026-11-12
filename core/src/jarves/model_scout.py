"""JAR-233: monthly best-in-class scout. Compares the recorded voice models and agents SDK with official public pages
and writes idea cards (stage, recorded, candidate, source, date checked, measured reason) for the Inventor/council.

* Runs only when the owner starts it (`jarvis scout run`); `status` says when a month has passed. No timer.
* Fetches only the fixed public URLs in WATCH with a plain GET: no key, no cookie, no query, nothing personal.
* A reason to switch is "measured" only when a Model Lab report holds quality/latency/cost for the candidate;
  otherwise the card says to measure first. A card is an idea: switching stays an owner decision.
Default OFF (feature `model_scout`, beta).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import features as feat
from .model_lab import latest

FEATURE = "model_scout"
INTERVAL_DAYS = 30
MAX_PAGE = 2_000_000
_V = r"(\d+(?:\.\d+)*)"


@dataclass(frozen=True)
class Watch:
    stage: str
    component: str
    url: str
    pattern: str                 # group 1 = version
    label: str                   # candidate name, "{}" = version


MODELS = "https://developers.openai.com/api/docs/models"
WATCH = (
    Watch("voice", "live_model", MODELS, r"gpt-live-" + _V + r"(?![\w.-])", "gpt-live-{}"),
    Watch("voice", "realtime_model", MODELS, r"gpt-realtime-" + _V + r"(?![\w.-])", "gpt-realtime-{}"),
    Watch("agents", "openai-agents", "https://pypi.org/pypi/openai-agents/json", r'"version":\s*"' + _V + '"',
          "openai-agents {}"),
)


def _ver(text: str) -> tuple[int, ...]:
    found = re.findall(_V, text or "")
    return tuple(int(x) for x in found[-1].split(".")) if found else ()


def recorded(cfg) -> dict[str, str]:
    from importlib.metadata import PackageNotFoundError, version
    try:
        agents = version("openai-agents")
    except PackageNotFoundError:
        agents = ""
    return {"live_model": cfg.voice.live_model, "realtime_model": cfg.voice.realtime_model, "openai-agents": agents}


def _live_fetch(url: str) -> str:
    import httpx
    r = httpx.get(url, timeout=15, follow_redirects=True, headers={"User-Agent": "jarves-scout"})
    r.raise_for_status()
    return r.text[:MAX_PAGE]


def _measured(home: Path, candidate: str) -> dict | None:
    try:
        rep = latest(home) or {}
    except (OSError, ValueError):
        return None
    for row in rep.get("summary") or []:
        if row.get("model") == candidate and row.get("quality") is not None:
            return {k: row.get(k) for k in ("quality", "avg_latency_s", "cost_usd")}
    return None


def scout(home: Path, rec: dict[str, str], fetch, *, today: date) -> dict:
    home, pages, cards, errors = Path(home), {}, [], []
    for w in WATCH:
        try:
            page = pages[w.url] if w.url in pages else pages.setdefault(w.url, fetch(w.url))
        except Exception as e:                 # noqa: BLE001 - a source down is reported, never invented
            pages[w.url] = None
            errors.append({"component": w.component, "source": w.url, "error": type(e).__name__})
            continue
        if page is None:
            errors.append({"component": w.component, "source": w.url, "error": "unavailable"})
            continue
        best = max((tuple(int(x) for x in v.split(".")) for v in re.findall(w.pattern, page)), default=())
        if not best or best <= _ver(rec.get(w.component, "")):
            continue
        cand = w.label.format(".".join(map(str, best)))
        reason = _measured(home, cand)
        cards.append({"stage": w.stage, "component": w.component, "recorded": rec.get(w.component, ""),
                      "candidate": cand, "source": w.url, "checked": today.isoformat(), "measured": bool(reason),
                      "reason": reason, "decision": "owner", "for": "inventor/council",
                      "next": "compare the measured reason with the recorded one" if reason else
                      f"measure first: jarvis modellab run --models {cand}"})
    out = {"checked": today.isoformat(), "cards": cards, "errors": errors}
    d = home / "scout"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{today:%Y-%m}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def _last(home: Path) -> dict | None:
    files = sorted((Path(home) / "scout").glob("*.json"))
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


def status(home: Path, *, today: date) -> dict:
    last = _last(home)
    when = date.fromisoformat(last["checked"]) if last else None
    return {"last_checked": last["checked"] if last else None, "cards": len(last["cards"]) if last else 0,
            "due": when is None or (today - when).days >= INTERVAL_DAYS}


feat.register(FEATURE, "סייר הטכנולוגיה החודשי", "Monthly best-in-class scout", False, risk="low", stage="beta",
              about="once a month, when you start it, checks official public pages for newer voice models and agent libraries than the recorded ones and writes idea cards; nothing personal is sent",
              targets={"windows": {"status": "implemented_unverified", "blocker": "tested with a fake fetch only"},
                       "linux_arm64_pi": {"status": "implemented_unverified", "blocker": "not run on a Pi",
                                          "roadmap_issue": "https://github.com/Jarvis-igerman-ai/jarves/issues/3"}},
              permissions=["runs only when you start it", "reads public web pages"], settings="jarvis scout status")


def add_cli(sub) -> None:
    m = sub.add_parser("scout", help="Monthly best-in-class scout: `status`, `run`, `cards`. Runs only when you start it")
    m.add_argument("action", choices=["status", "run", "cards"])


def cmd_scout(a, cfg, home) -> int:
    from .cli import _cli_features
    home, today = Path(home), date.today()
    if a.action == "status":
        print(json.dumps(status(home, today=today), ensure_ascii=False))
        return 0
    if a.action == "cards":
        last = _last(home)
        print(json.dumps(last["cards"] if last else [], ensure_ascii=False, indent=1))
        return 0
    if not _cli_features(cfg, home).active(FEATURE):
        print(f"SCOUT_OFF: {FEATURE} is off (jarvis features beta on, then jarvis features on {FEATURE})")
        return 1
    from .config import load_config
    out = scout(home, recorded(cfg or load_config()[0]), _live_fetch, today=today)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


COMMANDS = {"scout": cmd_scout}
