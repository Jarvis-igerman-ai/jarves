"""Long-term, searchable, ENCRYPTED WhatsApp archive (Issue #17), Core side. Opt-in: feature `whatsapp_archive`.

Why it exists: the bridge keeps messages 1..90 days (default 14). The owner may want months or years, searchable in
Hebrew and English. Design (least risky, no Go change): the Core copies messages it can ALREADY read through the
bridge's user API (`/chats`, `/messages`, the `message.new` event as a nudge) into its own SQLite, which lives only in
memory and is written to disk ONLY as `<home>/whatsapp_bridge/archive/store.enc` (the same AES-256-GCM container, key
wrap (DPAPI on Windows, a 0600 key file elsewhere) and `StoreLocked` rules as the bridge store, see whatsapp_store.py).
The folder sits inside `whatsapp_bridge/`, which backups and diagnostics bundles already exclude; nothing here is ever
logged (counts and codes only) and nothing reaches an AI model except through the `whatsapp_search` tool for words the
owner asked about (untrusted text, taint guard unchanged).

Rules kept: only chats the bridge says are readable are archived (read scope and allow-list, groups only when the
scope says so); chats that stop being readable are dropped from the archive; mode `off`, the kill switch, the bridge's
"keep nothing" and a locked store stop ingesting; "keep nothing", "delete messages" and unlink wipe the archive and its
index; retention purges both (old rows are rewritten with VACUUM + secure_delete so they do not linger in free pages).

Search: SQLite FTS5 (`unicode61`) over a folded copy of the text: NFKD, marks removed (niqqud, accents), case folded,
punctuation/emoji dropped, Hebrew final letters unified, and Hebrew one-letter prefixes (ו ה ב ל מ ש כ) indexed as extra
forms so "בכספת" finds "כספת". Prefix matching for longer words; a substring fallback when nothing matches.
Semantic (meaning) search is deliberately NOT built: see docs/handoff.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

from . import logs
from .whatsapp_store import SecureStore, StoreLocked

FEATURE = "whatsapp_archive"
KEEP_CHOICES = (14, 90, 365, 0)            # 0 = forever
DEFAULT_KEEP = 14
FLUSH_EVERY_S = 5.0
OVERLAP_S = 2 * 86400                       # re-read this much on each pull: edits and deletions of recent messages
PAGE = 500                                  # the bridge returns at most 500 messages per call
PREFIX_LETTERS = "והבלמשכ"
FINALS = str.maketrans("םןץףך", "מנצפכ")
_DROP = set("'’‘ʼ׳״\"`´")                   # apostrophes / geresh: "don't" -> "dont", "צ'ק" -> "צק"

SCHEMA = """
CREATE TABLE IF NOT EXISTS msg(
  id INTEGER PRIMARY KEY, chat TEXT NOT NULL, mid TEXT NOT NULL, ts REAL NOT NULL, from_me INTEGER NOT NULL,
  sender TEXT, kind TEXT, text TEXT, media TEXT, edited INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0,
  UNIQUE(chat, mid));
