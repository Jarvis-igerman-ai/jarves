"""Notes (SW-V2 WP-M3, spec 6.5 / 9): first-class persistent records with title, text, tags and created/updated
timestamps in memory.db `notes`. Searchable (title, text, tags), editable, exportable as Markdown (one note or all,
into the file sandbox `exports/notes/` or as text for a dashboard download). Deleting goes through the registry's
destructive-action policy: from a model it always needs the user's approval on the screen.

Distinct from `remember` (short facts JARVES uses in every conversation) and USER.md/MEMORY.md (persona files):
a note is the user's own document, recalled only when asked.
"""
from __future__ import annotations

import re
import time
from datetime import datetime

from pydantic import BaseModel, Field

from . import logs
from .userdata import UserDB, ctx_db

MAX_TITLE = 120
MAX_TEXT = 20_000
MAX_TAGS = 10
_TAG = re.compile(r"[^\w\-]+")


def norm_tags(tags) -> list[str]:
    if isinstance(tags, str):
        tags = re.split(r"[,#\s]+", tags)
    out: list[str] = []
    for t in tags or []:
        t = _TAG.sub("", str(t).strip().lower())[:30]
        if t and t not in out:
            out.append(t)
    return out[:MAX_TAGS]


def _tags_col(tags: list[str]) -> str:
    return "," + ",".join(tags) + "," if tags else ""


def _view(r: dict) -> dict:
    return {**r, "tags": [t for t in (r.get("tags") or "").split(",") if t]}


def slug(title: str) -> str:
    s = re.sub(r"[^\w\-]+", "-", str(title).strip().lower(), flags=re.U).strip("-.")
    return (s or "note")[:60]


class Notes:
    def __init__(self, db: UserDB, *, clock=time.time) -> None:
        self.db, self.clock = db, clock

    def add(self, title: str, text: str = "", tags=None, *, by: str = "user") -> dict:
        title, text = " ".join(str(title or "").split())[:MAX_TITLE], str(text or "").strip()
        if not title and not text:
            raise ValueError("a note needs a title or text")
        if len(text) > MAX_TEXT:
            raise ValueError(f"note longer than {MAX_TEXT} characters")
        title = title or " ".join(text.split())[:60]
        now = self.clock()
        cur = self.db.x("INSERT INTO notes (title, text, tags, created, updated) VALUES (?,?,?,?,?)",
                        (title, text, _tags_col(norm_tags(tags)), now, now))
        logs.info("notes", "NOTE_ADDED", "note added", note=cur.lastrowid, by=by)
        return self.get(cur.lastrowid)

    def get(self, nid: int) -> dict | None:
        r = self.db.row("SELECT * FROM notes WHERE id=?", (int(nid),))
        return _view(r) if r else None

    def search(self, q: str = "", tag: str = "", limit: int = 50) -> list[dict]:
        sql, args = "SELECT * FROM notes WHERE 1=1", []
        for word in str(q or "").split()[:6]:
            sql += " AND (title LIKE ? OR text LIKE ? OR tags LIKE ?)"
            args += [f"%{word}%", f"%{word}%", f"%,{word.lower().lstrip('#')}%"]
        t = norm_tags(tag)
        if t:
            sql += " AND tags LIKE ?"
            args.append(f"%,{t[0]},%")
        sql += " ORDER BY updated DESC, id DESC LIMIT ?"
        args.append(int(limit))
        return [_view(r) for r in self.db.rows(sql, tuple(args))]

    def edit(self, nid: int, *, title: str | None = None, text: str | None = None, tags=None,
             append: str | None = None, by: str = "user") -> dict:
        n = self.get(nid)
        if n is None:
            raise KeyError(nid)
        new_title = n["title"] if title is None else " ".join(str(title).split())[:MAX_TITLE] or n["title"]
        new_text = n["text"] if text is None else str(text).strip()
        if append:
            new_text = (new_text.rstrip() + "\n" + str(append).strip()).strip()
        if len(new_text) > MAX_TEXT:
            raise ValueError(f"note longer than {MAX_TEXT} characters")
        new_tags = n["tags"] if tags is None else norm_tags(tags)
        self.db.x("UPDATE notes SET title=?, text=?, tags=?, updated=? WHERE id=?",
                  (new_title, new_text, _tags_col(new_tags), self.clock(), n["id"]))
        logs.info("notes", "NOTE_EDITED", "note edited", note=n["id"], by=by)
        return self.get(n["id"])

    def delete(self, nid: int, *, by: str) -> bool:
        n = self.get(nid)
        if n is None:
            return False
        self.db.x("DELETE FROM notes WHERE id=?", (n["id"],))
        self.db.audit(by, "note_delete", n["id"], {"title": n["title"]})
        logs.info("notes", "NOTE_DELETED", "note deleted", note=n["id"], by=by)
        return True

    def markdown(self, notes: list[dict]) -> str:
        parts = []
        for n in notes:
            stamp = (f"created {datetime.fromtimestamp(n['created']).isoformat(timespec='minutes')}, "
                     f"updated {datetime.fromtimestamp(n['updated']).isoformat(timespec='minutes')}")
            tags = " ".join(f"#{t}" for t in n["tags"])
            parts.append(f"# {n['title']}\n\n_{stamp}_" + (f"  \n{tags}" if tags else "") + f"\n\n{n['text']}\n")
        return "\n---\n\n".join(parts)

    def export(self, sandbox, nid: int | None = None, *, by: str = "user") -> dict:
        """Write Markdown into the file sandbox (the name is derived from the title and re-checked there)."""
        notes = [self.get(nid)] if nid else self.search(limit=10_000)
        notes = [n for n in notes if n]
        if not notes:
            raise KeyError(nid)
        name = f"{slug(notes[0]['title'])}-{notes[0]['id']}.md" if nid else \
            f"all-notes-{datetime.fromtimestamp(self.clock()):%Y%m%d-%H%M}.md"
        return sandbox.write_text(f"exports/notes/{name}", self.markdown(notes), overwrite=True, by=by)


