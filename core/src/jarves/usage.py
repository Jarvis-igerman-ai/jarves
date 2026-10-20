"""Usage monitor (SW-V2 M5, spec section 10, decisions D10 + D13): ChatGPT plan and OpenAI API meters.

Every meter says WHERE its number comes from and how sure it is (category):
- provider_authoritative  a number the provider returned to JARVES (Costs API, rate-limit response headers, the
                          plan route's own "usage limit reached" error).
- app_local_counter       JARVES's own counter (Budget day counters, store.db): exact for THIS install only, never
                          the whole account.
- estimate                computed by JARVES (voice minutes and tokens x [pricing]); the OpenAI bill is the truth.
- unavailable             the provider does not tell JARVES. Shown as "Unknown", never as a made-up percent.

Metrics are never mixed: tokens, requests, realtime minutes, money (USD) and plan quota (percent) are separate
meters. A percent exists only when BOTH used and a real limit are known; thresholds (default 75/90/100 %) fire once
per meter per window (usage_alerts table, persisted across reboot; several crossed at once -> one alert for the
highest). A window changes only on real evidence (new local day / month, a provider-reported reset that has
passed); JARVES never invents a reset.

What the providers expose (checked 2026-10-19):
- OpenAI API: x-ratelimit-{limit,remaining,reset}-{requests,tokens} (+ -project-tokens) response headers, i.e.
  per-minute RATE limits, not spend (developers.openai.com/api/docs/guides/rate-limits). Month spend: the Costs API
  `GET /v1/organization/costs` needs an Admin API key (OPENAI_ADMIN_KEY; read-only use, free, no model call;
  developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/costs).
  The project's monthly budget / hard limit is NOT exposed by any API: the user may enter his own budget here.
  Realtime sessions send `rate_limits.updated` events (observe_realtime_rate_limits).
- ChatGPT plan (Sign in with ChatGPT, subscription route): no documented quota/remaining/reset API. JARVES reads
  only what its own requests get back: the `subscription_sharing_usage_limit_exceeded` error (limit reached) and,
  if the route ever returns them, rate-limit headers (x-ratelimit-*, Codex-style x-codex-primary-* / -secondary-*,
  undocumented -> confidence medium). No scraping of chatgpt.com, no hidden endpoints.
Never: an automatic switch to the billable API when the plan quota ends (chatgpt_brain pauses instead; tested).

Interface (stable):
    usage.attach(home)                              # Core start; observe_* write to <home>/memory.db
    usage.observe_headers("chatgpt_plan" | "openai_api", headers)   # after a provider HTTP response
    usage.observe_realtime_rate_limits(event["rate_limits"])        # GPT-Live / Realtime server event
    m = usage.Monitor(home, cfg); m.meters(); m.check_alerts(); m.footer(); m.summary_he()
    usage.ALERT_SINK = fn(alert_dict)               # default: jarves.alerts.emit(alert) if WP-M3's module exists
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from . import locales, logs

CATEGORIES = ("provider_authoritative", "app_local_counter", "estimate", "unavailable")
UNKNOWN = "Unknown"
DEFAULT_THRESHOLDS = (75, 90, 100)
COSTS_URL = "https://api.openai.com/v1/organization/costs"
ADMIN_ENV = "OPENAI_ADMIN_KEY"
COSTS_REFRESH_S = 3600          # Costs API at most hourly (it reports daily buckets anyway)
RATE_FRESH_S = 900              # a rate-limit observation older than this is shown as stale
PLAN_LIMIT_CODE = "subscription_sharing_usage_limit_exceeded"

_NOTES = ("Unknown means the provider does not report it; JARVES never guesses a percent.",
          "Tokens, requests, voice minutes, dollars and plan quota are tracked separately.",
          "When the ChatGPT plan limit is reached JARVES pauses; it never switches to the paid API by itself.")

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_obs (key TEXT PRIMARY KEY, data TEXT NOT NULL, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS usage_alerts (meter TEXT NOT NULL, window TEXT NOT NULL, threshold INTEGER NOT NULL,
                                         ts REAL NOT NULL, percent REAL, PRIMARY KEY (meter, window, threshold));
"""


