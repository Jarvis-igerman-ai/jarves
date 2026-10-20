"""Shopping and to-do lists (V3 P1): named lists of items in memory.db `list_items`. Two built-in lists,
"shopping" (קניות) and "todo" (משימות); any other name works too. Add several items at once, read a list back,
tick items off (kept until cleared), remove one, clear the done ones or the whole list. Local only, no network.

Distinct from notes (free text documents) and reminders (the scheduler, at a time): a list is a set of short items.
Clearing from a model needs the user's approval on the screen (destructive).
"""
from __future__ import annotations

import time

from pydantic import BaseModel, Field

from . import logs
from .userdata import UserDB

MAX_ITEM = 120
MAX_ITEMS_PER_LIST = 200
ALIASES = {"shopping": "shopping", "shop": "shopping", "groceries": "shopping", "קניות": "shopping",
           "רשימת קניות": "shopping", "סופר": "shopping", "todo": "todo", "to-do": "todo", "to do": "todo",
           "tasks": "todo", "משימות": "todo", "רשימת משימות": "todo", "מטלות": "todo"}
TITLES = {"shopping": "רשימת קניות", "todo": "רשימת משימות"}


def list_key(name: str) -> str:
    n = " ".join(str(name or "").strip().lower().split())[:40]
    return ALIASES.get(n, n or "todo")


def title(key: str) -> str:
    return TITLES.get(key, key)


class Lists:
    def __init__(self, db: UserDB, *, clock=time.time) -> None:
        self.db, self.clock = db, clock

    def add(self, name: str, items: list[str], *, by: str = "user") -> dict:
        key = list_key(name)
        clean: list[str] = []
        for it in items or []:
            t = " ".join(str(it).split())[:MAX_ITEM]
            if t and t.lower() not in [c.lower() for c in clean]:
                clean.append(t)
        if not clean:
            raise ValueError("nothing to add")
        have = {r["text"].lower() for r in self.db.rows("SELECT text FROM list_items WHERE list_name=? AND done=0",
                                                         (key,))}
        if len(have) + len(clean) > MAX_ITEMS_PER_LIST:
            raise ValueError(f"a list holds at most {MAX_ITEMS_PER_LIST} items")
        added, dup = [], []
        for t in clean:
            if t.lower() in have:
                dup.append(t)
                continue
            self.db.x("INSERT INTO list_items (list_name, text, done, created) VALUES (?,?,0,?)",
                      (key, t, self.clock()))
            added.append(t)
        logs.info("lists", "LIST_ADDED", "list items added", list=key, n=len(added), by=by)
        return {"list": key, "added": added, "already_there": dup}

    def items(self, name: str, include_done: bool = False) -> list[dict]:
        sql = "SELECT * FROM list_items WHERE list_name=?" + ("" if include_done else " AND done=0") + " ORDER BY id"
        return self.db.rows(sql, (list_key(name),))

    def names(self) -> list[dict]:
        return self.db.rows("SELECT list_name AS name, SUM(done=0) AS open, SUM(done=1) AS done FROM list_items "
                            "GROUP BY list_name ORDER BY list_name")

    def _match(self, key: str, text: str, done: int) -> list[dict]:
        t = " ".join(str(text or "").split()).lower()
        rows = self.db.rows("SELECT * FROM list_items WHERE list_name=? AND done=? ORDER BY id", (key, done))
        exact = [r for r in rows if r["text"].lower() == t]
        return exact or [r for r in rows if t and (t in r["text"].lower() or r["text"].lower() in t)]

    def check(self, name: str, text: str, *, by: str = "user") -> list[str]:
        """Tick items off (the words may match several, e.g. "milk" ticks "milk 3%")."""
        key = list_key(name)
        hit = self._match(key, text, 0)
        for r in hit:
            self.db.x("UPDATE list_items SET done=1, done_at=? WHERE id=?", (self.clock(), r["id"]))
        logs.info("lists", "LIST_CHECKED", "list items ticked off", list=key, n=len(hit), by=by)
        return [r["text"] for r in hit]

    def remove(self, name: str, text: str, *, by: str = "user") -> list[str]:
        key = list_key(name)
        hit = self._match(key, text, 0) or self._match(key, text, 1)
        for r in hit:
            self.db.x("DELETE FROM list_items WHERE id=?", (r["id"],))
        logs.info("lists", "LIST_REMOVED", "list items removed", list=key, n=len(hit), by=by)
        return [r["text"] for r in hit]

    def clear(self, name: str, *, only_done: bool = False, by: str = "user") -> int:
        key = list_key(name)
        where = " AND done=1" if only_done else ""
        n = len(self.db.rows("SELECT id FROM list_items WHERE list_name=?" + where, (key,)))
        self.db.x("DELETE FROM list_items WHERE list_name=?" + where, (key,))
        logs.info("lists", "LIST_CLEARED", "list cleared", list=key, n=n, only_done=only_done, by=by)
        return n


# ---------------------------------------------------------------------- voice / agent tools
class AddArgs(BaseModel):
    list_name: str = Field("shopping", max_length=40, description="'shopping' (קניות), 'todo' (משימות) or any name")
    items: list[str] = Field(..., min_length=1, max_length=30, description="short items, one per entry")


class ShowArgs(BaseModel):
    list_name: str = Field("", max_length=40, description="'' = which lists exist")
    include_done: bool = False


class ItemArgs(BaseModel):
    list_name: str = Field("shopping", max_length=40)
    item: str = Field(..., min_length=1, max_length=MAX_ITEM)


