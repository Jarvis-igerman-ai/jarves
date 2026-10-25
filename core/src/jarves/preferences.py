"""Adaptive Memory (V5 pillar 3, software slice): preferences the owner STATES, learned only after an explicit yes.

Flow: the owner says a preference ("I prefer short answers in the morning") -> the model calls `pref_suggest` ->
the item is stored as `proposed` (visible on the Learned preferences page, NEVER used anywhere yet) -> the owner
confirms (spoken/typed yes on the approval, or the Confirm button) -> `confirmed`. Declining keeps only the
normalised text so the same suggestion is not asked again (removable). Nothing is inferred silently: a suggestion
must carry the owner's own words as evidence, sensitive text (passwords, card/ID/long numbers, health and money
details) is refused and NOT stored, proposals are capped and expire, and conflicts are shown, never auto-merged.
Every item is listable, editable, removable (dashboard, CLI, voice). Local only, in memory.db `learned_prefs`.
"""
from __future__ import annotations

import difflib
import re
import time

from pydantic import BaseModel, Field

from . import calm
from . import features as feat
from . import logs
from .userdata import UserDB

FEATURE = "prefs_in_prompt"
feat.register(FEATURE, "שימוש בהעדפות בכל שיחה", "Use learned preferences in conversations", True, applies="next_session",
              about="your confirmed preferences (only those you said yes to) are added to the instructions of every "
                    "conversation within a small size limit; switch off to stop",
              targets=calm.targets('test_v5_pillars.py', 'test_v5_followups.py'))
PROMPT_TOKENS = 120               # estimated-token budget of the preference block in the instructions
PROMPT_HEAD = "Preferences the owner confirmed (follow them quietly, never recite this list):"

MAX_TEXT = 200
MAX_EVIDENCE = 200
MAX_PROPOSED = 20                 # pending suggestions at once; more are refused until some are answered
PROPOSAL_TTL_S = 14 * 86400       # an unanswered suggestion disappears (never silently turns into a fact)
STALE_S = 180 * 86400             # a confirmed item not re-confirmed this long is not used until reaffirmed
CONFLICT_RATIO = 0.55
TOPICS = ("voice", "schedule", "food", "music", "communication", "other")
_PUNCT = re.compile(r"[^\w\s]+")
_NIQQUD = re.compile(r"[֑-ׇ]")
# Text that must never be learned: credentials, card/ID/phone-like numbers, and health or money details.
_SENSITIVE = re.compile(
    r"סיסמ|קוד\s|קוד$|הקוד|פין|אשראי|תעודת\s+זהות|ת\"ז|חשבון\s+(?:ה)?בנק|משכורת|שכר|חוב|אבחנ|תרופ|מחלה|"
    r"password|passcode|\bpin\b|credit\s*card|\bssn\b|\biban\b|salary|diagnos|medicat|prescription|"
    r"\d{6,}|\d[\d -]{10,}\d", re.I)


def looks_sensitive(text: str) -> bool:
    return bool(_SENSITIVE.search(text or ""))


def normalize(text: str) -> str:
    return " ".join(_PUNCT.sub(" ", _NIQQUD.sub("", str(text).casefold())).split())


def _clean(text: str, n: int) -> str:
    return " ".join(str(text or "").split())[:n]