# ---------------------------------------------------------------------- voice / agent tools
class AddArgs(BaseModel):
    title: str = Field("", max_length=MAX_TITLE)
    text: str = Field(..., min_length=1, max_length=MAX_TEXT)
    tags: list[str] = Field(default_factory=list, max_length=MAX_TAGS)


class FindArgs(BaseModel):
    query: str = Field("", max_length=120, description="words to find (title, text or tag); '' = newest notes")
    tag: str = Field("", max_length=30)
    note_id: int = Field(0, ge=0, description="read one note by id (0 = search)")


class EditArgs(BaseModel):
    note_id: int = Field(..., ge=1)
    title: str | None = Field(None, max_length=MAX_TITLE)
    text: str | None = Field(None, max_length=MAX_TEXT, description="the new full text (replaces it)")
    append: str | None = Field(None, max_length=4000, description="text to add at the end")
    tags: list[str] | None = Field(None, max_length=MAX_TAGS)


class IdArgs(BaseModel):
    note_id: int = Field(..., ge=1)


class ExportArgs(BaseModel):
    note_id: int = Field(0, ge=0, description="0 = all notes")


def make_skill(notes: Notes, sandbox=None):
    from .skills import Skill, Tool, ToolResult

    def brief(n: dict) -> dict:
        return {"id": n["id"], "title": n["title"], "tags": n["tags"], "text": n["text"][:1500],
                "updated": datetime.fromtimestamp(n["updated"]).isoformat(timespec="minutes")}

    class NoteSkill(Skill):
        name = "notes"
        description = "the user's notes (title, text, tags): add, find, edit, export, delete"

        def tools(self) -> list:
            async def add(a: AddArgs):
                try:
                    n = notes.add(a.title, a.text, a.tags, by="voice")
                except ValueError as e:
                    return ToolResult(False, str(e), code="NOTE_BAD")
                return ToolResult(True, f"note saved: {n['title']}", brief(n))

            async def find(a: FindArgs):
                if a.note_id:
                    n = notes.get(a.note_id)
                    return ToolResult(True, n["title"], brief(n)) if n else ToolResult(False, "no such note",
                                                                                       code="NOTE_NOT_FOUND")
                items = notes.search(a.query, a.tag, 10)
                return ToolResult(True, f"{len(items)} note(s)" if items else "no matching notes",
                                  {"notes": [brief(n) for n in items]})

            async def edit(a: EditArgs):
                try:
                    n = notes.edit(a.note_id, title=a.title, text=a.text, tags=a.tags, append=a.append, by="voice")
                except KeyError:
                    return ToolResult(False, "no such note", code="NOTE_NOT_FOUND")
                except ValueError as e:
                    return ToolResult(False, str(e), code="NOTE_BAD")
                return ToolResult(True, f"note updated: {n['title']}", brief(n))

            async def delete(a: IdArgs):
                ok = notes.delete(a.note_id, by="voice")
                return ToolResult(True, "note deleted") if ok else ToolResult(False, "no such note",
                                                                              code="NOTE_NOT_FOUND")

            async def delete_impact(a: IdArgs):
                n = notes.get(a.note_id)
                return 1, f"למחוק את הפתק \"{n['title'] if n else a.note_id}\"? / Delete this note?"

            async def export(a: ExportArgs):
                if sandbox is None:
                    return ToolResult(False, "file workspace unavailable", code="SANDBOX_UNAVAILABLE")
                from .sandbox import SandboxError
                try:
                    r = notes.export(sandbox, a.note_id or None, by="voice")
                except KeyError:
                    return ToolResult(False, "no such note", code="NOTE_NOT_FOUND")
                except SandboxError as e:
                    return ToolResult(False, e.message, code=e.code)
                return ToolResult(True, f"exported to {r['path']} in my workspace", r)

            return [
                Tool("note_add", "Save a note for the user (title, text, tags) when they ask to write something "
                     "down ('תרשום פתק', 'שמור הערה').", add, AddArgs, permission="action"),
                Tool("note_find", "Find the user's notes by words or tag, or read one by id.", find, FindArgs,
                     permission="read"),
                Tool("note_edit", "Change a note: new title/text/tags, or append text.", edit, EditArgs,
                     permission="action"),
                Tool("note_delete", "Delete a note (asks the user first).", delete, IdArgs, permission="destructive",
                     impact=delete_impact),
                Tool("note_export", "Export one note or all notes as a Markdown file in my workspace.", export,
                     ExportArgs, permission="action"),
            ]

    return NoteSkill()