# ---------------------------------------------------------------------- meter
@dataclass
class Meter:
    id: str
    provider: str                    # chatgpt_plan | openai_api | jarves_local
    title: str
    metric: str                      # what is counted, in words
    unit: str                        # percent | requests | tokens | minutes | usd
    category: str
    source: str                      # where the number came from
    used: float | None = None
    limit: float | None = None
    limit_source: str = ""           # provider | your setting | JARVES daily cap
    window: str | None = None        # human words: "today (local)", "calendar month (UTC)", "5 h"...
    window_key: str | None = None    # identifies the current window for alert de-duplication
    reset_at: float | None = None    # epoch seconds, only when known
    last_verified: float | None = None
    confidence: str = "none"         # high | medium | low | none
    scope: str = "this_install"      # account | organization | project | this_install
    note: str = ""
    alerts: bool = True              # rate limits that reset every minute never alert (they would storm)
    state: str = "ok"                # ok | limit_reached | stale | unknown

    def __post_init__(self) -> None:
        if self.category not in CATEGORIES:
            raise ValueError(f"category must be one of {CATEGORIES}")

    def percent(self) -> float | None:
        """Only from a known used AND a known, positive limit; never for an unavailable meter."""
        if self.category == "unavailable" or self.used is None or not self.limit or self.limit <= 0:
            return None
        return round(100.0 * self.used / self.limit, 1)

    def to_dict(self) -> dict:
        d = asdict(self)
        pct = self.percent()
        d["percent"] = pct
        d["used_text"] = UNKNOWN if self.used is None else _fmt(self.used, self.unit)
        d["limit_text"] = UNKNOWN if self.limit is None else _fmt(self.limit, self.unit)
        d["percent_text"] = UNKNOWN if pct is None else f"{pct:g}%"
        d["window_text"] = self.window or UNKNOWN
        locales.attach(d, "title", "metric", "source", "window", "limit_source", "note")
        d["reset_text"] = UNKNOWN if self.reset_at is None else datetime.fromtimestamp(self.reset_at).isoformat(
            sep=" ", timespec="minutes")
        return d


def _fmt(v: float, unit: str) -> str:
    if unit == "usd":
        return f"${v:.2f}" if v >= 1 else f"${v:.3f}"
    if unit == "percent":
        return f"{v:g}%"
    if unit == "minutes":
        return f"{v:.1f} min"
    return f"{int(v)} {unit}" if float(v).is_integer() else f"{v:.1f} {unit}"


# ---------------------------------------------------------------------- observations (cross-process, memory.db)
_home: Path | None = None
_lock = threading.Lock()


def attach(home: Path | None) -> None:
    """Where observe_* write (the Core's data dir). None = observations are dropped (tests, tools)."""
    global _home
    _home = Path(home) if home is not None else None


def _connect(home: Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(Path(home) / "memory.db"), timeout=10, isolation_level=None, check_same_thread=False)
    db.executescript(SCHEMA)
    return db


def _put_obs(home: Path | None, key: str, data: dict, ts: float | None = None) -> None:
    if home is None:
        return
    try:
        with _lock:
            db = _connect(home)
            try:
                db.execute("INSERT INTO usage_obs (key, data, ts) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                           "data = excluded.data, ts = excluded.ts", (key, json.dumps(data), ts or time.time()))
            finally:
                db.close()
    except sqlite3.Error as e:            # usage must never break a provider call
        logs.warn("usage", "USAGE_OBS_FAILED", "could not store a usage observation", error=type(e).__name__)


def _get_obs(db: sqlite3.Connection, key: str) -> tuple[dict, float] | None:
    r = db.execute("SELECT data, ts FROM usage_obs WHERE key = ?", (key,)).fetchone()
    if not r:
        return None
    try:
        return json.loads(r[0]), float(r[1])
    except ValueError:
        return None


def _duration_s(v: str | None) -> float | None:
    """OpenAI reset values: "1s", "6m0s", "20ms", "1h2m3s" or plain seconds."""
    if v is None:
        return None
    s = str(v).strip()
    try:
        return float(s)
    except ValueError:
        pass
    total, num = 0.0, ""
    i = 0
    while i < len(s):
        ch = s[i]
        if ch.isdigit() or ch == ".":
            num += ch
        else:
            unit = "ms" if s[i:i + 2] == "ms" else ch
            i += len(unit) - 1
            mult = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit)
            if mult is None or not num:
                return None
            total += float(num) * mult
            num = ""
        i += 1
    return total if not num else None


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_headers(headers) -> dict:
    """Only the documented/known numeric rate-limit headers; never stores raw headers (no ids, no cookies)."""
    h = {str(k).lower(): str(v) for k, v in dict(headers or {}).items()}
    out: dict = {}
    for kind in ("requests", "tokens", "project-tokens"):
        lim, rem = _num(h.get(f"x-ratelimit-limit-{kind}")), _num(h.get(f"x-ratelimit-remaining-{kind}"))
        if lim is not None and rem is not None:
            out[kind] = {"limit": lim, "remaining": rem, "reset_s": _duration_s(h.get(f"x-ratelimit-reset-{kind}"))}
    for w in ("primary", "secondary"):              # Codex-style plan windows (undocumented: confidence medium)
        pct = _num(h.get(f"x-codex-{w}-used-percent"))
        if pct is not None:
            reset_s = _num(h.get(f"x-codex-{w}-reset-after-seconds"))
            reset_at = _num(h.get(f"x-codex-{w}-reset-at"))
            out[f"plan_{w}"] = {"used_percent": pct, "window_minutes": _num(h.get(f"x-codex-{w}-window-minutes")),
                                "reset_s": reset_s, "reset_at": reset_at}
    return out