class Preferences:
    def __init__(self, db: UserDB, *, clock=time.time) -> None:
        self.db, self.clock = db, clock

    # ------------------------------------------------------------------ housekeeping
    def _expire(self) -> None:
        self.db.x("DELETE FROM learned_prefs WHERE state='proposed' AND created < ?", (self.clock() - PROPOSAL_TTL_S,))

    def _view(self, r: dict) -> dict:
        r = dict(r)
        r.pop("norm", None)
        r["stale"] = r["state"] == "confirmed" and (self.clock() - (r["confirmed_at"] or r["updated"])) > STALE_S
        return r

    # ------------------------------------------------------------------ learning
    def suggest(self, text: str, evidence: str, topic: str = "other", *, by: str = "voice") -> dict:
        """Store a PROPOSAL. Raises ValueError (nothing stored) for empty/sensitive/unsupported suggestions."""
        t, ev = _clean(text, MAX_TEXT), _clean(evidence, MAX_EVIDENCE)
        if not t:
            raise ValueError("nothing to remember")
        if not ev:
            raise ValueError("a suggestion needs the owner's own words as evidence")
        if looks_sensitive(t) or looks_sensitive(ev):
            logs.info("prefs", "PREF_REFUSED_SENSITIVE", "sensitive preference not stored", by=by)
            raise ValueError("this looks sensitive (secret, number, health or money detail): not stored")
        self._expire()
        norm = normalize(t)
        topic = topic if topic in TOPICS else "other"
        same = self.db.row("SELECT * FROM learned_prefs WHERE norm=?", (norm,))
        if same:
            return {"item": self._view(same), "status": "known_" + same["state"]}
        if len(self.db.rows("SELECT id FROM learned_prefs WHERE state='proposed'")) >= MAX_PROPOSED:
            raise ValueError("too many unanswered suggestions: answer or remove some first")
        now = self.clock()
        cur = self.db.x("INSERT INTO learned_prefs (text, norm, topic, state, evidence, source, created, updated) "
                        "VALUES (?,?,?,?,?,?,?,?)", (t, norm, topic, "proposed", ev, by, now, now))
        logs.info("prefs", "PREF_PROPOSED", "preference proposed", id=cur.lastrowid, by=by)
        return {"item": self._view(self.get(cur.lastrowid)), "status": "proposed"}

    def add_confirmed(self, text: str, topic: str = "other", *, by: str = "dashboard") -> dict:
        """The owner typed it themselves (dashboard/CLI): explicit by definition, stored as confirmed."""
        r = self.suggest(text, "typed by the owner", topic, by=by)
        if r["status"] in ("proposed", "known_proposed", "known_declined"):
            r["item"] = self.confirm(r["item"]["id"], by=by)["item"]
            r["status"] = "confirmed"
        return r

    def confirm(self, pid: int, *, by: str = "user") -> dict:
        r = self.get(pid)
        if not r:
            raise KeyError(pid)
        now = self.clock()
        self.db.x("UPDATE learned_prefs SET state='confirmed', confirmed_at=?, updated=? WHERE id=?", (now, now, pid))
        logs.info("prefs", "PREF_CONFIRMED", "preference confirmed", id=pid, by=by)
        return {"item": self._view(self.get(pid)), "conflicts": [self._view(c) for c in self.conflicts(pid)]}

    def decline(self, pid: int, *, by: str = "user") -> bool:
        n = self.db.x("UPDATE learned_prefs SET state='declined', updated=? WHERE id=?", (self.clock(), pid)).rowcount
        logs.info("prefs", "PREF_DECLINED", "preference declined", id=pid, by=by)
        return bool(n)

    def reaffirm(self, pid: int, *, by: str = "user") -> bool:
        now = self.clock()
        n = self.db.x("UPDATE learned_prefs SET confirmed_at=?, updated=? WHERE id=? AND state='confirmed'",
                      (now, now, pid)).rowcount
        logs.info("prefs", "PREF_REAFFIRMED", "preference reaffirmed", id=pid, by=by)
        return bool(n)

    def edit(self, pid: int, text: str, *, by: str = "user", repropose: bool = False) -> dict:
        """Reword an item. `repropose` (the model's edit, V5 sec F2): a confirmed item goes back to 'proposed' so the
        new wording needs a fresh confirm before it can reach the instructions."""
        t = _clean(text, MAX_TEXT)
        if not t or looks_sensitive(t):
            raise ValueError("empty or sensitive text: not stored")
        norm = normalize(t)
        other = self.db.row("SELECT id FROM learned_prefs WHERE norm=? AND id<>?", (norm, pid))
        if other:
            raise ValueError("the same preference already exists")
        cur = self.get(pid)
        if not cur:
            raise KeyError(pid)
        if repropose and cur["state"] == "confirmed":
            self.db.x("UPDATE learned_prefs SET text=?, norm=?, state='proposed', confirmed_at=0, updated=? WHERE id=?",
                      (t, norm, self.clock(), pid))
            logs.info("prefs", "PREF_REPROPOSED", "a confirmed preference was reworded: it needs a new confirm", id=pid, by=by)
            return self._view(self.get(pid))
        self.db.x("UPDATE learned_prefs SET text=?, norm=?, updated=? WHERE id=?", (t, norm, self.clock(), pid))
        logs.info("prefs", "PREF_EDITED", "preference edited", id=pid, by=by)
        return self._view(self.get(pid))

    def remove(self, pid: int, *, by: str = "user") -> bool:
        n = self.db.x("DELETE FROM learned_prefs WHERE id=?", (pid,)).rowcount
        logs.info("prefs", "PREF_REMOVED", "preference removed", id=pid, by=by)
        return bool(n)

    def clear(self, *, by: str = "user") -> int:
        n = self.db.x("DELETE FROM learned_prefs").rowcount
        logs.info("prefs", "PREF_CLEARED", "all preferences removed", n=n, by=by)
        return n

    # ------------------------------------------------------------------ reading
    def get(self, pid: int) -> dict | None:
        return self.db.row("SELECT * FROM learned_prefs WHERE id=?", (pid,))

    def items(self, state: str = "") -> list[dict]:
        self._expire()
        rows = self.db.rows("SELECT * FROM learned_prefs" + (" WHERE state=?" if state else "") + " ORDER BY id",
                            (state,) if state else ())
        return [self._view(r) for r in rows]

    def conflicts(self, pid: int) -> list[dict]:
        """Confirmed items on the same topic that look similar but are not the same text: shown to the owner."""
        me = self.get(pid)
        if not me:
            return []
        return [r for r in self.db.rows("SELECT * FROM learned_prefs WHERE state='confirmed' AND topic=? AND id<>?",
                                        (me["topic"], pid))
                if difflib.SequenceMatcher(None, me["norm"], r["norm"]).ratio() >= CONFLICT_RATIO]

    def for_prompt(self, limit: int = 20) -> list[str]:
        """What may influence behaviour: confirmed, not stale. Proposals and declined items never appear."""
        return [r["text"] for r in self.items("confirmed") if not r["stale"]][:limit]


