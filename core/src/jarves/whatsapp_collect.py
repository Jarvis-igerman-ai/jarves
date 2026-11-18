"""JAR-273 slice 1: `jarvis whatsapp collect TOPIC`: every archived message on a topic (Hebrew folding + topic synonyms,
plus messages in other chats that MENTION the chosen contacts) as rows: date, chat, sender, with, amount ILS, method,
short quote. Deduplicated, sorted by date, totalled per chat; rows without a sure amount are flagged. Saved under
<home>/reports/ as CSV + a short Hebrew summary. Local and rule-based: no model, message text is only data."""
from __future__ import annotations

import csv
import io
import os
import re
import tempfile
from pathlib import Path

from .whatsapp_archive import fold, index_text
from .whatsapp_bridge import _ymd
from .whatsapp_policy import clean_text

SYNONYMS = {"payment": ("תשלום", "שילמתי", "שילמת", "שולם", "העברה", "העברתי", "ביט", "פייבוקס", "שח", "שקל",
                        "קבלה", "חשבונית", "payment", "paid", "paybox", "transfer", "invoice", "receipt")}
METHODS = (("bit", ("ביט", "bit")), ("paybox", ("פייבוקס", "paybox")), ("bank transfer", ("העברה", "העברתי", "transfer")),
           ("cash", ("מזומן", "cash")), ("cheque", ("צק", "שק", "cheque")), ("card", ("אשראי", "card")))
_NUM = r"(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?"
_CUR = r"(?:₪|ש\"ח|ש״ח|שח|שקלים|שקל|nis|ils)(?![א-תA-Za-z])"
_SURE = (re.compile(_NUM + r"\s*" + _CUR, re.I), re.compile(r"(?:₪|nis|ils)\s*" + _NUM, re.I))
_ANY = re.compile(_NUM)
COLUMNS = ("date", "chat", "sender", "with", "amount_ils", "method", "quote", "uncertain")


def terms(topic: str) -> tuple[str, ...]:
    """The topic itself, or its synonym list when the topic names one (by key or by any of its words)."""
    t = fold(topic).strip()
    if not t:
        raise ValueError("empty topic")
    for key, words in SYNONYMS.items():
        if t == key or t in (fold(w).strip() for w in words):
            return words
    return (topic.strip(),)


def _amount(text: str) -> tuple[float | None, bool]:
    for rx in _SURE:
        if (hit := rx.search(text)):
            return float(hit.group(1).replace(",", "") + "." + (hit.group(2) or "0")), True
    hit = _ANY.search(text)
    return (float(hit.group(1).replace(",", "")), False) if hit else (None, False)


def row(m: dict, names: dict[str, str]) -> dict:
    text = " ".join(clean_text(str(m.get("text") or ""))[0].split())
    toks = set(index_text(text).split())
    chat = names.get(m.get("chat", ""), "")
    amount, sure = _amount(text)
    sender = "אני" if m.get("from_me") else (clean_text(str(m.get("sender_name") or ""))[0][:40]
                                              if str(m.get("chat", "")).endswith("@g.us") else chat)
    return {"id": m.get("id"), "chat_id": m.get("chat"), "ts": float(m.get("ts") or 0), "date": _ymd(m.get("ts") or 0),
            "chat": chat, "sender": sender, "with": chat, "amount_ils": amount, "quote": text[:80], "uncertain": not sure,
            "method": next((name for name, words in METHODS if toks & {fold(w).strip() for w in words}), "")}


def collect(archive, topic: str, *, names: dict[str, str], contacts: dict[str, str] | None = None,
            chat: str | None = None, since: float | None = None) -> dict:
    """`names` = the readable chats (id -> name), a hard filter. `contacts` (id -> name): keep their chats and any
    message elsewhere that mentions their first name."""
    found: dict[tuple, dict] = {}
    for word in terms(topic):
        for m in archive.search(word, chat=chat, since=since, readable=set(names), limit=200):
            found.setdefault((m.get("chat"), m.get("id")), m)
    if contacts:
        firsts = {fold(n).split()[0] for n in contacts.values() if fold(n).split()}
        found = {k: m for k, m in found.items() if m.get("chat") in contacts
                 or firsts & set(index_text(str(m.get("text") or "")).split())}
    rows = sorted((row(m, names) for m in found.values()), key=lambda r: (r["ts"], str(r["id"])))
    totals: dict[str, float] = {}
    for r in rows:
        if not r["uncertain"]:
            totals[r["with"]] = totals.get(r["with"], 0.0) + r["amount_ils"]
    return {"rows": rows, "totals": dict(sorted(totals.items())), "uncertain": sum(r["uncertain"] for r in rows),
            "untrusted": True}


def _cell(v) -> str:
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s       # a spreadsheet never runs message text


def _money(v: float) -> str:
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def _atomic(path: Path, text: str, encoding: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def summary(rep: dict, topic: str) -> str:
    lines = [f"# איסוף וואטסאפ: {topic}", "", f"{len(rep['rows'])} הודעות, לא ודאי: {rep['uncertain']}", "",
             "## סכום לפי צ'אט (₪, רק שורות ודאיות)"]
    return "\n".join(lines + [f"- {k}: {_money(v)}" for k, v in rep["totals"].items()]) + "\n"


def write_report(home: Path, rep: dict, topic: str, *, stamp: str, fmt: str = "csv") -> list[Path]:
    """fmt csv = CSV + Hebrew summary (.md); md = the summary only. Each file is written atomically."""
    if fmt not in ("csv", "md"):
        raise ValueError("format must be csv or md (xlsx is not built yet)")
    out = Path(home) / "reports"
    out.mkdir(parents=True, exist_ok=True)
    base = out / f"whatsapp-collect-{'-'.join(fold(topic).split())[:40] or 'topic'}-{stamp}"
    paths = []
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(COLUMNS)
        w.writerows([_cell(r[c]) for c in COLUMNS] for r in rep["rows"])
        _atomic(base.with_suffix(".csv"), buf.getvalue(), "utf-8-sig")
        paths.append(base.with_suffix(".csv"))
    _atomic(base.with_suffix(".md"), summary(rep, topic), "utf-8")
    return paths + [base.with_suffix(".md")]