def observe_headers(provider: str, headers, home: Path | None = None) -> dict:
    """Call after a provider HTTP response (any status). Cheap; no-op without rate-limit headers."""
    parsed = parse_headers(headers)
    if parsed:
        now = time.time()
        for v in parsed.values():
            if v.get("reset_s") is not None and not v.get("reset_at"):
                v["reset_at"] = now + v["reset_s"]
        _put_obs(home or _home, f"{provider}.headers", parsed, now)
    return parsed


def observe_realtime_rate_limits(rate_limits, home: Path | None = None) -> None:
    """Realtime/GPT-Live `rate_limits.updated`: [{name, limit, remaining, reset_seconds}, ...]."""
    out, now = {}, time.time()
    for r in rate_limits or []:
        if not isinstance(r, dict):
            continue
        name, lim, rem = str(r.get("name") or ""), _num(r.get("limit")), _num(r.get("remaining"))
        if name in ("requests", "tokens") and lim is not None and rem is not None:
            rs = _num(r.get("reset_seconds"))
            out[name] = {"limit": lim, "remaining": rem, "reset_s": rs, "reset_at": now + rs if rs is not None else None}
    if out:
        _put_obs(home or _home, "openai_api.realtime", out, now)


# ---------------------------------------------------------------------- settings (memory.db settings table)
SETTINGS_DEFAULTS = {"thresholds": list(DEFAULT_THRESHOLDS), "api_monthly_budget_usd": None, "costs_api": "auto"}


def get_settings(home: Path) -> dict:
    out = dict(SETTINGS_DEFAULTS)
    try:
        db = _connect(home)
        try:
            db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            for k, v in db.execute("SELECT key, value FROM settings WHERE key LIKE 'usage.%'"):
                name = k.removeprefix("usage.")
                if name in out:
                    out[name] = json.loads(v)
        finally:
            db.close()
    except (sqlite3.Error, ValueError):
        pass
    return out


def validate_settings(changes: dict) -> dict:
    clean: dict = {}
    for k, v in (changes or {}).items():
        if k == "thresholds":
            if isinstance(v, str):
                v = [x for x in v.replace(" ", "").split(",") if x]
            try:
                th = sorted({int(x) for x in v})
            except (TypeError, ValueError):
                raise ValueError("thresholds must be whole percents, e.g. 75,90,100")
            if not th or len(th) > 6 or th[0] < 1 or th[-1] > 100:
                raise ValueError("thresholds: 1 to 6 values between 1 and 100")
            clean[k] = th
        elif k == "api_monthly_budget_usd":
            if v in (None, "", 0, "0", "none", "off"):
                clean[k] = None
            else:
                b = _num(v)
                if b is None or b <= 0 or b > 100000:
                    raise ValueError("api_monthly_budget_usd must be a positive number of dollars (or empty)")
                clean[k] = round(b, 2)
        elif k == "costs_api":
            if v not in ("auto", "on", "off"):
                raise ValueError("costs_api must be auto, on or off")
            clean[k] = v
        else:
            raise ValueError(f"unknown usage setting {k}")
    return clean


def set_settings(home: Path, changes: dict) -> dict:
    clean = validate_settings(changes)
    db = _connect(home)
    try:
        db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        for k, v in clean.items():
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = "
                       "excluded.value", (f"usage.{k}", json.dumps(v)))
    finally:
        db.close()
    logs.info("usage", "USAGE_SETTINGS", "usage settings changed", keys=sorted(clean))
    return get_settings(home)


# ---------------------------------------------------------------------- alerts hook (WP-M3 builds jarves.alerts)
def _default_sink(alert: dict) -> None:
    """WP-M3's jarves.alerts.emit(alert) when it exists (quiet hours, channels); until then a log WARNING, which the
    dashboard keeps as an event. 100 % stays visible on the Usage page whatever the delivery settings say."""
    try:
        from . import alerts as _alerts             # type: ignore[attr-defined]
    except ImportError:
        _alerts = None
    emit = getattr(_alerts, "emit", None)
    if callable(emit):
        emit(alert)
        return
    logs.warn("usage", "USAGE_THRESHOLD", alert["title"], meter=alert["meter"], threshold=alert["threshold"],
              percent=alert["percent"], window=alert["window"])