class ClearArgs(BaseModel):
    list_name: str = Field("shopping", max_length=40)
    only_done: bool = Field(True, description="True = clear the ticked-off items only; False = the whole list")


def make_skill(lists: Lists):
    from .skills import Skill, Tool, ToolResult

    class ListSkill(Skill):
        name = "lists"
        description = "shopping and to-do lists: add items, read them back, tick off, remove, clear"

        def tools(self) -> list:
            async def add(a: AddArgs):
                try:
                    r = lists.add(a.list_name, a.items, by="voice")
                except ValueError as e:
                    return ToolResult(False, str(e), code="LIST_BAD")
                msg = f"added to {title(r['list'])}: {', '.join(r['added'])}" if r["added"] else "already on the list"
                return ToolResult(True, msg, r)

            async def show(a: ShowArgs):
                if not a.list_name.strip():
                    names = lists.names()
                    return ToolResult(True, f"{len(names)} list(s)" if names else "no lists yet", {"lists": names})
                rows = lists.items(a.list_name, a.include_done)
                key = list_key(a.list_name)
                data = {"list": key, "items": [{"text": r["text"], "done": bool(r["done"])} for r in rows]}
                if not rows:
                    return ToolResult(True, f"{title(key)} is empty", data)
                return ToolResult(True, f"{title(key)}: " + ", ".join(r["text"] for r in rows), data)

            async def check(a: ItemArgs):
                hit = lists.check(a.list_name, a.item, by="voice")
                return (ToolResult(True, "ticked off: " + ", ".join(hit), {"items": hit}) if hit
                        else ToolResult(False, "no such item on the list", code="LIST_ITEM_NOT_FOUND"))

            async def remove(a: ItemArgs):
                hit = lists.remove(a.list_name, a.item, by="voice")
                return (ToolResult(True, "removed: " + ", ".join(hit), {"items": hit}) if hit
                        else ToolResult(False, "no such item on the list", code="LIST_ITEM_NOT_FOUND"))

            async def clear(a: ClearArgs):
                n = lists.clear(a.list_name, only_done=a.only_done, by="voice")
                return ToolResult(True, f"cleared {n} item(s)", {"cleared": n})

            async def clear_impact(a: ClearArgs):
                rows = lists.items(a.list_name, include_done=True)
                n = len([r for r in rows if r["done"]]) if a.only_done else len(rows)
                return n, f"לנקות {n} פריטים מ{title(list_key(a.list_name))}? / Clear {n} item(s)?"

            return [
                Tool("list_add", "Add items to the shopping list or the to-do list ('תוסיף חלב ולחם לרשימת הקניות').",
                     add, AddArgs, permission="action"),
                Tool("list_show", "Read a list back (shopping, todo, or any name), or say which lists exist.", show,
                     ShowArgs, permission="read"),
                Tool("list_check", "Tick an item off a list as bought / done.", check, ItemArgs, permission="action"),
                Tool("list_remove", "Take one item off a list.", remove, ItemArgs, permission="action"),
                Tool("list_clear", "Clear the ticked-off items (or the whole list) after the user approves.", clear,
                     ClearArgs, permission="destructive", impact=clear_impact),
            ]

    return ListSkill()


ALLOWED_ACTIONS = {"list_add", "list_check", "list_remove"}


# ---------------------------------------------------------------------- dashboard (V4 P0.3: the Lists page)
def _ctx_lists(ctx) -> Lists:
    from .userdata import ctx_db
    ls = getattr(ctx, "_lists", None)
    if ls is None:
        ls = Lists(ctx_db(ctx))              # the same store the voice tools use
        ctx._lists = ls
    return ls


def api_lists(ctx, m, body, q=None):
    ls = _ctx_lists(ctx)
    name = str(((q or {}).get("name") or [""])[0]).strip()
    out = {"lists": [{**n, "title": title(n["name"])} for n in ls.names()]}
    if name:
        out["list"] = list_key(name)
        out["items"] = [{"id": r["id"], "text": r["text"], "done": bool(r["done"])}
                        for r in ls.items(name, include_done=True)]
    return 200, out


def api_list_add(ctx, m, body):
    b = body or {}
    items = b.get("items")
    if isinstance(items, str):
        items = items.replace("\n", ",").split(",")
    try:
        return 200, _ctx_lists(ctx).add(str(b.get("list_name", "shopping")), items or [], by="dashboard")
    except ValueError as e:
        return 400, {"error": str(e)}


def api_list_op(ctx, m, body):
    """check / remove one item; clear asks for approval: the page confirms first and sends approved=true."""
    ls, b = _ctx_lists(ctx), body or {}
    name, op = str(b.get("list_name", "")), b.get("op")
    if op in ("check", "remove"):
        hit = (ls.check if op == "check" else ls.remove)(name, str(b.get("item", "")), by="dashboard")
        return (200, {"items": hit}) if hit else (404, {"error": "no such item on the list"})
    if op == "clear":
        if b.get("approved") is not True:
            return 400, {"error": "clearing needs your approval", "needs_approval": True}
        return 200, {"cleared": ls.clear(name, only_done=bool(b.get("only_done", True)), by="dashboard")}
    return 400, {"error": "op must be check, remove or clear"}


def dashboard_routes(register) -> None:
    register("GET", r"/api/lists", api_lists)
    register("POST", r"/api/lists", api_list_add)
    register("POST", r"/api/lists/op", api_list_op)
