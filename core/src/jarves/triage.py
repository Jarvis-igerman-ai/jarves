"""Task triage from messages (S3 of docs/plans/SMART_CHAT_AND_TASK_TRIAGE_DESIGN_2026-10-26.md, #446).

`tasks_from_messages` is a lazy, read-only tool: the model proposes candidate tasks from messages it ALREADY read in this
turn; the server validates them (<= 10, titles trimmed and cleaned), drops what the owner skipped before, flags likely
duplicates (already in Any.do, or twice in the batch) as preselected Skip, and shows ONE `cards` block to the owner. The
tool result tells the model counts only, never a title. Nothing is written here: the owner's Add / Edit / Skip clicks come
back as ONE ui_action and the S2 path (anydo_create_tasks) asks for ONE approval showing the exact final titles.
Privacy: logs carry counts and codes only; the skipped memory holds salted hashes only."""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import secrets
import tempfile
import threading
import time
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable

from pydantic import BaseModel, Field

from . import calm, clilang, features as feat, logs, ui_blocks as ub
from .skills import Skill, Tool, ToolResult

MAX_TASKS = ub.MAX_BATCH                      # owner decision: 10 per batch
EXPIRY_DAYS = 90                              # owner decision: skipped tasks stay hidden 90 days, restorable
SIMILAR = 0.85
FILE = "triage_skipped.json"
# offered to the model only when the owner's words ask for tasks from messages (no always-on prompt cost)
LAZY = r"tasks? from|make (me )?tasks|create tasks|what (should|do|must) i (do|need)|משימות|מה (אני )?צריך|מה לעשות"


def norm(s: str) -> str:
    """NFKC, lowercase, no niqqud / marks, punctuation to spaces: the shape titles are compared in."""
    t = unicodedata.normalize("NFKC", str(s)).casefold()
    t = "".join(c for c in unicodedata.normalize("NFD", t) if not unicodedata.category(c).startswith("M"))
    return " ".join(re.sub(r"[^\w]+", " ", t).split())


def similar(a: str, b: str) -> bool:
    na, nb = norm(a), norm(b)
    return bool(na) and bool(nb) and (na == nb or difflib.SequenceMatcher(None, na, nb).ratio() >= SIMILAR)


