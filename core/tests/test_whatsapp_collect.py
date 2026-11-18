"""JAR-273: `jarvis whatsapp collect TOPIC`: archive messages on a topic (Hebrew synonyms, contact mentions) into rows,
deduplicated, sorted, totalled, saved as CSV + a Hebrew summary. Fixture texts are invented, never real messages."""
from __future__ import annotations

import csv

import pytest

from jarves import whatsapp_archive as arc
from jarves import whatsapp_collect as wc

DANA, BOB, SPOUSE = "d@s.whatsapp.net", "b@s.whatsapp.net", "s@s.whatsapp.net"
NAMES = {DANA: "דנה כהן", BOB: "בוב", SPOUSE: "בית"}
T0 = 1_790_000_000.0


def m(mid, chat, text, ts, me=False):
    return {"id": mid, "chat": chat, "ts": ts, "from_me": me, "sender_name": "x", "kind": "text", "text": text,
            "media": None, "edited": False, "deleted": False}


@pytest.fixture
def archive(home):
    a = arc.Archive(home, use_dpapi=False, clock=lambda: T0)
    a.add([m("1", DANA, "שילמתי לך 250 ₪ בביט על השיעור", T0 + 20, me=True),
           m("2", DANA, "קיבלתי, תודה", T0 + 30),
           m("3", BOB, "העברה בנקאית של 1,200 שח לשיפוץ", T0 + 10),
           m("4", SPOUSE, "תזכיר לי לבדוק את התשלום לדנה", T0 + 5, me=True),
           m("5", SPOUSE, "=HYPERLINK(1) תשלום", T0 + 1),
           m("6", BOB, "פייבוקס שלחתי", T0 + 40, me=True)])
    yield a
    a.close()


def test_collect_finds_synonyms_dedups_sorts_and_totals(archive):
    rep = wc.collect(archive, "payment", names=NAMES)
    assert [r["id"] for r in rep["rows"]] == ["5", "4", "3", "1", "6"]       # by date, "קיבלתי" is not a payment
    by = {r["id"]: r for r in rep["rows"]}
    assert (by["1"]["amount_ils"], by["1"]["method"], by["1"]["uncertain"]) == (250.0, "bit", False)
    assert (by["3"]["amount_ils"], by["3"]["method"]) == (1200.0, "bank transfer")
    assert by["6"]["amount_ils"] is None and by["6"]["uncertain"] and by["6"]["method"] == "paybox"
    assert rep["totals"] == {"בוב": 1200.0, "דנה כהן": 250.0}
    assert rep["uncertain"] == 3


def test_contacts_keep_their_chat_and_mentions_elsewhere(archive):
    rep = wc.collect(archive, "תשלום", names=NAMES, contacts={DANA: "דנה כהן"})
    assert [r["id"] for r in rep["rows"]] == ["4", "1"]                     # spouse chat mentions "דנה"
    assert wc.collect(archive, "payment", names=NAMES, chat=BOB)["rows"][0]["id"] == "3"
    assert wc.collect(archive, "payment", names={DANA: "דנה כהן"})["rows"][0]["id"] == "1"   # unreadable chats never


def test_report_files_are_written_and_formula_safe(archive, home):
    rep = wc.collect(archive, "payment", names=NAMES)
    paths = wc.write_report(home, rep, "payment", stamp="20260922-000000")
    assert sorted(p.name for p in paths) == ["whatsapp-collect-payment-20260922-000000.csv",
                                             "whatsapp-collect-payment-20260922-000000.md"]
    assert all(p.parent == home / "reports" for p in paths)
    rows = list(csv.DictReader(paths[0].open(encoding="utf-8-sig")))
    assert len(rows) == 5 and not any(c.startswith("=") for r in rows for c in r.values())
    md = paths[1].read_text(encoding="utf-8")
    assert "5 הודעות" in md and "בוב: 1,200" in md and "לא ודאי: 3" in md
    assert not list((home / "reports").glob("*.tmp"))


def test_bad_topic_and_format_are_refused(archive):
    with pytest.raises(ValueError):
        wc.collect(archive, "  ", names=NAMES)
    with pytest.raises(ValueError):
        wc.write_report(archive.home, {"rows": [], "totals": {}, "uncertain": 0}, "x", stamp="s", fmt="xlsx")