ALLOWED_ACTIONS = {"note_add", "note_edit", "note_export"}


# ---------------------------------------------------------------------- dashboard
def _notes(ctx) -> Notes:
    n = getattr(ctx, "_notes", None)
    if n is None:
        n = Notes(ctx_db(ctx))
        ctx._notes = n
    return n


def api_notes(ctx, m, body, q=None):
    q = q or {}
    return 200, {"notes": _notes(ctx).search((q.get("q") or [""])[0][:120], (q.get("tag") or [""])[0][:30], 500)}


def api_note_add(ctx, m, body):
    b = body or {}
    try:
        return 200, _notes(ctx).add(str(b.get("title", "")), str(b.get("text", "")), b.get("tags"), by="dashboard")
    except ValueError as e:
        return 400, {"error": str(e)}


def api_note_op(ctx, m, body):
    n, b, nid = _notes(ctx), body or {}, int(m.group(1))
    op = b.get("op", "edit")
    try:
        if op == "edit":
            return 200, n.edit(nid, title=b.get("title"), text=b.get("text"), tags=b.get("tags"), by="dashboard")
        if op == "delete":                       # the owner approves on the approval popup; the request waits
            from . import approvals
            ok, _w = approvals.ask_owner(ctx.home, f"Delete note #{nid}?", title="dashboard_delete_note",
                                         risk="medium", why="You pressed Delete on the dashboard Notes page.",
                                         details={"note": nid})
            if not ok:
                return 403, dict(approvals.APPROVAL_REFUSED)
            return (200, {"deleted": True}) if n.delete(nid, by="dashboard") else (404, {"error": "no such note"})
        if op == "export":
            note = n.get(nid)
            if note is None:
                return 404, {"error": "no such note"}
            return 200, {"filename": f"{slug(note['title'])}-{nid}.md", "markdown": n.markdown([note])}
    except KeyError:
        return 404, {"error": "no such note"}
    except ValueError as e:
        return 400, {"error": str(e)}
    return 400, {"error": "op must be edit, delete or export"}


def api_notes_export(ctx, m, body):
    n = _notes(ctx)
    return 200, {"filename": "notes.md", "markdown": n.markdown(n.search(limit=10_000))}


def dashboard_routes(register) -> None:
    register("GET", r"/api/notes", api_notes)
    register("POST", r"/api/notes", api_note_add)
    register("GET", r"/api/notes/export", api_notes_export)
    register("POST", r"/api/notes/(\d+)", api_note_op)