ALERT_SINK: Callable[[dict], None] = _default_sink


# ---------------------------------------------------------------------- monitor
def _month_start(now: float, utc: bool) -> datetime:
    d = datetime.fromtimestamp(now, timezone.utc) if utc else datetime.fromtimestamp(now)
    return d.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _next_month(d: datetime) -> datetime:
    return d.replace(year=d.year + 1, month=1) if d.month == 12 else d.replace(month=d.month + 1)


class Monitor:
    """Builds the meters from memory.db (Budget counters, observations), store.db and the optional Costs API."""

    def __init__(self, home: Path, cfg=None, *, clock=time.time, http=None) -> None:
        if cfg is None:
            from .config import Config
            cfg = Config()
        self.home, self.cfg, self._clock = Path(home), cfg, clock
        self._http = http                          # tests inject a fake GET (url, headers, params) -> (status, json)

    # --- sources
    def _db(self) -> sqlite3.Connection:
        return _connect(self.home)

    def _local_day_usage(self, db, kind: str) -> int:
        try:
            r = db.execute("SELECT count FROM usage WHERE day = ? AND kind = ?",
                           (date.fromtimestamp(self._clock()).isoformat(), kind)).fetchone()
        except sqlite3.Error:
            return 0
        return int(r[0]) if r else 0

    def _store(self):
        p = self.home / "store.db"
        if not p.is_file():
            return None
        from .store import Store
        return Store(p)

    def _plan_events(self) -> tuple[float | None, float | None, int]:
        """(last plan-limit error ts, last successful plan reply ts, JARVES plan replies today) from store.db."""
        st = self._store()
        if st is None:
            return None, None, 0
        try:
            lim = st.rows("SELECT MAX(ts) t FROM events WHERE code = ?", (PLAN_LIMIT_CODE,))[0]["t"]
            ok = st.rows("SELECT MAX(ts) t FROM delegations WHERE billed = 0", ())[0]["t"]
            t0 = datetime.fromtimestamp(self._clock()).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            n = st.rows("SELECT COALESCE(SUM(calls), 0) n FROM delegations WHERE billed = 0 AND ts >= ?", (t0,))[0]["n"]
            return lim, ok, int(n or 0)
        except sqlite3.Error:
            return None, None, 0
        finally:
            st.close()

    def _estimate_month_usd(self) -> float | None:
        st = self._store()
        if st is None:
            return None
        try:
            from .store import daily_costs
            start = _month_start(self._clock(), utc=False)
            days = daily_costs(st, self.cfg.pricing, int((self._clock() - start.timestamp()) // 86400) + 1)
            return round(sum(d["total"] for d in days if d["day"] >= start.date().isoformat()), 4)
        except Exception:
            return None
        finally:
            st.close()

    def costs_enabled(self, settings: dict | None = None) -> bool:
        mode = (settings or get_settings(self.home)).get("costs_api", "auto")
        return mode != "off" and bool(os.environ.get(ADMIN_ENV, "").strip())

    def refresh_costs(self, force: bool = False) -> dict | None:
        """Month-to-date organization cost from the Costs API (Admin key, read-only GET; free; at most hourly).
        Returns the stored observation or None. Never raises; a failure is stored as an error observation."""
        if not self.costs_enabled():
            return None
        db = self._db()
        try:
            prev = _get_obs(db, "openai_api.costs")
        finally:
            db.close()
        now = self._clock()
        if prev and not force and now - prev[1] < COSTS_REFRESH_S:
            return prev[0]
        start = _month_start(now, utc=True)
        params = {"start_time": int(start.timestamp()), "bucket_width": "1d", "limit": 31}
        headers = {"Authorization": f"Bearer {os.environ[ADMIN_ENV].strip()}"}
        total, currency, pages, err = 0.0, "usd", 0, None
        try:
            while pages < 3:
                status, body = (self._http or _http_get)(COSTS_URL, headers, params)
                pages += 1
                if status != 200 or not isinstance(body, dict):
                    err = f"HTTP {status}"
                    break
                for bucket in body.get("data") or []:
                    for res in bucket.get("results") or []:
                        amt = res.get("amount") or {}
                        total += float(_num(amt.get("value")) or 0.0)
                        currency = str(amt.get("currency") or currency)
                if not body.get("has_more") or not body.get("next_page"):
                    break
                params = {**params, "page": body["next_page"]}
        except Exception as e:                       # network down, bad JSON...
            err = type(e).__name__
        data = ({"error": err, "month": start.strftime("%Y-%m")} if err else
                {"usd": round(total, 4), "currency": currency, "month": start.strftime("%Y-%m"),
                 "start": start.timestamp()})
        if err:
            logs.warn("usage", "USAGE_COSTS_FAILED", "OpenAI Costs API not readable", error=err)
            if prev and "usd" in prev[0]:
                data = {**prev[0], "stale_error": err}
        _put_obs(self.home, "openai_api.costs", data, now if not err or not prev else prev[1])
        return data

    # --- meters
    def meters(self) -> list[Meter]:
        now = self._clock()
        settings = get_settings(self.home)
        ai = self.cfg.ai
        today = date.fromtimestamp(now).isoformat()
        db = self._db()
        try:
            plan_hdr = _get_obs(db, "chatgpt_plan.headers")
            api_hdr = _get_obs(db, "openai_api.headers")
            rt = _get_obs(db, "openai_api.realtime")
            costs = _get_obs(db, "openai_api.costs")
            ai_req = self._local_day_usage(db, "ai_requests")
            voice_s = self._local_day_usage(db, "voice_seconds")
            plan_win = _get_obs(db, "chatgpt_plan.window")
        finally:
            db.close()
        lim_ts, ok_ts, plan_today = self._plan_events()
        out: list[Meter] = []

        # ChatGPT plan quota: provider data or Unknown
        win = (plan_hdr[0].get("plan_primary") if plan_hdr else None)
        if win and now - plan_hdr[1] < 6 * 3600:
            reset_at = win.get("reset_at")
            key = self._plan_window_key(win, plan_win, now)
            mins = win.get("window_minutes")
            out.append(Meter("chatgpt_plan.quota", "chatgpt_plan", "ChatGPT plan usage", "plan usage window",
                             "percent", "provider_authoritative", "response headers of JARVES's plan requests",
                             used=win["used_percent"], limit=100.0, limit_source="provider",
                             window=f"{mins:g} min" if mins else None, window_key=key, reset_at=reset_at,
                             last_verified=plan_hdr[1], confidence="medium", scope="account",
                             note="undocumented headers of the plan route; ChatGPT Settings > Usage is the truth",
                             state="limit_reached" if win["used_percent"] >= 100 else "ok"))
        elif lim_ts and (ok_ts is None or lim_ts > ok_ts):
            out.append(Meter("chatgpt_plan.quota", "chatgpt_plan", "ChatGPT plan usage", "plan usage limit",
                             "percent", "provider_authoritative", "the plan route's 'usage limit reached' error",
                             used=100.0, limit=100.0, limit_source="provider", window=None,
                             window_key=f"limit@{int(lim_ts)}", reset_at=None, last_verified=lim_ts, confidence="high",
                             scope="account", state="limit_reached",
                             note="JARVES paused plan requests; reset time not reported. It does NOT switch to the "
                                  "paid API by itself."))
        else:
            out.append(Meter("chatgpt_plan.quota", "chatgpt_plan", "ChatGPT plan usage", "plan quota remaining",
                             "percent", "unavailable", "not exposed to apps by the ChatGPT plan route",
                             confidence="none", scope="account", state="unknown", alerts=False,
                             note="OpenAI does not report remaining plan quota or its reset to JARVES. "
                                  "See ChatGPT Settings > Usage."))
        out.append(Meter("chatgpt_plan.jarves_requests", "chatgpt_plan", "JARVES requests on the plan today",
                         "model requests sent by this JARVES on your plan", "requests", "app_local_counter",
                         "store.db (typed answers / tasks on the plan)", used=float(plan_today), limit=None,
                         window="today (local)", window_key=today, last_verified=now, confidence="high",
                         scope="this_install", alerts=False,
                         note="only this install's requests, not your whole ChatGPT account"))

        # OpenAI API money: Costs API (authoritative) or the local estimate
        budget = settings.get("api_monthly_budget_usd")
        if costs and "usd" in costs[0] and self.costs_enabled(settings):
            c, ts = costs
            start = datetime.fromtimestamp(c["start"], timezone.utc)
            out.append(Meter("openai_api.spend_month", "openai_api", "OpenAI API spend this month", "organization cost",
                             "usd", "provider_authoritative", "OpenAI Costs API (Admin key, read-only)",
                             used=c["usd"], limit=budget, limit_source="your setting" if budget else "",
                             window="calendar month (UTC)", window_key=c["month"],
                             reset_at=_next_month(start).timestamp(), last_verified=ts, confidence="high",
                             scope="organization", state="stale" if c.get("stale_error") else "ok",
                             note="daily buckets; today may lag. The project budget set on platform.openai.com is "
                                  "not readable by API" + (" (last refresh failed)" if c.get("stale_error") else "")))
        else:
            est = self._estimate_month_usd()
            start = _month_start(now, utc=False)
            out.append(Meter("openai_api.spend_month", "openai_api", "OpenAI API spend this month (estimate)",
                             "JARVES's voice + backend cost, estimated", "usd", "estimate",
                             "store.db x [pricing]", used=est, limit=budget, limit_source="your setting" if budget else "",
                             window="calendar month (local)", window_key=start.strftime("%Y-%m"),
                             reset_at=_next_month(start).timestamp(), last_verified=now, confidence="low",
                             scope="this_install",
                             note="estimate of this install only; your OpenAI bill is the truth. Add a read-only "
                                  f"{ADMIN_ENV} for the real organization cost."))
        out.append(Meter("openai_api.voice_minutes", "openai_api", "Live voice minutes today", "GPT-Live session time",
                         "minutes", "app_local_counter", "JARVES budget counter (memory.db)",
                         used=round(voice_s / 60, 1), limit=float(ai.daily_realtime_minutes) or None,
                         limit_source="JARVES daily cap (ai.daily_realtime_minutes)", window="today (local)",
                         window_key=today, reset_at=datetime.fromisoformat(today).timestamp() + 86400,
                         last_verified=now, confidence="high", scope="this_install"))
        for key, obs, src in (("openai_api.headers", api_hdr, "x-ratelimit-* response headers"),
                              ("openai_api.realtime", rt, "Realtime rate_limits.updated event")):
            if not obs:
                continue
            for kind in ("requests", "tokens", "project-tokens"):
                v = obs[0].get(kind)
                if not v:
                    continue
                stale = now - obs[1] > RATE_FRESH_S
                tag = "realtime" if key.endswith("realtime") else "api"
                out.append(Meter(f"openai_api.rate_{tag}_{kind.replace('-', '_')}", "openai_api",
                                 f"API rate limit ({kind}{', realtime' if tag == 'realtime' else ''})",
                                 f"{kind} per rate-limit window", "tokens" if "tokens" in kind else "requests",
                                 "provider_authoritative", src, used=max(0.0, v["limit"] - v["remaining"]),
                                 limit=v["limit"], limit_source="provider", window="rate-limit window (about a minute)",
                                 window_key=None, reset_at=v.get("reset_at"), last_verified=obs[1],
                                 confidence="low" if stale else "high", scope="project", alerts=False,
                                 state="stale" if stale else "ok",
                                 note="a speed limit that refills within about a minute, not a spending limit"))

        # JARVES's own daily request cap (spans plan and API requests)
        out.append(Meter("jarves.ai_requests", "jarves_local", "JARVES model requests today", "model requests "
                         "(plan + API)", "requests", "app_local_counter", "JARVES budget counter (memory.db)",
                         used=float(ai_req), limit=float(ai.daily_request_limit) or None,
                         limit_source="JARVES daily cap (ai.daily_request_limit)", window="today (local)",
                         window_key=today, reset_at=datetime.fromisoformat(today).timestamp() + 86400,
                         last_verified=now, confidence="high", scope="this_install"))
        return out

    def _plan_window_key(self, win: dict, stored, now: float) -> str:
        """Same window until the provider-reported reset has really passed; then a new key (no invented reset)."""
        reset_at = win.get("reset_at")
        if stored:
            st = stored[0]
            old_reset = st.get("reset_at")
            if old_reset is None or reset_at is None or now < old_reset - 60 or abs(reset_at - old_reset) < 600:
                return st["key"]
        key = f"plan@{int(reset_at)}" if reset_at else f"plan@{int(now)}"
        _put_obs(self.home, "chatgpt_plan.window", {"key": key, "reset_at": reset_at}, now)
        return key

    # --- alerts
    def check_alerts(self, meters: list[Meter] | None = None) -> list[dict]:
        """Fire each threshold once per meter/window (persisted). Unknown meters never fire."""
        meters = meters if meters is not None else self.meters()
        th = sorted(get_settings(self.home)["thresholds"])
        fired = []
        db = self._db()
        try:
            for m in meters:
                pct = m.percent()
                if not m.alerts or pct is None or not m.window_key:
                    continue
                crossed = [t for t in th if pct >= t]
                if not crossed:
                    continue
                new = []
                for t in crossed:          # INSERT OR IGNORE: exactly one process records (and sends) each one
                    cur = db.execute("INSERT OR IGNORE INTO usage_alerts (meter, window, threshold, ts, percent) "
                                     "VALUES (?, ?, ?, ?, ?)", (m.id, m.window_key, t, self._clock(), pct))
                    if cur.rowcount == 1:
                        new.append(t)
                if not new:
                    continue
                t = max(new)
                alert = {"kind": "usage_threshold", "meter": m.id, "provider": m.provider, "threshold": t,
                         "percent": pct, "window": m.window_key, "category": m.category,
                         "priority": "high" if t >= 100 else "normal", "dedupe_key": f"usage:{m.id}:{m.window_key}:{t}",
                         "title": f"{m.title}: {pct:g}% ({_fmt(m.used, m.unit)} of {_fmt(m.limit, m.unit)})",
                         "title_he": _alert_he(m, pct), "ts": self._clock()}
                try:
                    ALERT_SINK(alert)
                except Exception:
                    logs.error("usage", "USAGE_ALERT_SINK_FAILED", "alert delivery failed", exc=True)
                fired.append(alert)
        finally:
            db.close()
        return fired

    def alert_history(self, limit: int = 50) -> list[dict]:
        db = self._db()
        try:
            return [{"meter": r[0], "window": r[1], "threshold": r[2], "ts": r[3], "percent": r[4]} for r in
                    db.execute("SELECT meter, window, threshold, ts, percent FROM usage_alerts ORDER BY ts DESC "
                               "LIMIT ?", (limit,))]
        finally:
            db.close()

    # --- views
    def footer(self, meters: list[Meter] | None = None) -> dict:
        """The two compact indicators for the dashboard's bottom strip."""
        ms = {m.id: m for m in (meters if meters is not None else self.meters())}

        def pick(main: str, extra: str) -> dict:
            m, x = ms[main], ms.get(extra)
            return {"meter": m.to_dict(), "extra": x.to_dict() if x else None}
        return {"chatgpt_plan": pick("chatgpt_plan.quota", "chatgpt_plan.jarves_requests"),
                "openai_api": pick("openai_api.spend_month", "openai_api.voice_minutes")}

    def summary_he(self, meters: list[Meter] | None = None) -> str:
        """Short spoken answer to "כמה נשאר לי?"."""
        ms = {m.id: m for m in (meters if meters is not None else self.meters())}
        parts = []
        q = ms["chatgpt_plan.quota"]
        if q.state == "limit_reached":
            parts.append("בתוכנית ChatGPT הגעת למגבלת השימוש; מתי היא מתאפסת OpenAI לא מדווחת לי.")
        elif q.percent() is not None:
            parts.append(f"בתוכנית ChatGPT נוצלו {q.percent():g} אחוז מהחלון הנוכחי.")
        else:
            parts.append("כמה נשאר בתוכנית ChatGPT אני לא יודע: OpenAI לא מדווחת את זה לאפליקציות.")
        jr = ms["chatgpt_plan.jarves_requests"]
        if jr.used:
            parts.append(f"היום שלחתי בתוכנית {int(jr.used)} בקשות.")
        s = ms["openai_api.spend_month"]
        if s.used is not None:
            word = "לפי OpenAI" if s.category == "provider_authoritative" else "בהערכה"
            txt = f"ב-API החודש: {_fmt(s.used, 'usd')} {word}"
            if s.percent() is not None:
                txt += f", {s.percent():g} אחוז מהתקציב שקבעת"
            parts.append(txt + ".")
        v = ms["openai_api.voice_minutes"]
        if v.limit:
            parts.append(f"שיחה קולית היום: {v.used:g} מתוך {v.limit:g} דקות.")
        r = ms["jarves.ai_requests"]
        if r.limit:
            parts.append(f"בקשות למודל היום: {int(r.used or 0)} מתוך {int(r.limit)}.")
        return " ".join(parts)

    def snapshot(self) -> dict:
        ms = self.meters()
        return {"meters": [m.to_dict() for m in ms], "footer": self.footer(ms), "settings": get_settings(self.home),
                "alerts": self.alert_history(20), "costs_api": {"enabled": self.costs_enabled(),
                                                                "key_present": bool(os.environ.get(ADMIN_ENV))},
                "notes": list(_NOTES), **locales.list_siblings("notes", _NOTES)}


def _alert_he(m: Meter, pct: float) -> str:
    names = {"chatgpt_plan.quota": "תוכנית ChatGPT", "openai_api.spend_month": "הוצאות ה-API החודש",
             "openai_api.voice_minutes": "דקות השיחה הקולית היום", "jarves.ai_requests": "בקשות למודל היום"}
    return f"{names.get(m.id, m.title)}: {pct:g} אחוז מהמגבלה."


def _http_get(url: str, headers: dict, params: dict) -> tuple[int, dict | None]:
    import httpx
    r = httpx.get(url, headers=headers, params=params, timeout=15)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, None


# ---------------------------------------------------------------------- feature + voice Skill
def register_feature() -> None:
    from . import features
    f = features.register("usage_monitor", "מעקב שימוש", "Usage monitor", True, skills=["usage"],
                          about="ChatGPT plan and OpenAI API meters; 75/90/100% alerts; Unknown when not reported")
    f.targets = {"windows": {"status": "implemented_unverified",
                             "evidence": "core/tests/test_usage.py (unit + dashboard HTTP); no live plan-limit event "
                                         "observed yet"},
                 "linux_arm64_pi": {"status": "implemented_unverified",
                                    "roadmap_issue": "https://github.com/igalgerman/JARVIS/issues/22",
                                    "blocker": "pure Python + SQLite, no platform code; not yet run on a Pi"}}


def make_skill(home: Path, cfg=None):
    from .skills import Skill, Tool, ToolResult

    class UsageSkill(Skill):
        name = "usage"
        description = "How much of the ChatGPT plan / OpenAI API / JARVES daily limits is used (כמה נשאר לי?)"

        def tools(self) -> list[Tool]:
            async def status(_):
                mon = Monitor(home, cfg)
                ms = mon.meters()
                mon.check_alerts(ms)
                return ToolResult(True, mon.summary_he(ms), {"meters": [
                    {k: m.to_dict()[k] for k in ("id", "category", "unit", "used_text", "limit_text", "percent_text",
                                                 "window_text", "reset_text", "state")} for m in ms]})
            return [Tool("usage_status", "Usage and limits: the user asks 'כמה נשאר לי?', 'how much quota is left', "
                                         "'how much did I spend'. Says Unknown when the provider does not report it; "
                                         "never guess.", status, permission="read")]

        async def health(self) -> tuple[str, str]:
            return "ok", ""

    return UsageSkill()


register_feature()


# ---------------------------------------------------------------------- dashboard
def _monitor(ctx) -> Monitor:
    return Monitor(ctx.home, ctx.cfg)


def api_usage(ctx, m, body):
    mon = _monitor(ctx)
    mon.refresh_costs()
    mon.check_alerts()
    return 200, mon.snapshot()


def api_usage_settings(ctx, m, body):
    try:
        return 200, {"settings": set_settings(ctx.home, body or {})}
    except ValueError as e:
        return 400, {"error": str(e)}


def api_usage_refresh(ctx, m, body):
    mon = _monitor(ctx)
    data = mon.refresh_costs(force=True)
    return 200, {"costs": data, "enabled": mon.costs_enabled()}


def dashboard_routes(register_route) -> None:
    register_route("GET", r"/api/usage", api_usage)
    register_route("POST", r"/api/usage/settings", api_usage_settings)
    register_route("POST", r"/api/usage/refresh", api_usage_refresh)


# ---------------------------------------------------------------------- CLI
def add_cli(sub) -> None:
    u = sub.add_parser("usage", help="usage meters: ChatGPT plan, OpenAI API, JARVES daily limits")
    u.add_argument("action", nargs="?", default="show", choices=["show", "thresholds", "budget", "costs-api"])
    u.add_argument("value", nargs="?")


def cmd_usage(a, cfg, home) -> int:
    attach(home)
    mon = Monitor(home, cfg)
    try:
        if a.action == "thresholds" and a.value:
            print("thresholds:", set_settings(home, {"thresholds": a.value})["thresholds"])
            return 0
        if a.action == "budget":
            print("API monthly budget:", set_settings(home, {"api_monthly_budget_usd": a.value})["api_monthly_budget_usd"])
            return 0
        if a.action == "costs-api" and a.value:
            print("Costs API:", set_settings(home, {"costs_api": a.value})["costs_api"])
            return 0
    except ValueError as e:
        print(f"error: {e}")
        return 2
    mon.refresh_costs()
    ms = mon.meters()
    mon.check_alerts(ms)
    for m in ms:
        d = m.to_dict()
        print(f"{m.provider:13} {m.title}: {d['used_text']} / {d['limit_text']} ({d['percent_text']}) "
              f"[{m.category}; window {d['window_text']}; reset {d['reset_text']}]")
        print(f"{'':13}   source: {m.source}" + (f" — {m.note}" if m.note else ""))
    print("thresholds:", get_settings(home)["thresholds"])
    return 0
