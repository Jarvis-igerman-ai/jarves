"""The scheduled, SELECTIVE AI agent review (owner addition to package O).

1. Schedule: every N days (default 2, OD-33) at a time outside quiet hours, only when the budget allows
   (`jarvis health ai-review --every 2d | off | --now`, Health page). The scheduler job runs daily; this module
   decides whether the review is due.
2. The rule layer selects first: only what changed since the last review and is worth an AI look -
   new error clusters, regressions (baseline or previous version), slow-turn outliers, a tool failing across 3+
   conversations, false-action claims, wake false positives above the J target, conversations flagged by the
   existing diagnose rules, configuration drift, files changed (version / commit, persona files: names and size
   deltas only). Each item has a score (low 1, medium 3, high 5); items the reviewer saw before are not raised
   again unless their evidence grew.
3. Skipped at $0 when nothing notable changed (slice empty or its score below health.ai_review_min_score).
4. What the reviewer sees: a redacted slice - log codes, counts, timings, file:line + commit hints, the 10
   surrounding log lines as TEMPLATES + numeric fields (logindex.safe_record), config key names with old/new values
   for non-secret keys, finding ids. NEVER_SHOWN lists what it may never see; guard() is the last line of defence
   on the serialized request (secrets, e-mail addresses, phone numbers, Hebrew text are removed). The one exception:
   excerpts of the FLAGGED conversations, and only when the owner enabled the existing AI-review path
   (self_check.ai_pass + diagnosis_llm_pass, privacy mode off), exactly as in V3 (diagnose.review_input).
5. Cost: a per-run cap (default $0.10) and a monthly cap (default $1, OD-34). The estimate is a true upper bound
   (input tokens <= request bytes, output tokens <= max_output_tokens) shown before the run; a slice over the cap
   is trimmed to its highest-ranked items, or the run is skipped. The request is reserved through the shared
   Budget; the actual cost (from the usage the API reports) is shown after.
6. Model: the OpenAI backend (a different model family from the Claude models that wrote the code), default
   gpt-6.1-sol (checked 2026-10-20), on the owner's API-key route. Its cost lands in delegations (kind 'review'),
   like diagnose.llm_review, so the Costs page shows it.
7. Output: at most 8 ranked items (severity, evidence, hypothesis, suggested fix, effort). Each can be accepted,
   dismissed or snoozed. Accepting a settings change creates an approvals proposal (never applied here); a ticket
   is only a draft until approved. NOTHING IS EVER APPLIED AUTOMATICALLY. Undecided items go stale after 7 days.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .. import logs
from . import db

NEVER_SHOWN = (
    "secrets, API keys or tokens (only their presence)",
    "message bodies (email, WhatsApp, Telegram, Instagram)",
    "contact names, phone numbers or addresses",
    "calendar event text",
    "recalled text (conversation recall)",
    "wake audio or wake samples",
    "code from the orchestrated agents' projects",
    "raw conversation text (except the flagged conversations' excerpts when the owner enabled the AI-review path)",
)
NEVER_SHOWN_HE = (
    "סודות, מפתחות API או אסימונים (רק אם הם קיימים)",
    "תוכן הודעות (מייל, וואטסאפ, טלגרם, אינסטגרם)",
    "שמות, מספרי טלפון או כתובות של אנשי קשר",
    "טקסט של אירועים ביומן",
    "טקסט שנזכר משיחות קודמות",
    "הקלטות או דוגמאות של מילת ההפעלה",
    "קוד מהפרויקטים של הסוכנים",
    "טקסט שיחה גולמי (חוץ מקטעים של שיחות מסומנות, אם הפעלתם את בדיקת המודל)",
)
SEV_SCORE = {"low": 1.0, "medium": 3.0, "high": 5.0}
MAX_OUTPUT_TOKENS = 2000
MAX_REPORT_ITEMS = 8
STALE_S = 7 * 86400
EXCERPT_CHARS = 2000
INSTRUCTIONS = """You are an independent reviewer of JARVES, a home voice assistant (Python Core, Godot face).
You get a REDACTED slice of its self-diagnostics: findings selected by rules (log codes, counts, timings, the
source file:line and commit of each log code, redacted context lines that are message templates plus numbers).
Find the most likely root causes and concrete fixes. Rank by user impact. Do not invent facts that are not in
the slice; say when the evidence is thin. Never ask for personal data.
Answer with JSON only: {"items": [{"ref": "<slice item id>", "severity": "low|medium|high", "title": "short",
"evidence": "what in the slice shows it", "hypothesis": "likely root cause", "fix": "concrete next step",
"effort": "minutes|hour|bigger", "action": null}]} with at most 8 items (empty list if nothing is wrong)."""

_PHONE = re.compile(r"(?<![\w.:])\+?\d[\d\-\s().]{7,}\d(?![\w.:])")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2})?")


def _phone(m: re.Match) -> str:
    s = m.group(0)
    if _DATE.fullmatch(s.strip()) or sum(ch.isdigit() for ch in s) < 7:
        return s                                    # a date / time stamp, not a number
    return "<number>"
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_HEBREW = re.compile("[\\u0590-\\u05FF][\\u0590-\\u05FF ]*")      # Hebrew runs (conversation language)
_LONGTOKEN = re.compile(r"\b[A-Za-z0-9_\-]{32,}\b")
_SAFE_STR = re.compile(r"^[a-z0-9_.\-<>]{0,32}$")      # enum-like values only; anything else becomes <N chars>


@dataclass
class SliceItem:
    id: str
    kind: str                 # error_cluster | regression | slow_turns | tool_failures | false_claim | wake_fp |
    severity: str             # trend | canary | preflight | config_drift | flagged_conversation | files_changed
    score: float
    title: str
    facts: dict = field(default_factory=dict)
    evidence_n: int = 1


# ---------------------------------------------------------------------- the boundary
def guard(text: str) -> str:
    """Last line of defence on everything that leaves for the reviewer (except an allowed excerpt)."""
    t = logs.redact(str(text))
    t = _EMAIL.sub("<email>", t)
    t = _PHONE.sub(_phone, t)
    t = _LONGTOKEN.sub("<token>", t)
    return _HEBREW.sub("<he>", t)


def _safe_value(v):
    if isinstance(v, (bool, int, float)) or v is None:
        return v
    s = str(v)
    return s if _SAFE_STR.match(s) else f"<{len(s)} chars>"


KIND_OF = (("anomaly:newcode:", "error_cluster"), ("anomaly:step:", "error_cluster"), ("version:", "regression"),
           ("anomaly:metric:", "regression"), ("anomaly:slow:", "slow_turns"), ("anomaly:tool:", "tool_failures"),
           ("anomaly:claim:", "false_claim"), ("anomaly:wake_false", "wake_fp"), ("trend:", "trend"),
           ("canary:", "canary"), ("preflight:", "preflight"), ("config:drift", "config_drift"))


def _kind(fp: str) -> str:
    return next((k for p, k in KIND_OF if fp.startswith(p)), "other")


# ---------------------------------------------------------------------- the rule layer
def select(store, *, home: Path, cfg=None, since: float, now: float, index=None, dead_air_s: float = 4.0,
           include_seen: bool = False) -> list[SliceItem]:
    """The slice: only what is new or notable in [since, now], highest score first."""
    from .anomaly import Detector
    from .logindex import context_lines
    det = Detector(store, clock=lambda: now, dead_air_s=dead_air_s, index=index)
    items: list[SliceItem] = []
    for f in det.run(window_s=max(3600.0, now - since), now=now):
        kind = _kind(f.fingerprint)
        if kind == "other":
            continue
        facts = {"finding": f.fingerprint, "category": f.category, "count": f.count,
                 "evidence": [guard(e)[:160] for e in f.evidence[:5]], "codes": f.codes[:3]}
        if f.hints:
            facts["code_locations"] = [{k: h.get(k) for k in ("code", "file", "line", "function", "commit", "url")}
                                       for h in f.hints]
        if f.codes and kind in ("error_cluster", "false_claim", "regression", "tool_failures"):
            try:
                facts["context"] = context_lines(home, f.codes[0], since=since, until=now, index=index)
            except Exception:
                facts["context"] = []
        if kind == "config_drift":
            facts["evidence"] = [_drift_line(e) for e in f.evidence[:5]]
        items.append(SliceItem(f.fingerprint, kind, f.severity, SEV_SCORE.get(f.severity, 1.0), f.title[:160],
                               facts, int(f.count)))
    items += _flagged(store, since)
    items += _files(store, home, since, now)
    if not include_seen:
        seen = {r["fingerprint"]: r["n"] for r in db.rows(
            store, "SELECT fingerprint, MAX(evidence_n) n FROM diag_review_items GROUP BY fingerprint")}
        items = [i for i in items if i.id not in seen or i.evidence_n > seen[i.id]]
    items.sort(key=lambda i: (-i.score, i.kind, i.id))
    return items


def _drift_line(e: str) -> str:
    key, _, rest = e.partition(":")
    old, _, new = rest.partition("->")
    return f"{key.strip()}: {_safe_value(old.strip())} -> {_safe_value(new.strip())}"


def _flagged(store, since: float) -> list[SliceItem]:
    """Conversations flagged by the existing diagnose rules (issue kind, severity, counts; never the text)."""
    out = []
    try:
        rows = store.issues("open")
    except Exception:
        return out
    for i in rows:
        if i["last_seen"] < since or i["severity"] not in ("high", "medium") or str(i["kind"]) == "ai_review":
            continue
        out.append(SliceItem(f"issue:{i['fingerprint']}", "flagged_conversation", i["severity"],
                             SEV_SCORE.get(i["severity"], 1.0), f"diagnose rule: {i['kind']} ({i['severity']})",
                             {"issue_id": i["id"], "kind": i["kind"], "count": i["count"],
                              "session": i.get("last_session") or ""}, int(i["count"])))
    return out


PERSONA_GLOB = "*.md"


def persona_snapshot(home: Path) -> dict[str, int]:
    d = Path(home) / "workspace"
    out = {}
    if d.is_dir():
        for p in sorted(d.glob(PERSONA_GLOB)):
            try:
                out[p.name] = p.stat().st_size
            except OSError:
                pass
    return out


def _files(store, home: Path, since: float, now: float) -> list[SliceItem]:
    """Installed version / commit changed and persona files changed since the last review (names + size deltas)."""
    from .regression import versions
    out = []
    vs = [v for v in versions(store) if v["first"] >= since]
    if vs:
        out.append(SliceItem("files:version", "files_changed", "low", 1.0, "installed version changed",
                             {"versions": [{"version": v["version"], "commit": v["commit"]} for v in vs]}, len(vs)))
    last = db.rows(store, "SELECT detail FROM diag_events WHERE kind='persona' AND ts < ? ORDER BY id DESC LIMIT 1",
                   (since,))
    cur = persona_snapshot(home)
    if last:
        old = db.j(last[0]["detail"], {})
        changed = {k: cur.get(k, 0) - old.get(k, 0) for k in set(cur) | set(old) if cur.get(k) != old.get(k)}
        if changed:
            out.append(SliceItem("files:persona", "files_changed", "low", 1.0, "persona files changed",
                                 {"files": [{"name": k, "size_delta": v} for k, v in sorted(changed.items())]},
                                 len(changed)))
    return out


def note_persona(store, home: Path, now: float) -> None:
    db.write(store, "INSERT INTO diag_events (ts, kind, name, detail) VALUES (?,?,?,?)",
             (now, "persona", "workspace", json.dumps(persona_snapshot(home))))


# ---------------------------------------------------------------------- the request and its cost
def excerpts_allowed(store, cfg, db_settings) -> bool:
    """The V3 AI-review path: self_check.ai_pass + diagnosis_llm_pass on, privacy mode off."""
    try:
        # the OWNER must have switched it on: an explicit stored choice, never just the default value
        row = db_settings.row("SELECT value FROM agent_settings WHERE key='self_check.ai_pass'")
        if row is None or not json.loads(row["value"]) or not bool(db_settings.setting("self_check.ai_pass")):
            return False
        from .. import diagnose
        feats = diagnose._features(store, cfg)
        if feats is not None and not feats.enabled(diagnose.REVIEW_FEATURE):
            return False
        return not (cfg.log.privacy or logs.runtime_privacy())
    except Exception:
        return False


def build_request(items: list[SliceItem], *, store=None, excerpts: bool = False) -> str:
    body = {"slice": [{"id": i.id, "kind": i.kind, "severity": i.severity, "title": i.title, "facts": i.facts}
                      for i in items],
            "never_shown": list(NEVER_SHOWN)}
    text = guard(json.dumps(body, ensure_ascii=False, default=str))
    if excerpts and store is not None:
        from .. import diagnose
        parts = []
        for i in items:
            sid = i.facts.get("session") if i.kind == "flagged_conversation" else None
            if not sid:
                continue
            ex = diagnose.review_input(store, sid)          # already redacted (secrets, e-mail) by diagnose
            if ex:
                parts.append(f"--- excerpt of flagged conversation {i.id} ---\n" +
                             _LONGTOKEN.sub("<token>", _PHONE.sub(_phone, _EMAIL.sub("<email>", logs.redact(ex))))
                             [:EXCERPT_CHARS])
        if parts:
            text += "\n\n" + "\n".join(parts)
    return text


def price(cfg, model: str):
    from ..config import PricingConfig
    table = {**PricingConfig().models, **(getattr(getattr(cfg, "pricing", None), "models", None) or {})}
    p = table.get(model) or next((table[k] for k in sorted(table, key=len, reverse=True) if model.startswith(k)), None)
    if p is None:                                   # unknown model: the most expensive known price (upper bound)
        p = max(table.values(), key=lambda x: x.input + x.output)
    return p


def estimate_usd(request: str, cfg, model: str, max_out: int = MAX_OUTPUT_TOKENS) -> float:
    """A TRUE upper bound: a token is at least one byte, and the API stops at max_output_tokens."""
    p = price(cfg, model)
    return (len(request.encode("utf-8")) * p.input + max_out * p.output) / 1e6


def actual_usd(usage: dict, cfg, model: str) -> float:
    p = price(cfg, model)
    cached = min(usage.get("cached", 0), usage.get("in", 0))
    return ((usage.get("in", 0) - cached) * p.input + cached * p.cached_input + usage.get("out", 0) * p.output) / 1e6


def month_spent(store, now: float) -> float:
    return float(db.rows(store, "SELECT COALESCE(SUM(cost_usd), 0) s FROM diag_review_runs WHERE started >= ?",
                         (db.month_start(now),))[0]["s"])


def plan(items: list[SliceItem], *, cfg, model: str, run_cap: float, month_cap: float, spent: float,
         store=None, excerpts: bool = False, max_items: int = 12) -> dict:
    """Trim the slice to the highest-ranked items whose estimate fits min(run cap, monthly cap left)."""
    cap = max(0.0, min(run_cap, month_cap - spent))
    chosen = list(items[:max_items])

    def fit(k: int):
        req = build_request(chosen[:k], store=store, excerpts=excerpts)
        est = estimate_usd(req, cfg, model)
        return (req, est) if est <= cap else None

    lo, hi, best = 1, len(chosen), None              # the estimate grows with k: the longest prefix that fits
    while lo <= hi:
        mid = (lo + hi) // 2
        got = fit(mid)
        if got is not None:
            best, lo = (mid, *got), mid + 1
        else:
            hi = mid - 1
    if best is None:
        return {"items": [], "request": "", "est_usd": 0.0, "cap_usd": cap, "trimmed": len(items)}
    k, req, est = best
    return {"items": chosen[:k], "request": req, "est_usd": est, "cap_usd": cap, "trimmed": len(items) - k}


# ---------------------------------------------------------------------- the run
def _settings(dbs) -> dict:
    return {k: dbs.setting(k) for k in ("health.ai_review_enabled", "health.ai_review_every_days",
                                        "health.ai_review_run_cap_usd", "health.ai_review_month_cap_usd",
                                        "health.ai_review_model", "health.ai_review_min_score",
                                        "health.ai_review_max_items", "health.ai_review_last")}


def due(dbs, now: float) -> bool:
    s = _settings(dbs)
    every = float(s["health.ai_review_every_days"] or 0)
    return bool(s["health.ai_review_enabled"]) and every > 0 and \
        now - float(s["health.ai_review_last"] or 0) >= every * 86400 - 3600


def _record_run(store, **kw) -> int:
    cols = ", ".join(kw)
    return db.write(store, f"INSERT INTO diag_review_runs ({cols}) VALUES ({','.join('?' * len(kw))})",
                    tuple(kw.values()))


def preview(store, *, home: Path, cfg, dbs, now: float, index=None) -> dict:
    """What a run would send and cost NOW (shown before an on-demand run). Sends nothing."""
    s = _settings(dbs)
    since = float(s["health.ai_review_last"] or 0) or now - float(s["health.ai_review_every_days"] or 2) * 86400
    items = select(store, home=home, cfg=cfg, since=since, now=now, index=index,
                   dead_air_s=float(getattr(getattr(cfg, "history", None), "dead_air_s", 4.0) or 4.0))
    score = sum(i.score for i in items)
    ex = excerpts_allowed(store, cfg, dbs)
    p = plan(items, cfg=cfg, model=str(s["health.ai_review_model"]), run_cap=float(s["health.ai_review_run_cap_usd"]),
             month_cap=float(s["health.ai_review_month_cap_usd"]), spent=month_spent(store, now), store=store,
             excerpts=ex, max_items=int(s["health.ai_review_max_items"] or 12))
    return {"since": since, "items": [asdict(i) for i in items], "score": score,
            "min_score": float(s["health.ai_review_min_score"]), "would_skip": score < float(s["health.ai_review_min_score"]),
            "est_usd": round(p["est_usd"], 5), "cap_usd": round(p["cap_usd"], 4), "trimmed": p["trimmed"],
            "model": s["health.ai_review_model"], "excerpts": ex, "never_shown": list(NEVER_SHOWN),
            "never_shown_he": list(NEVER_SHOWN_HE), "month_spent_usd": round(month_spent(store, now), 5)}


def run(store, *, home: Path, cfg, dbs, client=None, budget=None, now: float | None = None, trigger: str = "schedule",
        index=None, force: bool = False) -> dict:
    """One review. Returns the run row (status: done | skipped | failed). Applies nothing."""
    now = time.time() if now is None else now
    s = _settings(dbs)
    if not force and trigger == "schedule" and not due(dbs, now):
        return {"status": "not_due", "cost_usd": 0.0}
    model = str(s["health.ai_review_model"] or "gpt-6.1-sol")
    since = float(s["health.ai_review_last"] or 0) or now - float(s["health.ai_review_every_days"] or 2) * 86400

    def skipped(reason: str, **extra) -> dict:
        rid = _record_run(store, started=now, finished=now, trigger=trigger, status="skipped", reason=reason,
                          model=model, **extra)
        dbs.set_setting("health.ai_review_last", now, actor="health")
        logs.info("health", "HEALTH_AI_REVIEW_SKIPPED", "AI review skipped", run=rid, usd=0.0, skipped_because=reason[:80])
        return run_view(store, rid, now=now)

    if not s["health.ai_review_enabled"] and trigger == "schedule":
        return skipped("off (Health page / jarvis health ai-review off)")
    dead = float(getattr(getattr(cfg, "history", None), "dead_air_s", 4.0) or 4.0)
    items = select(store, home=home, cfg=cfg, since=since, now=now, index=index, dead_air_s=dead)
    score = sum(i.score for i in items)
    if not items or score < float(s["health.ai_review_min_score"] or 0):
        return skipped("nothing new", score=score, items=len(items))
    ex = excerpts_allowed(store, cfg, dbs)
    spent = month_spent(store, now)
    p = plan(items, cfg=cfg, model=model, run_cap=float(s["health.ai_review_run_cap_usd"]),
             month_cap=float(s["health.ai_review_month_cap_usd"]), spent=spent, store=store, excerpts=ex,
             max_items=int(s["health.ai_review_max_items"] or 12))
    if not p["items"]:
        return skipped(f"cost cap (${p['cap_usd']:.3f} left this run/month)", score=score, items=len(items))
    from ..config import api_billing_allowed
    if client is None and not api_billing_allowed(cfg.ai):
        return skipped("no API key route (ai.connection api_key/combined + OPENAI_API_KEY)", score=score,
                       items=len(p["items"]), est_usd=p["est_usd"])
    sel = [i.id for i in p["items"]]
    rid = _record_run(store, started=now, trigger=trigger, status="running", model=model, items=len(sel),
                      selected=db.dumps(sel), score=score, est_usd=p["est_usd"],
                      request_bytes=len(p["request"].encode("utf-8")), excerpts=int(ex))
    logs.info("health", "HEALTH_AI_REVIEW_STARTED", "AI review started",
              run=rid, items=len(sel), est_usd=round(p["est_usd"], 5), cap_usd=round(p["cap_usd"], 4))
    own = None
    try:
        if client is None:
            import openai
            client = openai.OpenAI(timeout=60)
        if budget is None:
            from ..budget import Budget
            from ..memory import MemoryStore
            own = Budget(MemoryStore(Path(home) / "memory.db"), cfg.ai)
            budget = own
        with budget.reserve("ai_requests"):
            r = client.responses.create(model=model, instructions=INSTRUCTIONS, input=p["request"], store=False,
                                        max_output_tokens=MAX_OUTPUT_TOKENS, reasoning={"effort": "medium"})
    except Exception as e:
        db.write(store, "UPDATE diag_review_runs SET status='failed', finished=?, reason=? WHERE id=?",
                 (time.time(), type(e).__name__, rid))
        logs.warn("health", "HEALTH_AI_REVIEW_FAILED", "AI review failed", run=rid, error_type=type(e).__name__)
        return run_view(store, rid, now=now)
    finally:
        if own is not None:
            own.memory.close()
            own.close()
    u = getattr(r, "usage", None)
    usage = {"in": int(getattr(u, "input_tokens", 0) or 0), "out": int(getattr(u, "output_tokens", 0) or 0),
             "cached": int(getattr(getattr(u, "input_tokens_details", None), "cached_tokens", 0) or 0)}
    cost = actual_usd(usage, cfg, model)
    raw = (getattr(r, "output_text", "") or "").strip()
    try:
        found = json.loads(raw[raw.find("{"): raw.rfind("}") + 1] if "{" in raw else "{}").get("items", [])
    except ValueError:
        found = []
    by_id = {i.id: i for i in p["items"]}
    rank = 0
    for it in [x for x in found if isinstance(x, dict) and str(x.get("title", "")).strip()][:MAX_REPORT_ITEMS]:
        rank += 1
        ref = str(it.get("ref") or "")
        sev = str(it.get("severity", "low")).lower()
        fp = ref if ref in by_id else "ai:" + hashlib.sha1(str(it.get("title")).encode()).hexdigest()[:10]
        action = it.get("action") if isinstance(it.get("action"), dict) else None
        db.write(store, "INSERT INTO diag_review_items (run_id, rank, fingerprint, severity, title, evidence, "
                        "hypothesis, fix, effort, action, refs, evidence_n, created) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (rid, rank, fp, sev if sev in SEV_SCORE else "low", " ".join(str(it["title"]).split())[:160],
                  str(it.get("evidence", ""))[:500], str(it.get("hypothesis", ""))[:500], str(it.get("fix", ""))[:500],
                  str(it.get("effort", "hour"))[:10], db.dumps(action) if action else None, db.dumps([ref]),
                  by_id[ref].evidence_n if ref in by_id else 1, now))
    # every selected item counts as seen (not re-raised unless its evidence grows)
    for i in p["items"]:
        if not db.rows(store, "SELECT 1 FROM diag_review_items WHERE run_id=? AND fingerprint=?", (rid, i.id)):
            db.write(store, "INSERT INTO diag_review_items (run_id, rank, fingerprint, severity, title, evidence_n, "
                            "created, state) VALUES (?,?,?,?,?,?,?,?)",
                     (rid, 0, i.id, i.severity, i.title, i.evidence_n, now, "seen"))
    db.write(store, "UPDATE diag_review_runs SET status='done', finished=?, cost_usd=?, input_tokens=?, output_tokens=?"
                    " WHERE id=?", (time.time(), cost, usage["in"], usage["out"], rid))
    try:
        store.x("INSERT INTO delegations (session_id, ts, kind, model, tier, status, duration_s, input_tokens, "
                "cached_tokens, output_tokens, web_searches) VALUES (?,?,?,?,?,?,?,?,?,?,0)",
                (None, now, "review", str(getattr(r, "model", "") or model), "", "completed", 0.0, usage["in"],
                 usage["cached"], usage["out"]))
        store.commit()
    except Exception:
        pass
    dbs.set_setting("health.ai_review_last", now, actor="health")
    if cost > p["cap_usd"] + 1e-9:
        logs.warn("health", "HEALTH_AI_REVIEW_OVER_CAP", "AI review cost above its estimate", run=rid,
                  usd=round(cost, 5), cap_usd=round(p["cap_usd"], 5))
    logs.info("health", "HEALTH_AI_REVIEW_DONE", "AI review done", run=rid, items=rank,
              usd=round(cost, 5), est_usd=round(p["est_usd"], 5), model=model)
    return run_view(store, rid, now=now)


# ---------------------------------------------------------------------- views and decisions
def run_view(store, rid: int | None = None, *, now: float | None = None) -> dict | None:
    now = time.time() if now is None else now
    r = db.rows(store, "SELECT * FROM diag_review_runs WHERE id=?", (rid,)) if rid else \
        db.rows(store, "SELECT * FROM diag_review_runs WHERE status != 'running' ORDER BY id DESC LIMIT 1")
    if not r:
        return None
    run_ = dict(r[0])
    run_["selected"] = db.j(run_["selected"], [])
    items = db.rows(store, "SELECT * FROM diag_review_items WHERE run_id=? AND rank > 0 ORDER BY rank", (run_["id"],))
    for it in items:
        it["action"] = db.j(it["action"])
        it["refs"] = db.j(it["refs"], [])
        if it["state"] == "snoozed" and (it["until"] or 0) <= now:
            it["state"] = "open"
        if it["state"] == "open" and now - it["created"] > STALE_S:
            it["state"] = "stale"
    run_["items_list"] = items
    run_["ready"] = run_["status"] == "done" and any(i["state"] in ("open", "stale") for i in items)
    return run_


def runs(store, limit: int = 20) -> list[dict]:
    return db.rows(store, "SELECT id, started, finished, trigger, status, reason, model, items, score, est_usd, "
                          "cost_usd FROM diag_review_runs ORDER BY id DESC LIMIT ?", (limit,))


def decide(store, item_id: int, op: str, *, by: str, days: float = 7, home: Path | None = None, cfg=None,
           dbs=None, now: float | None = None) -> dict:
    """accept | dismiss | snooze | reopen. Accepting a settings change only creates an approvals proposal."""
    if op not in ("accept", "dismiss", "snooze", "reopen"):
        raise ValueError("op must be accept, dismiss, snooze or reopen")
    now = time.time() if now is None else now
    r = db.rows(store, "SELECT * FROM diag_review_items WHERE id=? AND rank > 0", (item_id,))
    if not r:
        raise KeyError(item_id)
    it = r[0]
    state = {"accept": "accepted", "dismiss": "dismissed", "snooze": "snoozed", "reopen": "open"}[op]
    until = now + max(1.0, float(days)) * 86400 if op == "snooze" else None
    pid = it["proposal_id"]
    action = db.j(it["action"])
    if op == "accept" and action and action.get("type") in ("setting", "feature_on") and dbs is not None:
        from ..selfcheck import propose
        pid = propose({"fingerprint": f"ai_review:{it['id']}", "action": action, "title": it["title"],
                       "fix": it["fix"] or "", "fix_he": it["fix"] or it["title"], "evidence": [it["evidence"] or ""]},
                      home=home, cfg=cfg, db=dbs, clock=lambda: now)
    db.write(store, "UPDATE diag_review_items SET state=?, until=?, decided_at=?, decided_by=?, proposal_id=? WHERE id=?",
             (state, until, now, by, pid, item_id))
    logs.info("health", "HEALTH_AI_ITEM_DECIDED", "AI review item decided", decision=op, item=item_id, by=by, proposal=pid)
    return {"id": item_id, "state": state, "until": until, "proposal_id": pid, "needs_approval": bool(pid),
            "applied": False}


def summary(view: dict | None) -> tuple[str, str]:
    """The spoken summary of the last review ("מה מצא הבודק?")."""
    if not view:
        return ("The AI reviewer has not run yet.", "הבודק עוד לא רץ.")
    if view["status"] == "skipped":
        return (f"The last AI review was skipped ({view['reason']}); it cost nothing.",
                f"הבדיקה האחרונה של הבודק דולגה ({'לא היה שום דבר חדש' if view['reason'] == 'nothing new' else view['reason']}), "
                "והיא לא עלתה כלום.")
    items = [i for i in view["items_list"] if i["state"] in ("open", "stale", "accepted")]
    cost = view.get("cost_usd") or 0.0
    if not items:
        return (f"The AI reviewer found nothing to fix (cost ${cost:.3f}).", f"הבודק לא מצא מה לתקן (עלות ${cost:.3f}).")
    top = items[0]
    return (f"The AI reviewer found {len(items)} item(s), cost ${cost:.3f}. The main one: {top['title']}. "
            f"Suggested: {top['fix'] or 'see the Health page'}. Nothing was changed; you decide on the Health page.",
            f"הבודק מצא {len(items)} נקודות, בעלות של ${cost:.3f}. העיקרית: {top['title']}. "
            "שום דבר לא שונה; ההחלטה שלכם בעמוד התקינות.")