class SkippedStore:
    """Local list of skipped candidates: salted hash of (normalized title + source ref) and the time. No titles, ever."""
    _lock = threading.Lock()

    def __init__(self, home: Path | None, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(home) / FILE if home is not None else None
        self.clock = clock

    def _load(self) -> dict:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8")) if self.path and self.path.exists() else {}
        except (OSError, ValueError):
            d = {}
        d = d if isinstance(d, dict) else {}
        d.setdefault("salt", secrets.token_hex(8))
        items = d.get("items") if isinstance(d.get("items"), dict) else {}
        cut = self.clock() - EXPIRY_DAYS * 86400
        d["items"] = {k: v for k, v in items.items() if isinstance(v, (int, float)) and v > cut}
        return d

    def _save(self, d: dict) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, self.path)

    @staticmethod
    def fp(salt: str, title: str, ref: str = "") -> str:
        return hashlib.sha256(f"{salt}|{norm(title)}|{ref}".encode("utf-8")).hexdigest()[:24]

    def add(self, pairs: list[tuple[str, str]]) -> int:
        with self._lock:
            d = self._load()
            for title, ref in pairs:
                d["items"][self.fp(d["salt"], title, ref)] = round(self.clock(), 1)
            self._save(d)
        logs.info("triage", "TRIAGE_SKIPPED", "tasks skipped", count=len(pairs))
        return len(pairs)

    def has(self, title: str, ref: str = "") -> bool:
        with self._lock:
            d = self._load()
        return self.fp(d["salt"], title, ref) in d["items"]

    def list(self) -> list[dict]:
        with self._lock:
            d = self._load()
        now = self.clock()
        return [{"id": k[:8], "age_days": int((now - v) // 86400), "expires_in_days": max(0, EXPIRY_DAYS - int((now - v) // 86400))}
                for k, v in sorted(d["items"].items(), key=lambda kv: -kv[1])]

    def restore(self, which: str) -> int:
        """`all` or an id (or its first characters) from list(): the task can be offered again."""
        with self._lock:
            d = self._load()
            gone = [k for k in d["items"] if which == "all" or (len(which) >= 4 and k.startswith(which.lower()))]
            for k in gone:
                del d["items"][k]
            self._save(d)
        logs.info("triage", "TRIAGE_RESTORED", "skipped tasks restored", count=len(gone))
        return len(gone)


class Cand(BaseModel):
    title: str = Field(..., max_length=120, description="imperative task, in the message's language")
    due: str | None = Field(None, max_length=10, description="YYYY-MM-DD or omit")
    person: str | None = Field(None, max_length=40)
    ref: str | None = Field(None, max_length=40, description="the message id from the tool result, as given")
    when: str | None = Field(None, max_length=16, description="the message date/time as shown, e.g. 2026-10-26 14:10")


class FromMessagesArgs(BaseModel):
    candidates: list[Cand] = Field(..., min_length=1, description=f"at most {MAX_TASKS} possible tasks found in the messages already read this turn")


TitlesFn = Callable[[], Awaitable[list[str] | None]]      # existing open Any.do titles, or None = not checked


async def _anydo_titles(registry, home) -> list[str] | None:
    """Read-only, only when Any.do is connected: the open task titles of the first page (100)."""
    from . import anydo
    if not anydo.connected(home):
        return None
    try:
        sk = registry._tools["anydo_tasks"][0]
        res = await sk._tasks(anydo.ListArgs(limit=100))
    except Exception:
        return None
    rows = res.data.get("tasks") if res.ok else None
    return [str(r.get("title", "")) for r in rows] if isinstance(rows, list) else None


def _ok_due(s: str | None) -> str:
    try:
        d = date.fromisoformat(s or "")
    except ValueError:
        return ""
    return s if d >= datetime.now().date() - timedelta(days=1) else ""


class TriageSkill(Skill):
    name = "triage"
    description = "Turn the messages just read into possible tasks for the owner to Add, Edit or Skip"

    def __init__(self, home: Path | None, titles_fn: TitlesFn | None = None, registry=None, clock=time.time) -> None:
        self.home, self.registry, self.clock = home, registry, clock
        self.titles_fn = titles_fn or (lambda: _anydo_titles(self.registry, self.home))

    def tools(self) -> list[Tool]:
        async def run(a: FromMessagesArgs) -> ToolResult:
            if len(a.candidates) > MAX_TASKS:
                return ToolResult(False, f"TRIAGE_TOO_MANY: at most {MAX_TASKS} tasks; pick the most important and call again.",
                                  code="TRIAGE_TOO_MANY")
            store, seen, cards, dropped, hidden, dups = SkippedStore(self.home, self.clock), [], [], 0, 0, 0
            existing = await self.titles_fn()
            for c in a.candidates:
                title = " ".join(ub.clean(c.title).split())
                if not title or not norm(title):
                    dropped += 1
                    continue
                ref = c.ref if c.ref and re.fullmatch(r"[A-Za-z0-9_.:@-]{1,40}", c.ref) else None
                if store.has(title, ref or ""):
                    hidden += 1
                    continue
                dup = any(similar(title, x) for x in seen) or any(similar(title, x) for x in existing or [])
                seen.append(title)
                dups += dup
                cards.append({"title": title, "due": _ok_due(c.due) or None, "person": " ".join(ub.clean(c.person or "").split()) or None,
                              "ref": ref, "when": c.when, "skip": dup})
            if not cards:
                return ToolResult(False, "TRIAGE_NONE: no usable task (empty titles or all skipped before). Tell the owner there is nothing new.",
                                  {"dropped": dropped, "already_skipped": hidden}, code="TRIAGE_NONE")
            raw = {"v": 1, "kind": "cards", "on": "anydo_create_tasks", "cards": cards,
                   "title": clilang.fmt("משימות אפשריות (לא מאושרות)", "Possible tasks (not confirmed)")}
            for _ in range(2):                       # a bad `when` / `ref` shape drops only that field, then once more
                good = ub.validate_blocks([raw], app=True)
                if good:
                    break
                for k in raw["cards"]:
                    k["when"] = None
            if not good:
                return ToolResult(False, "UI_BLOCK_INVALID: the tasks could not be shown; say so.", code="UI_BLOCK_INVALID")
            logs.info("triage", "TRIAGE_EXTRACT", "task candidates shown", ok=len(cards), dropped=dropped, duplicates=dups,
                      already_skipped=hidden, any_do_checked=existing is not None)
            return ToolResult(True, "Shown to the owner as cards (Add / Edit / Skip). Wait for the owner; do not repeat the titles.",
                              {"shown_to_owner": True, "cards": len(cards), "flagged_duplicates": dups, "already_skipped": hidden,
                               "dropped": dropped, "any_do_checked": existing is not None, "titles_withheld": True},
                              ui_blocks=good)
        return [Tool("tasks_from_messages", "Make tasks from messages just read: 'tasks from these', 'משימות מההודעות'.", run,
                     FromMessagesArgs, permission="read", lazy=LAZY)]


feat.register("task_triage", "משימות מהודעות", "Tasks from messages", False, needs=["anydo"], risk="low", skills=["triage"],
              about="turn the WhatsApp messages you asked me to read into possible tasks; you Add, Edit or Skip each one and approve "
                    "one batch for Any.do (nothing is written before you approve; skipped tasks are hidden for 90 days)",
              targets=calm.targets("test_triage.py"), permissions=["read", "writes approved each time"],
              settings="jarvis features on task_triage")


# ------------------------------------------------------------------------------------------ CLI
def add_cli(sub) -> None:
    h = sub.add_parser("tasks", help="tasks from messages: `jarvis tasks skipped list` / `restore <id|all>` (hashes only, 90 days)")
    h.add_argument("what", choices=["skipped"])
    h.add_argument("action", choices=["list", "restore"])
    h.add_argument("which", nargs="?", default="")


def cmd_tasks(a, cfg, home) -> int:
    st = SkippedStore(home)
    if a.action == "list":
        rows = st.list()
        print(json.dumps({"skipped": rows, "count": len(rows), "hidden_days": EXPIRY_DAYS}, ensure_ascii=False))
        return 0
    if not a.which:
        print(clilang.bi("ציינו מזהה מהרשימה או all. / Give an id from the list, or all."))
        return 2
    n = st.restore(a.which)
    print(clilang.bi(f"שוחזרו {n} משימות. / Restored {n} task(s).") if n else clilang.bi("לא נמצא. / Not found."))
    return 0 if n else 1