def prompt_block(prefs: "Preferences", limit_tokens: int = PROMPT_TOKENS) -> str:
    """The instruction block: confirmed, not stale, not sensitive items, newest first, within the token budget."""
    from .context_trace import estimate_tokens
    rows = sorted((r for r in prefs.items("confirmed") if not r["stale"] and not looks_sensitive(r["text"])),
                  key=lambda r: -(r["confirmed_at"] or r["updated"]))
    out, used = [], estimate_tokens(PROMPT_HEAD)
    for r in rows:
        cost = estimate_tokens(r["text"]) + 2
        if used + cost > limit_tokens:
            break
        out.append("- " + r["text"])
        used += cost
    return PROMPT_HEAD + "\n" + "\n".join(out) if out else ""


def _block(prefs, feats) -> str:
    try:
        if feats is not None and not feats.active(FEATURE):
            return ""
        return prompt_block(prefs)
    except Exception:
        logs.error("prefs", "PREF_PROMPT_FAILED", "preferences not added to the instructions", exc=True)
        return ""


def attach_live(live, prefs: "Preferences", feats=None) -> None:
    """Voice: add the block to the instructions built at the start of every conversation."""
    orig = getattr(live, "session_config", None)
    if orig is None:
        return

    def session_config() -> dict:
        cfg = orig()
        extra = _block(prefs, feats)
        if extra:
            cfg["instructions"] = f"{cfg.get('instructions', '')}\n\n{extra}"
        return cfg
    live.session_config = session_config


def augment_instructions(agent, prefs: "Preferences", feats=None) -> None:
    """Backend (typed chat and delegation): recomputed for every request, so a change applies at once."""
    base = agent.instructions

    def instructions() -> str:
        extra = _block(prefs, feats)
        return f"{base()}\n\n{extra}" if extra else base()
    agent.instructions = instructions


# ---------------------------------------------------------------------- voice / agent tools
class SuggestArgs(BaseModel):
    text: str = Field(..., min_length=3, max_length=MAX_TEXT)
    evidence: str = Field(..., min_length=3, max_length=MAX_EVIDENCE)
    topic: str = Field("other", max_length=20)


class IdArgs(BaseModel):
    id: int = Field(..., ge=1)


class EditArgs(BaseModel):
    id: int = Field(..., ge=1)
    text: str = Field(..., min_length=3, max_length=MAX_TEXT)


class NoArgs(BaseModel):
    pass