CREATE INDEX IF NOT EXISTS msg_ts ON msg(ts);
CREATE INDEX IF NOT EXISTS msg_chat_ts ON msg(chat, ts);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(body, tokenize='unicode61 remove_diacritics 0');
"""


# ---------------------------------------------------------------------- folding (Hebrew + English)
def fold(text: str) -> str:
    """Searchable form: NFKD, marks (niqqud, cantillation, accents) and invisible/bidi controls removed, case folded,
    apostrophes dropped, every other non-letter/digit a space, Hebrew final letters unified."""
    out: list[str] = []
    for ch in unicodedata.normalize("NFKD", text or ""):
        cat = unicodedata.category(ch)
        if cat[0] == "M" or cat == "Cf":
            continue
        if ch in _DROP:
            continue
        out.append(ch if cat[0] in "LN" else " ")
    return "".join(out).casefold().translate(FINALS)


def _is_he(ch: str) -> bool:
    return "א" <= ch <= "ת"


def stems(token: str) -> list[str]:
    """The token without a Hebrew one-letter prefix (ו ה ב ל מ ש כ), and for the valid two-letter combinations
    (ו+x, ש+x, כש), at least 3 letters left. Never returns the token itself."""
    out: list[str] = []
    if len(token) >= 4 and _is_he(token[0]) and token[0] in PREFIX_LETTERS:
        out.append(token[1:])
        second_ok = {"ו": "הבלמשכ", "ש": "הבלמכ", "כ": "ש"}.get(token[0], "")
        if len(token) >= 5 and token[1] in second_ok:
            out.append(token[2:])
    return out


def index_text(text: str) -> str:
    """What goes into the FTS column: every folded token plus its prefix-stripped forms."""
    toks = fold(text).split()
    extra = [s for t in toks for s in stems(t)]
    return " ".join(toks + extra)


def match_expr(query: str) -> str | None:
    """FTS5 expression for the owner's words: every word must match (AND); a word matches as itself (prefix match for
    3+ letters, so 'plumb' finds 'plumber') or, for Hebrew, as its prefix-stripped form."""
    parts: list[str] = []
    for t in fold(query).split():
        forms = [f'"{t}"*' if len(t) >= 3 else f'"{t}"'] + [f'"{s}"' for s in stems(t)]
        parts.append("(" + " OR ".join(forms) + ")")
    return " AND ".join(parts) if parts else None


class DateError(ValueError):
    """A date the owner typed that is neither YYYY-MM-DD nor 30d / 2w / 3m / 1y."""


def parse_when(text: str | None, now: float, *, end: bool = False) -> float | None:
    """'2026-09-01' (local midnight; end of that day when `end`), or relative '30d', '2w', '3m', '1y' (ago)."""
    t = (text or "").strip().lower()
    if not t:
        return None
    words = {"today": 0, "היום": 0, "now": 0, "yesterday": 1, "אתמול": 1}
    if t in words:
        d = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=words[t])
        if end:
            d += timedelta(days=1)
        return d.timestamp()
    m = re.fullmatch(r"(\d{1,4})\s*([dwmy])", t)
    if m:
        n, u = int(m.group(1)), m.group(2)
        return now - n * {"d": 86400, "w": 7 * 86400, "m": 30 * 86400, "y": 365 * 86400}[u]
    try:
        d = datetime.strptime(t[:10], "%Y-%m-%d")
    except ValueError:
        raise DateError("a date like 2026-09-01, or 30d / 2w / 3m / 1y") from None
    if end:
        d += timedelta(days=1)
    return d.timestamp()


# ---------------------------------------------------------------------- the archive
class Archive:
    """One encrypted archive per JARVES home. Use `get(home)`; every method is thread-safe. Other processes (the CLI)
    see changes through the sealed file: this object reloads when the file or key changed under it."""

    def __init__(self, home: Path, *, use_dpapi: bool | None = None, clock=time.time) -> None:
        self.home, self.clock, self._use_dpapi = Path(home), clock, use_dpapi
        self.dir = self.home / "whatsapp_bridge" / "archive"
        self.policy_path = self.dir / "policy.json"
        self.lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._store: SecureStore | None = None
        self._sig: tuple | None = None
        self._dirty = False
        self._flushed_at = 0.0

    # -- files
    def _files_sig(self) -> tuple:
        out = []
        for n in ("store.enc", "store.key"):
            try:
                s = (self.dir / n).stat()
                out.append((s.st_mtime_ns, s.st_size))
            except OSError:
                out.append(None)
        return tuple(out)

    def _open(self) -> sqlite3.Connection:
        """(Re)open from the sealed file when we have nothing yet or another process changed/removed it."""
        sig = self._files_sig()
        if self._conn is not None and sig == self._sig:
            return self._conn
        if self._conn is not None:
            self._conn.close()
        self._store = SecureStore(self.dir, use_dpapi=self._use_dpapi)
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        data = self._store.load()                      # StoreLocked propagates: unusable until wiped
        if data:
            conn.deserialize(data)
        conn.execute("PRAGMA secure_delete=ON")
        conn.executescript(SCHEMA)
        self._conn, self._sig, self._dirty = conn, sig, False
        return conn

    @property
    def key_protection(self) -> str:
        return (self._store or SecureStore(self.dir, use_dpapi=self._use_dpapi)).key_protection

    def flush(self, force: bool = False) -> bool:
        with self.lock:
            if self._conn is None or not self._dirty:
                return False
            if not force and time.monotonic() - self._flushed_at < FLUSH_EVERY_S:
                return False
            self._conn.commit()
            assert self._store is not None
            self._store.save(self._conn.serialize())
            self._sig, self._dirty, self._flushed_at = self._files_sig(), False, time.monotonic()
            return True

    def close(self) -> None:
        with self.lock:
            try:
                self.flush(force=True)
            finally:
                if self._conn is not None:
                    self._conn.close()
                self._conn = self._store = self._sig = None

    def _has(self) -> bool:
        return self._conn is not None or (self.dir / "store.enc").exists()

    # -- policy (no message content)
    def _policy(self) -> dict:
        try:
            d = json.loads(self.policy_path.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    @property
    def keep_days(self) -> int:
        v = self._policy().get("keep_days", DEFAULT_KEEP)
        return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else DEFAULT_KEEP

    def set_keep_days(self, days: int) -> dict:
        """days >= 1, or 0 = forever. Shrinking purges at once (when a store exists)."""
        if not isinstance(days, int) or isinstance(days, bool) or days < 0:
            raise ValueError("days must be 1 or more, or 0 for forever")
        from .channels_common import write_private_json
        self.dir.mkdir(parents=True, exist_ok=True)
        write_private_json(self.policy_path, {"v": 1, "keep_days": days})
        deleted = self.enforce() if self._has() else 0
        logs.info("whatsapp", "WA_ARCHIVE_RETENTION", "WhatsApp archive retention changed", days=days, deleted=deleted)
        return {"keep_days": days, "deleted": deleted}

    # -- writes
    def enforce(self) -> int:
        """Retention: delete messages older than the chosen days (0 = forever keeps all) from rows and index."""
        days = self.keep_days
        if days == 0:
            return 0
        return self.purge(older_than_days=days)

    def purge(self, older_than_days: float | None = None) -> int:
        """Delete archived messages older than N days, or everything (None). Rewrites the database so the deleted
        text does not stay in free pages. Everything -> the sealed files are removed."""
        with self.lock:
            if older_than_days is None:
                try:
                    n = self.count()
                except StoreLocked:
                    n = 0
                self.wipe()
                return n
            if not self._has():
                return 0
            conn = self._open()
            cut = self.clock() - float(older_than_days) * 86400
            ids = [r[0] for r in conn.execute("SELECT id FROM msg WHERE ts < ?", (cut,))]
            if not ids:
                return 0
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                q = ",".join("?" * len(chunk))
                conn.execute(f"DELETE FROM fts WHERE rowid IN ({q})", chunk)
                conn.execute(f"DELETE FROM msg WHERE id IN ({q})", chunk)
            conn.commit()
            conn.execute("INSERT INTO fts(fts) VALUES('optimize')")
            conn.commit()
            conn.execute("VACUUM")
            self._dirty = True
            self.flush(force=True)
            logs.info("whatsapp", "WA_ARCHIVE_PURGED", "WhatsApp archive messages deleted", count=len(ids))
            return len(ids)

    def wipe(self) -> None:
        """Remove the archive completely: database, index, key. (The retention choice stays.)"""
        with self.lock:
            if self._conn is not None:
                self._conn.close()
            self._conn = self._sig = None
            self._dirty = False
            SecureStore(self.dir, use_dpapi=self._use_dpapi).wipe()
            self._store = None
            logs.info("whatsapp", "WA_ARCHIVE_WIPED", "WhatsApp archive wiped")

    def drop_chats_not_in(self, readable: set[str]) -> int:
        with self.lock:
            if not self._has():
                return 0
            conn = self._open()
            gone = [r[0] for r in conn.execute("SELECT DISTINCT chat FROM msg") if r[0] not in readable]
            n = 0
            for chat in gone:
                ids = [r[0] for r in conn.execute("SELECT id FROM msg WHERE chat=?", (chat,))]
                for i in range(0, len(ids), 500):
                    chunk = ids[i:i + 500]
                    q = ",".join("?" * len(chunk))
                    conn.execute(f"DELETE FROM fts WHERE rowid IN ({q})", chunk)
                    conn.execute(f"DELETE FROM msg WHERE id IN ({q})", chunk)
                n += len(ids)
            if n:
                conn.commit()
                conn.execute("VACUUM")
                self._dirty = True
                self.flush(force=True)
                logs.info("whatsapp", "WA_ARCHIVE_UNREADABLE_DROPPED", "chats no longer readable were dropped",
                          chats=len(gone), count=n)
            return n

    def add(self, messages: list[dict]) -> int:
        """Insert or update bridge message objects. Returns how many were new or changed."""
        changed = 0
        with self.lock:
            conn = self._open()
            for m in messages:
                chat, mid, ts = str(m.get("chat") or ""), str(m.get("id") or ""), m.get("ts")
                if not chat or not mid or not isinstance(ts, (int, float)):
                    continue
                deleted = bool(m.get("deleted"))
                text = "" if deleted else str(m.get("text") or "")
                media = m.get("media") if isinstance(m.get("media"), dict) and not deleted else None
                cap = str((media or {}).get("caption") or "")
                row = (float(ts), int(bool(m.get("from_me"))), str(m.get("sender_name") or "")[:80],
                       str(m.get("kind") or "text")[:20], text, json.dumps(media, ensure_ascii=False) if media else None,
                       int(bool(m.get("edited"))), int(deleted))
                body = index_text(f"{text} {cap}")
                old = conn.execute("SELECT id, ts, text, media, edited, deleted FROM msg WHERE chat=? AND mid=?",
                                   (chat, mid)).fetchone()
                if old is None:
                    cur = conn.execute("INSERT INTO msg(chat,mid,ts,from_me,sender,kind,text,media,edited,deleted) "
                                       "VALUES(?,?,?,?,?,?,?,?,?,?)", (chat, mid, *row))
                    conn.execute("INSERT INTO fts(rowid, body) VALUES(?,?)", (cur.lastrowid, body))
                    changed += 1
                elif (old[1], old[2], old[3], old[4], old[5]) != (row[0], row[4], row[5], row[6], row[7]):
                    conn.execute("UPDATE msg SET ts=?,from_me=?,sender=?,kind=?,text=?,media=?,edited=?,deleted=? "
                                 "WHERE id=?", (*row, old[0]))
                    conn.execute("DELETE FROM fts WHERE rowid=?", (old[0],))
                    conn.execute("INSERT INTO fts(rowid, body) VALUES(?,?)", (old[0], body))
                    changed += 1
            if changed:
                conn.commit()
                self._dirty = True
        return changed

    def last_ts(self, chat: str) -> float | None:
        with self.lock:
            if not self._has():
                return None
            r = self._open().execute("SELECT MAX(ts) FROM msg WHERE chat=?", (chat,)).fetchone()
            return r[0]

    # -- reads
    def count(self) -> int:
        with self.lock:
            if not self._has():
                return 0
            return self._open().execute("SELECT COUNT(*) FROM msg").fetchone()[0]

    def stats(self) -> dict:
        """Size report: counts and bytes only, never text."""
        with self.lock:
            exists = (self.dir / "store.enc").exists()
            out = {"keep_days": self.keep_days, "messages": 0, "chats": 0, "bytes": 0, "oldest": None, "newest": None,
                   "key_protection": self.key_protection, "locked": False}
            try:
                size = (self.dir / "store.enc").stat().st_size if exists else 0
            except OSError:
                size = 0
            out["bytes"] = size
            if not exists and self._conn is None:
                return out
            try:
                conn = self._open()
            except StoreLocked:
                out["locked"] = True
                return out
            n, c, lo, hi = conn.execute("SELECT COUNT(*), COUNT(DISTINCT chat), MIN(ts), MAX(ts) FROM msg").fetchone()
            out.update(messages=n, chats=c, oldest=lo, newest=hi, mb=round(size / 1048576, 2))
            return out

    def search(self, query: str, *, chat: str | None = None, since: float | None = None, until: float | None = None,
               readable: set[str] | None = None, limit: int = 10) -> list[dict]:
        """Messages matching all the words, newest first, as bridge-shaped message dicts. `readable` (the chats the
        bridge lists now) is a hard filter: an unreadable chat never appears."""
        expr = match_expr(query)
        if expr is None:
            return []
        with self.lock:
            if not self._has():
                return []
            conn = self._open()
            where, args = [], []
            if chat is not None:
                where.append("m.chat = ?")
                args.append(chat)
            if readable is not None:
                if not readable:
                    return []
                where.append("m.chat IN (%s)" % ",".join("?" * len(readable)))
                args.extend(sorted(readable))
            if since is not None:
                where.append("m.ts >= ?")
                args.append(since)
            if until is not None:
                where.append("m.ts < ?")
                args.append(until)
            extra = (" AND " + " AND ".join(where)) if where else ""
            lim = max(1, min(int(limit), 200))
            try:
                rows = conn.execute("SELECT m.* FROM fts JOIN msg m ON m.id = fts.rowid WHERE fts MATCH ?" + extra
                                    + " ORDER BY m.ts DESC LIMIT ?", [expr, *args, lim]).fetchall()
            except sqlite3.OperationalError:
                rows = []
            if not rows:                                # fuzzy fallback: the words as a substring of the folded text
                needle = fold(query).strip()
                if needle:
                    rows = conn.execute("SELECT m.* FROM fts JOIN msg m ON m.id = fts.rowid WHERE instr(fts.body, ?) > 0"
                                        + extra + " ORDER BY m.ts DESC LIMIT ?", [needle, *args, lim]).fetchall()
            return [self._row(r) for r in rows]

    @staticmethod
    def _row(r: tuple) -> dict:
        media = None
        if r[8]:
            try:
                media = json.loads(r[8])
            except ValueError:
                media = None
        return {"id": r[2], "chat": r[1], "ts": r[3], "from_me": bool(r[4]), "sender_name": r[5], "kind": r[6],
                "text": r[7] or "", "media": media, "edited": bool(r[9]), "deleted": bool(r[10])}

    # -- ingest from the bridge
    def sync(self, client, *, only_chat: str | None = None, now: float | None = None) -> dict:
        """Pull new messages of the readable chats through the bridge's USER api. Returns counts and a `skipped`
        reason (a code) when nothing was done. Never raises for a bridge refusal."""
        from .whatsapp_bridge import BridgeError
        try:
            st = client.status()
            if st.get("mode") not in ("read", "send") or st.get("killed"):
                return {"added": 0, "skipped": "WA_KILLED" if st.get("killed") else "WA_MODE_OFF"}
            if st.get("keep_nothing"):
                self.wipe()
                return {"added": 0, "skipped": "KEEP_NOTHING"}
            if st.get("store_locked"):
                return {"added": 0, "skipped": "WA_STORE_LOCKED"}
            chats = client.chats(None, 500)
        except BridgeError as e:
            return {"added": 0, "skipped": e.code}
        readable = {c["id"] for c in chats if isinstance(c, dict) and c.get("id")}
        added = dropped = 0
        try:
            dropped = self.drop_chats_not_in(readable)
            for jid in ([only_chat] if only_chat else sorted(readable)):
                if jid not in readable:
                    continue
                last = self.last_ts(jid)
                since = None if last is None else max(0.0, last - OVERLAP_S)
                try:
                    res = client.messages(jid, since, PAGE)
                except BridgeError as e:
                    if e.code in ("WA_MODE_OFF", "WA_STORE_LOCKED", "WA_BRIDGE_DOWN"):
                        return {"added": added, "dropped": dropped, "skipped": e.code}
                    continue
                added += self.add([m for m in res.get("messages", []) if isinstance(m, dict)])
            self.enforce()
            self.flush()
        except StoreLocked:
            return {"added": added, "skipped": "WA_STORE_LOCKED"}
        if added or dropped:
            logs.info("whatsapp", "WA_ARCHIVE_SYNCED", "WhatsApp archive updated", added=added, removed=dropped)
        return {"added": added, "dropped": dropped}


_REG: dict[str, Archive] = {}
_REG_LOCK = threading.Lock()


def get(home: Path) -> Archive:
    key = str(Path(home).resolve())
    with _REG_LOCK:
        a = _REG.get(key)
        if a is None:
            a = _REG[key] = Archive(home)
        return a


def forget(home: Path) -> None:
    """Drop the cached object (tests, profile switch); flushes first."""
    with _REG_LOCK:
        a = _REG.pop(str(Path(home).resolve()), None)
    if a is not None:
        a.close()
