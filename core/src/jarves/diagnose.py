"""Self-diagnosis: rule-based analysis of finished conversations in the history store (store.py).

Runs in the store's writer thread after each conversation ends, and on demand (`jarvis diagnose`). Finds dead air,
stalled requests (the 19 Oct Intel web search: ~90 s of "checking..." and no answer), tool failures / repeats / slow
tools, slow or failed backend calls, early-response drops, user confusion ("לא הבנתי"), a filler the user does not
understand ("סגור" said again and again), the user repeating himself, false wakes, errors and high cost.
Issues are DEDUPLICATED by fingerprint: the same problem in another conversation raises its count, never a new row;
re-running on the same conversation changes nothing. A resolved issue that happens again is reopened.
Text rules need transcripts: with log.privacy on, only timing rules apply.

Optional AI review (history.llm_review, ON by default; SW-V2 WP-H): after the rules, ONE short backend request per
finished voice conversation (gpt-6-luna by default) reads the transcript and adds problems the rules miss. It runs
in its own thread (never blocks the log writer), only on the API-key route, reserves one `ai_requests` unit from
the shared daily Budget first, at most history.llm_review_daily_max a day, and is skipped with log.privacy on.
Its findings are labelled "AI review" (kind ai_review) and its exact cost is added to the conversation.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import threading
import time
from datetime import date, datetime
from pathlib import Path

from . import logs
from .store import Store, daily_costs, session_cost

SEVERITY = {"low": 0, "medium": 1, "high": 2}
_PUNCT = re.compile(r"[.,!?…:;\"'“”״׳()\-–—]+")
# Short acknowledgements; a sentence that is ONLY one of these is a filler. Same-root variants count together.
ACK = {"סגור": "סגור", "סוגר": "סגור", "בטח": "בטח", "אוקיי": "אוקיי", "אוקי": "אוקיי", "ok": "אוקיי",
       "okay": "אוקיי", "בסדר": "בסדר", "מעולה": "מעולה", "יופי": "יופי", "sure": "sure", "done": "done"}
WAITING = re.compile(r"בודק|בודקת|מחפש|מחפשת|\bרגע\b|שנייה|שניה|כמעט שם|מתעכב|one moment|just a (sec|moment)|"
                     r"checking|looking (it )?up|hold on", re.I)
CONFUSION = re.compile(r"לא הבנתי|לא שמעתי|מה אמרת|מה אתה אומר|מה זה אומר|מה זאת אומרת|תחזור על|לא ברור|"
                       r"what did you say|i don'?t understand|what do you mean|pardon", re.I)
IMPATIENCE = re.compile(r"למה (זה )?לוקח|כמה זמן|אתה שם|הלו\b|נו\s*\?|why is it taking|are you there|hello\?", re.I)
MAX_EVIDENCE_TURNS = 6


def _words(text: str) -> list[str]:
    return [w for w in _PUNCT.sub(" ", text.lower()).split() if w]


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"[.!?…]+", text) if s.strip()]


def is_filler(text: str) -> bool:
    w = _words(text)
    return 0 < len(w) <= 3 and all(x in ACK for x in w)


def is_waiting(text: str) -> bool:
    return bool(WAITING.search(text)) and len(_words(text)) <= 12 and not re.search(r"\d", text)


def substantive(t: dict) -> bool:
    """An assistant turn that actually answers (not "סגור", not "one moment, checking")."""
    text = t.get("text")
    if text is None:                  # privacy mode: unknown content, treat as an answer (no false stalls)
        return True
    return not is_filler(text) and not is_waiting(text)


def _clock(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def _quote(t: dict) -> str:
    return t["text"] if t.get("text") is not None else f"<{t.get('chars', 0)} chars, private>"


class Finder:
    def __init__(self, store: Store, cfg) -> None:
        self.store, self.cfg, self.h = store, cfg, cfg.history
        self.found: list[dict] = []

    # ------------------------------------------------------------------ issue upsert (deduplicated)
    def issue(self, sid: str, ts: float, fp: str, kind: str, severity: str, title: str, detail: str,
              evidence: dict) -> None:
        st = self.store
        evidence = {"session": sid, **evidence}
        with st._lock:
            db = st._db
            new_hit = db.execute("INSERT OR IGNORE INTO issue_hits (fingerprint, session_id) VALUES (?, ?)",
                                 (fp, sid)).rowcount > 0
            row = db.execute("SELECT * FROM issues WHERE fingerprint = ?", (fp,)).fetchone()
            ev = json.dumps(evidence, ensure_ascii=False, default=str)
            if row is None:
                db.execute("INSERT INTO issues (fingerprint, kind, severity, title, detail, evidence, first_seen, "
                           "last_seen, count, last_session) VALUES (?,?,?,?,?,?,?,?,1,?)",
                           (fp, kind, severity, title, detail, ev, ts, ts, sid))
            else:
                sev = max(severity, row["severity"], key=lambda s: SEVERITY.get(s, 0))
                status = "open" if (new_hit and row["status"] == "resolved") else row["status"]
                latest = ts >= row["last_seen"]
                db.execute("UPDATE issues SET severity=?, count=count+?, status=?, title=?, detail=?, evidence=?, "
                           "last_seen=?, last_session=? WHERE id=?",
                           (sev, 1 if new_hit else 0, status, title if latest else row["title"],
                            detail if latest else row["detail"], ev if latest else row["evidence"],
                            max(ts, row["last_seen"]), sid if latest else row["last_session"], row["id"]))
        self.found.append({"fingerprint": fp, "severity": severity, "title": title, "session": sid})

    # ------------------------------------------------------------------ rules
    def session(self, s: dict) -> None:
        sid = s["id"]
        tl = self.store.timeline(sid)
        turns, tools, dels, events = tl["turns"], tl["tools"], tl["delegations"], tl["events"]
        end = s.get("ended") or s.get("last_event") or s["started"]
        self._stalls(sid, s, turns, tools, dels, events, end)
        self._dead_air(sid, turns, end)
        self._fillers_and_confusion(sid, turns, end)
        self._tools(sid, tools, end)
        self._backend(sid, dels, end)
        self._events(sid, s, events, end)
        c = session_cost(self.store, s, self.cfg.pricing)
        if c["total"] > self.cfg.pricing.session_alert_usd:
            self.issue(sid, end, "cost_session", "cost", "medium",
                       f"Expensive conversation: ~${c['total']:.2f} (estimate)",
                       f"Above pricing.session_alert_usd (${self.cfg.pricing.session_alert_usd:.2f}).", {"cost": c})

    def _stalls(self, sid, s, turns, tools, dels, events, end) -> None:
        """A request answered only by "checking..." fillers (or progress notes) for longer than history.stall_s,
        and silence while such a request is pending."""
        progress = [e["ts"] for e in events if e["code"] == "LIVE_PROGRESS"]
        # Older runs have only aggregate speech counters. A bucket with speech cannot prove silence within
        # that bucket; retain that uncertainty instead of labelling spoken answer time as measured dead air.
        speech_windows = []
        previous = s["started"]
        for e in events:
            if e["code"] == "LIVE_MIC":
                fields = json.loads(e.get("fields") or "{}")
                if (fields.get("model_speech_s") or 0) > 0:
                    speech_windows.append((previous, e["ts"]))
                previous = e["ts"]
        consumed = -1
        for i, u in enumerate(turns):
            if i <= consumed or u["role"] != "user" or u.get("text") is None:
                continue
            waiting, impatient, stop, outcome, j = [], [], None, "", i
            for j in range(i + 1, len(turns)):
                t = turns[j]
                if t["role"] == "assistant":
                    if substantive(t):
                        stop, outcome = t.get("audio_started") or t["ts"], "answered late"
                        if not t.get("audio_started") and waiting:
                            # No intervening assistant transcript after the last filler: the first subsequent
                            # speech bucket bounds answer onset from above (never assume its exact onset).
                            stop = min([stop] + [b for a, b in speech_windows
                                               if waiting[-1]["ts"] <= a and b <= t["ts"]])
                        break
                    if is_waiting(t["text"]):
                        waiting.append(t)
                elif t.get("text") and IMPATIENCE.search(t["text"]):
                    impatient.append(t)
                else:
                    stop, outcome = t["ts"], "the user gave up and asked something else"
                    break
            else:
                stop, outcome = end, "never answered: the conversation ended while waiting"
            if not waiting and not any(u["ts"] < p < stop for p in progress):
                continue                           # handled normally (an answer or a plain acknowledgement)
            consumed = j
            dur = stop - u["ts"]
            window = sorted([u["ts"], stop] + [t["ts"] for t in turns[i + 1:j]] +
                            [x["ts"] for x in tools + dels if u["ts"] <= x["ts"] <= stop] +
                            [p for p in progress if u["ts"] <= p <= stop])
            intervals = [(x, x) for x in window if u["ts"] <= x <= stop]
            intervals += [(max(u["ts"], a), min(stop, b)) for a, b in speech_windows
                          if a < stop and b > u["ts"]]
            intervals += [(max(u["ts"], t["audio_started"]), min(stop, t["audio_ended"]))
                          for t in turns[i + 1:j + 1] if t.get("audio_started") is not None
                          and t.get("audio_ended") is not None and t["audio_started"] < stop]
            cursor, gap, gap_at = u["ts"], 0.0, u["ts"]
            for a, b in sorted(intervals):
                if a - cursor > gap:
                    gap, gap_at = a - cursor, cursor
                cursor = max(cursor, b)
            if dur >= self.h.stall_s:
                sev = "high" if dur >= 60 or impatient or outcome.startswith("never") else "medium"
                done = [d for d in dels if u["ts"] <= d["ts"] <= stop]
                self.issue(sid, end, "backend_stall", "stall", sev, f"Request stalled {dur:.0f} s ({outcome})",
                           f"\"{_quote(u)}\" got only progress fillers ({len(waiting)}) for {dur:.0f} s before "
                           "answer audio began (or its first recorded speech bucket for older logs).",
                           {"at": _clock(u["ts"]), "seconds": round(dur, 1), "request": _quote(u),
                            "fillers": [f"{_clock(t['ts'])} {_quote(t)}" for t in waiting][:MAX_EVIDENCE_TURNS],
                            "impatience": [f"{_clock(t['ts'])} {_quote(t)}" for t in impatient],
                            "backend_responses": [f"{_clock(d['ts'])} {d.get('status')} {d.get('duration_s')} s"
                                                  for d in done],
                            "longest_silence_s": round(gap, 1), "outcome": outcome})
            if gap >= self.h.dead_air_s:
                self.issue(sid, end, "silent_wait", "dead_air", "high" if gap >= 3 * self.h.dead_air_s else "medium",
                           f"Dead air while waiting: {gap:.0f} s of silence",
                           "Nothing was said or done while a request was pending; say what is happening or give up "
                           "with an honest answer.",
                           {"from": _clock(gap_at), "seconds": round(gap, 1), "request": _quote(u)})

    def _dead_air(self, sid, turns, end) -> None:
        """Measured reply latency (LIVE_JARVES_SAID wait_s: user's last voice -> first reply audio)."""
        slow = [t for t in turns if t["role"] == "assistant" and (t.get("wait_s") or 0) > self.h.dead_air_s]
        if slow:
            worst = max(slow, key=lambda t: t["wait_s"])
            self.issue(sid, end, "dead_air", "dead_air", "high" if worst["wait_s"] > 3 * self.h.dead_air_s else "medium",
                       f"Dead air before replies: {len(slow)}x over {self.h.dead_air_s:g} s (worst {worst['wait_s']:.1f} s)",
                       "Time from the end of the user's speech to JARVES's first audio.",
                       {"turns": [f"{_clock(t['ts'])} {t['wait_s']:.1f} s: {_quote(t)}" for t in slow][:MAX_EVIDENCE_TURNS]})

    def _fillers_and_confusion(self, sid, turns, end) -> None:
        counts: dict[str, list[dict]] = {}
        for t in turns:
            if t["role"] != "assistant" or t.get("text") is None:
                continue
            for sen in _sentences(t["text"]):
                w = _words(sen)
                if len(w) == 1 and w[0] in ACK:
                    counts.setdefault(ACK[w[0]], []).append(t)
        confused = []
        for i, t in enumerate(turns):
            if t["role"] == "user" and t.get("text") and CONFUSION.search(t["text"]):
                prev = [p for p in turns[max(0, i - 4):i] if p["role"] == "assistant"]
                confused.append((t, prev))
        for word, hits in counts.items():
            if len(hits) < 3:
                continue
            asked = [c for c, prev in confused if word in c["text"] or any(word in (p.get("text") or "") for p in prev)
                     or difflib.SequenceMatcher(None, word, c["text"]).find_longest_match().size >= 2]
            self.issue(sid, end, f"filler:{word}", "filler", "high" if asked else "medium",
                       f"Confusing filler \"{word}\" said {len(hits)}x" + (" - the user asked what it means" if asked else ""),
                       f"JARVES answers actions with a bare \"{word}\". Users do not understand it as \"done\"; "
                       "say what was done instead (\"הגברתי לשבעים\").",
                       {"said": [f"{_clock(t['ts'])} {t['text']}" for t in hits][:MAX_EVIDENCE_TURNS],
                        "user_reaction": [f"{_clock(c['ts'])} {c['text']}" for c in asked]})
        for c, prev in confused:
            self.issue(sid, end, "user_confusion", "confusion", "medium",
                       "The user did not understand JARVES",
                       "The user said he did not understand / asked what was said.",
                       {"at": _clock(c["ts"]), "user": _quote(c), "before": [_quote(p) for p in prev[-2:]]})
        users = [t for t in turns if t["role"] == "user" and t.get("text") and len(t["text"]) > 5]
        for a, b in zip(users, users[1:]):
            if b["ts"] - a["ts"] < 120 and difflib.SequenceMatcher(None, a["text"], b["text"]).ratio() > 0.75:
                self.issue(sid, end, "user_repeated", "confusion", "medium", "The user had to repeat himself",
                           "Two nearly identical requests in a row: the first was misheard or not handled.",
                           {"first": f"{_clock(a['ts'])} {a['text']}", "again": f"{_clock(b['ts'])} {b['text']}"})

    def _tools(self, sid, tools, end) -> None:
        by_name: dict[str, list[dict]] = {}
        for t in tools:
            by_name.setdefault(t["name"], []).append(t)
        for name, calls in by_name.items():
            failed = [c for c in calls if c.get("ok") == 0]
            if failed:
                self.issue(sid, end, f"tool_fail:{name}", "tool", "high" if len(failed) >= 3 else "medium",
                           f"Tool {name} failed {len(failed)}x", "See the result codes; the user may have been told "
                           "it worked.", {"calls": [f"{_clock(c['ts'])} {c.get('code')}" for c in failed][:6]})
            lost = [c for c in calls if c.get("done_ts") is None and c.get("ok") is None]
            if lost:
                self.issue(sid, end, f"tool_no_result:{name}", "tool", "medium",
                           f"Tool {name} never returned ({len(lost)}x)", "LIVE_TOOL_CALL without a TOOL_RESULT.",
                           {"calls": [_clock(c["ts"]) for c in lost][:6]})
            slow = [c for c in calls if c.get("ok") is not None and (c.get("duration_s") or 0) > self.h.slow_tool_s]
            if slow:
                worst = max(c["duration_s"] for c in slow)
                self.issue(sid, end, f"slow_tool:{name}", "tool", "low", f"Slow tool {name}: {worst:.1f} s",
                           f"Over history.slow_tool_s ({self.h.slow_tool_s:g} s).",
                           {"calls": [f"{_clock(c['ts'])} {c['duration_s']:.1f} s" for c in slow][:6]})
            ts = sorted(c["ts"] for c in calls)
            burst = max((sum(1 for y in ts if x <= y <= x + 30) for x in ts), default=0)
            if burst >= 3:
                self.issue(sid, end, f"tool_repeat:{name}", "tool", "low", f"Tool {name} called {burst}x within 30 s",
                           "Repeated calls usually mean the first result was not understood.",
                           {"times": [_clock(x) for x in ts][:10]})

    def _backend(self, sid, dels, end) -> None:
        slow = [d for d in dels if (d.get("duration_s") or 0) > self.h.slow_backend_s]
        if slow:
            worst = max(d["duration_s"] for d in slow)
            self.issue(sid, end, "slow_backend", "backend", "medium", f"Slow backend response: {worst:.1f} s",
                       f"Over history.slow_backend_s ({self.h.slow_backend_s:g} s).",
                       {"responses": [f"{_clock(d['ts'])} {d['duration_s']} s {d.get('model')}" for d in slow][:6]})
        bad = [d for d in dels if d.get("status") in ("failed", "incomplete")]
        if bad:
            self.issue(sid, end, "backend_failed", "backend", "high", f"Backend response failed {len(bad)}x",
                       "response.failed / response.incomplete from the delegated backend.",
                       {"responses": [f"{_clock(d['ts'])} {d['status']}" for d in bad][:6]})

    def _events(self, sid, s, events, end) -> None:
        stats = json.loads(s.get("stats") or "{}")
        drops = stats.get("early_response_drops") or 0
        if drops >= self.h.early_drops:
            self.issue(sid, end, "early_response", "voice", "medium",
                       f"Model kept answering while the user was talking ({drops} audio chunks dropped)",
                       "GPT-Live started replies before the user finished; JARVES dropped them (LIVE_EARLY_RESPONSE). "
                       "Frequent drops cut answers or make the user wait.", {"early_response_drops": drops})
        nospeech = [e for e in events if e["code"] == "VOICE_NO_SPEECH"]
        if nospeech:
            self.issue(sid, end, "false_wake", "wake", "low", "False wake: conversation opened but nobody spoke",
                       "Raise voice.wake_threshold (`jarvis wake test --apply`) if this repeats.",
                       {"at": [_clock(e["ts"]) for e in nospeech]})
        for e in events:
            if e["code"] == "CALENDAR_DELETE_FORBIDDEN":
                continue                          # a correct guard refusal (older logs wrote it at ERROR)
            if e["level"] == "WARNING" and end - e["ts"] < 5 and s.get("end_reason") == "user":
                continue                          # shutdown noise (face window closed with the conversation)
            if e["level"] == "ERROR" or (e["level"] == "WARNING" and e["code"] not in ("AI_BUDGET",)):
                sev = "high" if e["level"] == "ERROR" else "low"
                self.issue(sid, end, f"event:{e['code']}", "error" if sev == "high" else "warning", sev,
                           f"{e['code']}: {e['msg'][:100]}", f"{e['component']} logged {e['level']}.",
                           {"at": _clock(e["ts"]), "fields": json.loads(e.get("fields") or "{}")})
            elif e["code"] == "AI_BUDGET":
                self.issue(sid, end, "budget_reached", "cost", "medium", "Daily AI limit reached mid-conversation",
                           e["msg"][:200], {"at": _clock(e["ts"])})


def run(store: Store, cfg, session_ids: list[str] | None = None, days: int = 30) -> list[dict]:
    """Diagnose the given conversations (default: all of the last `days`). Returns the issues raised/updated."""
    f = Finder(store, cfg)
    if session_ids is None:
        sessions = store.rows("SELECT * FROM sessions WHERE started > ? ORDER BY started",
                              (time.time() - days * 86400,))
    else:
        sessions = [s for s in (store.session(i) for i in session_ids) if s]
    for s in sessions:
        f.session(s)
        store.x("UPDATE sessions SET diagnosed = ? WHERE id = ?", (time.time(), s["id"]))
    for d in daily_costs(store, cfg.pricing, days):
        if d["total"] > cfg.pricing.daily_alert_usd:
            f.issue(f"day:{d['day']}", datetime.fromisoformat(d["day"]).timestamp() + 86399, f"cost_day:{d['day']}",
                    "cost", "medium", f"High cost on {d['day']}: ~${d['total']:.2f} (estimate)",
                    f"Above pricing.daily_alert_usd (${cfg.pricing.daily_alert_usd:.2f}).", {"day": d})
    store.commit()
    return f.found


# ---------------------------------------------------------------------- optional AI review (one request)
REVIEW_INSTRUCTIONS = """You review one conversation between a user and JARVES, a Hebrew-speaking home voice
assistant (GPT-Live voice, a backend model for lookups/tools). Find PROBLEMS in how JARVES behaved that a user
would notice: wrong or made-up answers, ignoring what the user asked, misunderstanding, contradictions, claiming
an action happened without a tool result, awkward or confusing wording, unnatural Hebrew, bad tone, repeating
itself, long waits without explanation, a tool or lookup that should have been used but was not.
Do NOT repeat problems in "Already found". Report only real problems you can quote; nothing if it went well.
Answer with JSON only: {"issues": [{"title": "short English title", "severity": "low|medium|high",
"detail": "what went wrong and what JARVES should do instead (1-2 sentences)", "quote": "the exact words"}]}
(at most 5 issues; empty list if none)."""
REVIEW_INPUT_CHARS = 9000
REVIEW_KINDS = ("live", "realtime")


def _mmss(t0: float, ts: float) -> str:
    s = max(0, int(ts - t0))
    return f"{s // 60:02d}:{s % 60:02d}"


def review_input(store: Store, sid: str) -> str | None:
    """Compact, redacted transcript for the AI review, or None (privacy mode / nothing to review)."""
    s = store.session(sid)
    if s is None or s.get("kind") not in REVIEW_KINDS:
        return None
    tl = store.timeline(sid)
    turns = tl["turns"]
    if sum(t["role"] == "user" for t in turns) < 2 or any(t.get("text") is None for t in turns):
        return None                                 # too short, or privacy mode: never send what we do not keep
    t0 = s["started"]
    items = [(t["ts"], f"[{_mmss(t0, t['ts'])}] {'USER' if t['role'] == 'user' else 'JARVES'}"
              + (f" (reply after {t['wait_s']:.1f} s)" if t.get("wait_s") else "") + f": {t['text']}") for t in turns]
    items += [(t["ts"], f"[{_mmss(t0, t['ts'])}] TOOL {t['name']} {t.get('args') or ''} -> "
               f"{'ok' if t.get('ok') else (t.get('code') or 'no result')} {t.get('duration_s') or ''}") for t in tl["tools"]]
    items += [(d["ts"], f"[{_mmss(t0, d['ts'])}] BACKEND {d.get('kind')} {d.get('status')} "
               f"{d.get('duration_s') or '?'} s, web searches {d.get('web_searches') or 0}") for d in tl["delegations"]]
    items += [(e["ts"], f"[{_mmss(t0, e['ts'])}] {e['level']} {e['code']}: {e['msg'][:120]}") for e in tl["events"]
              if e["level"] in ("WARNING", "ERROR")]
    text = "\n".join(line for _, line in sorted(items, key=lambda x: x[0]))
    text = re.sub(r"(?<![\w.+-])[\w.+-]+@[\w-]+\.[\w.-]+", "<email>", logs.redact(text))
    if len(text) > REVIEW_INPUT_CHARS:
        text = text[:REVIEW_INPUT_CHARS // 3] + "\n...\n" + text[-REVIEW_INPUT_CHARS * 2 // 3:]
    hits = {r["fingerprint"] for r in store.rows("SELECT fingerprint FROM issue_hits WHERE session_id = ?", (sid,))}
    known = [i["title"] for i in store.issues(None) if i["fingerprint"] in hits]
    return "Already found:\n" + ("\n".join(f"- {k}" for k in known) or "- nothing") + "\n\nConversation:\n" + text


REVIEW_FEATURE = "diagnosis_llm_pass"         # jarves.features switch (WP-I); its default is history.llm_review


def _features(store: Store, cfg):
    """The feature switches of the store's data dir (features.json, shared with the Core / CLI / dashboard)."""
    f = getattr(store, "_features", None)
    if f is None:
        try:
            from .features import Features
        except ImportError:
            return None
        f = store._features = Features(cfg, Path(store.path).parent) if store.path != ":memory:" else Features(cfg)
    return f


def review_skip_reason(store: Store, cfg, sid: str, *, have_client: bool = False) -> str | None:
    from .config import api_billing_allowed
    h = cfg.history
    feats = _features(store, cfg)
    if feats is not None and not feats.enabled(REVIEW_FEATURE):
        return f"the feature {REVIEW_FEATURE} is switched off (dashboard Features page)"
    if feats is None and not h.llm_review:
        return "history.llm_review is off"
    if cfg.log.privacy or logs.runtime_privacy():             # WP-K: the dashboard switch applies at once
        return "log.privacy is on (the review needs the transcript)"
    if not have_client and not api_billing_allowed(cfg.ai):
        return "no API key route (ai.connection / OPENAI_API_KEY)"
    if int(store.meta(f"llm_review:{date.today().isoformat()}") or 0) >= h.llm_review_daily_max:
        return f"daily maximum reached ({h.llm_review_daily_max})"
    if store.meta(f"llm_reviewed:{sid}"):
        return "already reviewed"
    # SW-V3 P0.1 (review Q05): "reviewed" used to be written BEFORE the request, so a failed or interrupted
    # review blocked every retry. Now: pending while it runs, reviewed only after its result is saved.
    pending = float(store.meta(f"llm_review_pending:{sid}") or 0)
    if pending and time.time() - pending < REVIEW_PENDING_STALE_S:
        return "review in progress"
    if int(store.meta(f"llm_review_attempts:{sid}") or 0) >= REVIEW_MAX_ATTEMPTS:
        return f"review failed {REVIEW_MAX_ATTEMPTS} times: {store.meta(f'llm_review_failed:{sid}') or 'unknown'}"
    return None


REVIEW_PENDING_STALE_S = 120.0    # a pending mark older than this belongs to an interrupted review (retry allowed)
REVIEW_MAX_ATTEMPTS = 2           # bounded retries: each attempt may be billed


def llm_review(store: Store, cfg, sid: str, *, client=None, budget=None) -> list[dict]:
    """Run the AI review for one finished conversation (blocking; see schedule_review). Returns new findings."""
    why = review_skip_reason(store, cfg, sid, have_client=client is not None)
    text = None if why else review_input(store, sid)
    if why or text is None:
        logs.debug("diagnose", "DIAGNOSE_LLM_SKIPPED", "AI review skipped", why=why or "nothing to review",
                   conversation_id=sid)
        return []
    model = cfg.history.llm_review_model or cfg.ai.text_model
    own_budget = None
    if budget is None:
        from .budget import Budget
        from .memory import MemoryStore
        own_budget = budget = Budget(MemoryStore(Path(store.path).parent / "memory.db"), cfg.ai)
    day_key = f"llm_review:{date.today().isoformat()}"
    attempts_key = f"llm_review_attempts:{sid}"
    with store._lock:
        store._set_meta(day_key, str(int(store.meta(day_key) or 0) + 1))     # an attempt may be billed: counted
        store._set_meta(attempts_key, str(int(store.meta(attempts_key) or 0) + 1))
        store._set_meta(f"llm_review_pending:{sid}", str(time.time()))
        store.commit()
    t0 = time.monotonic()
    try:
        if client is None:
            import openai
            client = openai.OpenAI(timeout=30)
        with budget.reserve("ai_requests"):
            r = client.responses.create(model=model, instructions=REVIEW_INSTRUCTIONS, input=text, store=False,
                                        max_output_tokens=900, reasoning={"effort": "low"})
    except Exception as e:                         # budget reached, network, API: the rules' findings stand
        logs.warn("diagnose", "DIAGNOSE_LLM_FAILED", "AI review failed", error_type=type(e).__name__,
                  conversation_id=sid)
        with store._lock:
            store._set_meta(f"llm_review_pending:{sid}", "")
            store._set_meta(f"llm_review_failed:{sid}", type(e).__name__)
            store.commit()
        return []
    finally:
        if own_budget is not None:
            own_budget.memory.close()
            own_budget.close()
    u = getattr(r, "usage", None)
    usage = {"in": int(getattr(u, "input_tokens", 0) or 0), "out": int(getattr(u, "output_tokens", 0) or 0),
             "cached": int(getattr(getattr(u, "input_tokens_details", None), "cached_tokens", 0) or 0)}
    store.x("INSERT INTO delegations (session_id, ts, kind, model, tier, status, duration_s, input_tokens, "
            "cached_tokens, output_tokens, web_searches) VALUES (?,?,?,?,?,?,?,?,?,?,0)",
            (sid, time.time(), "review", str(getattr(r, "model", "") or model), "", "completed",
             round(time.monotonic() - t0, 2), usage["in"], usage["cached"], usage["out"]))
    raw = (getattr(r, "output_text", "") or "").strip()
    try:
        found = json.loads(raw[raw.find("{"): raw.rfind("}") + 1] if "{" in raw else "{}").get("issues", [])
    except ValueError:
        found = []
    s = store.session(sid) or {}
    end = s.get("ended") or s.get("last_event") or time.time()
    f = Finder(store, cfg)
    for it in [x for x in found if isinstance(x, dict) and str(x.get("title", "")).strip()][:5]:
        title = " ".join(str(it["title"]).split())[:120]
        sev = str(it.get("severity", "low")).lower()
        f.issue(sid, end, "ai:" + hashlib.sha1(title.lower().encode("utf-8")).hexdigest()[:12], "ai_review",
                sev if sev in SEVERITY else "low", f"AI review: {title}", str(it.get("detail", ""))[:400],
                {"quote": str(it.get("quote", ""))[:300], "source": f"AI review ({model}); it can be wrong"})
    store._set_meta(f"llm_reviewed:{sid}", str(time.time()))      # only now: the outcome is saved
    store._set_meta(f"llm_review_pending:{sid}", "")
    store.commit()
    logs.info("diagnose", "DIAGNOSE_LLM_DONE", "AI review finished", conversation_id=sid,
              model=model, usage_in=usage["in"], usage_out=usage["out"], found=len(f.found))
    return f.found


def schedule_review(store: Store, cfg, sid: str) -> threading.Thread | None:
    """Start the AI review in a daemon thread (the store's writer thread must never wait on the network)."""
    if review_skip_reason(store, cfg, sid) is not None:
        return None
    def go() -> None:
        try:
            llm_review(store, cfg, sid)
        except Exception as e:                     # e.g. the store closed at Core shutdown mid-review
            print(f"jarves AI review: {type(e).__name__}: {str(e)[:120]}", flush=True)
    th = threading.Thread(target=go, name="jarves-ai-review", daemon=True)
    th.start()
    return th