def make_skill(prefs: Preferences):
    from .skills import Skill, Tool, ToolResult

    class PrefSkill(Skill):
        name = "preferences"
        description = "learned preferences: suggest what the owner stated, confirm, list, edit, forget"

        def tools(self) -> list:
            async def suggest(a: SuggestArgs):
                try:
                    r = prefs.suggest(a.text, a.evidence, a.topic, by="voice")
                except ValueError as e:
                    return ToolResult(False, str(e), code="PREF_REFUSED")
                if r["status"] == "proposed":
                    return ToolResult(True, "proposed, not saved yet: ask the owner whether to remember it", r)
                return ToolResult(True, "already " + r["status"].split("_")[1], r)

            async def confirm(a: IdArgs):
                if not prefs.get(a.id):
                    return ToolResult(False, "no such preference", code="PREF_NOT_FOUND")
                r = prefs.confirm(a.id, by="voice")
                msg = "remembered"
                if r["conflicts"]:
                    msg += "; it may conflict with: " + "; ".join(c["text"] for c in r["conflicts"])
                return ToolResult(True, msg, r)

            async def decline(a: IdArgs):
                return (ToolResult(True, "will not remember it") if prefs.decline(a.id, by="voice")
                        else ToolResult(False, "no such preference", code="PREF_NOT_FOUND"))

            async def show(_a: NoArgs):
                rows = prefs.items()
                shown = [r for r in rows if r["state"] != "declined"]
                data = {"items": [{k: r[k] for k in ("id", "text", "topic", "state", "stale")} for r in shown]}
                if not shown:
                    return ToolResult(True, "no learned preferences", data)
                return ToolResult(True, "; ".join(f"{r['text']} ({r['state']})" for r in shown), data)

            async def edit(a: EditArgs):
                try:
                    return ToolResult(True, "updated; it needs the owner's confirmation again before it is remembered", {"item": prefs.edit(a.id, a.text, by="voice", repropose=True)})
                except KeyError:
                    return ToolResult(False, "no such preference", code="PREF_NOT_FOUND")
                except ValueError as e:
                    return ToolResult(False, str(e), code="PREF_REFUSED")

            async def forget(a: IdArgs):
                return (ToolResult(True, "forgotten") if prefs.remove(a.id, by="voice")
                        else ToolResult(False, "no such preference", code="PREF_NOT_FOUND"))

            async def forget_impact(_a: IdArgs):
                return 1, "לשכוח את ההעדפה? / Forget this preference?"

            def confirm_meta(a: IdArgs) -> dict:
                r = prefs.get(a.id)           # the popup shows WHAT would be remembered, not just "a preference"
                return {"details": {"preference": str(r["text"])[:MAX_TEXT]}} if r else {}

            return [
                Tool("pref_suggest", "Owner STATED a lasting preference: propose it, then ask. 'I always prefer', 'אני תמיד מעדיף'. Never secrets, health or money.",
                     suggest, SuggestArgs, permission="action"),
                Tool("pref_confirm", "Owner said yes: 'yes keep that preference', 'כן תשמור את ההעדפה'.", confirm, IdArgs,
                     permission="action", confirm="לזכור את ההעדפה הזאת? / Remember this preference?",
                     approval_meta=confirm_meta),
                Tool("pref_decline", "Owner said no: 'no do not keep that preference', 'לא תשמור את ההעדפה'.", decline, IdArgs, permission="action"),
                Tool("pref_show", "Read learned preferences: 'what preferences did you learn', 'אילו העדפות למדת'.", show, NoArgs,
                     permission="read"),
                Tool("pref_edit", "Reword a preference: 'change the preference', 'תשנה את ההעדפה'.", edit, EditArgs, permission="action"),
                Tool("pref_forget", "Forget a preference (asks): 'forget that preference', 'תשכח את ההעדפה'.", forget, IdArgs,
                     permission="destructive", impact=forget_impact),
            ]

    return PrefSkill()


ALLOWED_ACTIONS = {"pref_suggest", "pref_confirm", "pref_decline", "pref_edit"}


# ---------------------------------------------------------------------- dashboard: the Learned preferences page
def _ctx(ctx) -> Preferences:
    from .userdata import ctx_db
    p = getattr(ctx, "_prefs", None)
    if p is None:
        p = Preferences(ctx_db(ctx))
        ctx._prefs = p
    return p


def api_prefs(ctx, m, body, q=None):
    p = _ctx(ctx)
    rows = p.items()
    from . import features as _f
    on = _f._ctx_features(ctx).enabled(FEATURE)
    return 200, {"in_prompt": on, "prompt_text": prompt_block(p) if on else "",
                 "items": [r for r in rows if r["state"] == "confirmed"],
                 "proposed": [r for r in rows if r["state"] == "proposed"],
                 "declined": [r for r in rows if r["state"] == "declined"], "topics": list(TOPICS)}


def api_pref_add(ctx, m, body):
    b = body or {}
    try:
        return 200, _ctx(ctx).add_confirmed(str(b.get("text", "")), str(b.get("topic", "other")), by="dashboard")
    except ValueError as e:
        return 400, {"error": str(e)}


def api_pref_op(ctx, m, body):
    p, b = _ctx(ctx), body or {}
    op = b.get("op")
    try:
        pid = int(b.get("id", 0))
    except (TypeError, ValueError):
        return 400, {"error": "id must be a number"}
    if not p.get(pid):
        return 404, {"error": "no such preference"}
    try:
        if op == "confirm":
            return 200, p.confirm(pid, by="dashboard")
        if op == "decline":
            return 200, {"ok": p.decline(pid, by="dashboard")}
        if op == "reaffirm":
            return 200, {"ok": p.reaffirm(pid, by="dashboard")}
        if op == "edit":
            return 200, {"item": p.edit(pid, str(b.get("text", "")), by="dashboard")}
        if op == "remove":
            return 200, {"ok": p.remove(pid, by="dashboard")}
    except ValueError as e:
        return 400, {"error": str(e)}
    return 400, {"error": "op must be confirm, decline, reaffirm, edit or remove"}


def dashboard_routes(register) -> None:
    register("GET", r"/api/prefs", api_prefs)
    register("POST", r"/api/prefs", api_pref_add)
    register("POST", r"/api/prefs/op", api_pref_op)
