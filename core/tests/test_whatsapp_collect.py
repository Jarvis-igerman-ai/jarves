"""JAR-273: `jarvis whatsapp collect TOPIC`: archive messages on a topic (Hebrew synonyms, contact mentions) into rows,
deduplicated, sorted, totalled, saved as CSV + a Hebrew summary. Fixture texts are invented, never real messages."""
from __future__ import annotations

import csv
from datetime import datetime

import pytest

from jarves import whatsapp_archive as arc
from jarves import whatsapp_admin as wad
from jarves import whatsapp_collect as wc
from whatsapp_fixtures import bridge, clock  # noqa: F401

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
    assert rep["totals"] == {"by_me": {DANA: 250.0}, "by_them": {BOB: 1200.0}}     # by chat id and sender side
    assert (by["1"]["side"], by["3"]["side"]) == ("by_me", "by_them")
    assert by["1"]["date"] == datetime.fromtimestamp(T0 + 20).strftime("%Y-%m-%d")
    assert rep["uncertain"] == 3 and rep["truncated"] is False


def test_two_chats_with_one_name_keep_two_totals(home):
    a = arc.Archive(home, use_dpapi=False, clock=lambda: T0)
    a.add([m("a", DANA, "תשלום 10 שח", T0), m("b", BOB, "תשלום 20 שח", T0 + 1)])
    try:
        rep = wc.collect(a, "תשלום", names={DANA: "דנה", BOB: "דנה"})
    finally:
        a.close()
    assert sorted(t["amount_ils"] for t in wc.totals_view(rep)) == [10.0, 20.0]
    assert "- דנה: 10" in wc.summary(rep, "x") and "- דנה: 20" in wc.summary(rep, "x")


def test_formula_guard_skips_invisible_marks():
    assert wc._cell("\u200f=1+1") == "'\u200f=1+1" and wc._cell("\ufeff@x").startswith("'")
    assert wc._cell("שלום") == "שלום"


def test_decimal_comma_is_never_a_sure_wrong_amount():
    r = wc.row(m("x", DANA, "העברתי 12,50 ₪", T0), NAMES)
    assert r["uncertain"] and r["amount_ils"] != 50.0
    assert wc.row(m("y", DANA, "₪1,200.5", T0), NAMES)["amount_ils"] == 1200.5


def test_paging_finds_more_than_one_page_and_the_contacts_own_chat(home, monkeypatch):
    a = arc.Archive(home, use_dpapi=False, clock=lambda: T0)
    a.add([m(f"b{i}", BOB, f"תשלום {i} שח", T0 + i // 2) for i in range(450)]     # ties on the same second too
          + [m("d1", DANA, "תשלום 70 שח", T0 - 1)])
    try:
        assert len(wc.collect(a, "תשלום", names=NAMES)["rows"]) == 451
        rep = wc.collect(a, "תשלום", names=NAMES, contacts={DANA: "דנה כהן"})
        assert [r["id"] for r in rep["rows"]] == ["d1"] and rep["truncated"] is False
        monkeypatch.setattr(wc, "MAX_PAGES", 1)
        capped = wc.collect(a, "תשלום", names=NAMES)
        assert capped["truncated"] is True and "truncated" in wc.summary(capped, "x")
    finally:
        a.close()


def test_contacts_keep_their_chat_and_mentions_elsewhere(archive):
    rep = wc.collect(archive, "תשלום", names=NAMES, contacts={DANA: "דנה כהן"})
    assert [r["id"] for r in rep["rows"]] == ["4", "1"]                     # spouse chat mentions "דנה"
    assert wc.collect(archive, "payment", names=NAMES, chat=BOB)["rows"][0]["id"] == "3"
    only = wc.collect(archive, "payment", names={DANA: "דנה כהן"})["rows"]
    assert only and all(r["chat_id"] == DANA for r in only)                 # unreadable chats never


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


@pytest.mark.parametrize("topic", ["../x", 'ש"ח', "a/b\\c:d?*", "..", "  ?  "])
def test_report_name_never_leaves_reports_or_breaks_windows(home, topic):
    paths = wc.write_report(home, {"rows": [], "totals": {}, "uncertain": 0}, topic, stamp="20260922-000000")
    for p in paths:
        assert p.parent == home / "reports" and p.is_file()
        assert not set(p.name) & set('/\\:"?*<>|') and ".." not in p.name


def test_bad_topic_and_format_are_refused(archive):
    with pytest.raises(ValueError):
        wc.collect(archive, "  ", names=NAMES)
    with pytest.raises(ValueError):
        wc.write_report(archive.home, {"rows": [], "totals": {}, "uncertain": 0}, "x", stamp="s", fmt="xlsx")


def test_cli_collect_needs_the_archive_and_writes_the_report(bridge, home, capsys, monkeypatch):
    from jarves import cli
    from jarves.config import Config
    from jarves.features import Features
    from whatsapp_fixtures import BOB as FB, DANA as FD
    a = arc.Archive(home, use_dpapi=False, clock=lambda: T0)
    arc._REG[str(home.resolve())] = a
    try:
        assert cli.main(["whatsapp", "collect", "payment"]) == 1 and "WA_ARCHIVE_OFF" in capsys.readouterr().out
        Features(Config(), home).set(arc.FEATURE, True, by="test")
        a.add([m("c1", FD, "שילמתי 300 ₪", T0, me=True), m("c2", FB, "תשלום 50 שח", T0 + 1)])
        assert cli.main(["whatsapp", "collect", "payment", "--from", "דנה כהן", "--json"]) == 0
        out = capsys.readouterr().out
        assert '"rows": 1' in out and '"chat": "דנה כהן", "amount_ils": 300.0' in out and (home / "reports").is_dir()
        assert '"chats_full": false' in out and '"truncated": false' in out
        assert cli.main(["whatsapp", "collect", "payment", "--since", "banana"]) == 2
        assert cli.main(["whatsapp", "collect", "payment", "--from", "nobody-zz"]) == 1
        assert "WA_UNKNOWN_CONTACT" in capsys.readouterr().out
        assert cli.main(["whatsapp", "collect", "payment", "--chat", "Bob Smith", "--format", "md", "--json"]) == 0
        out = capsys.readouterr().out
        assert '"rows": 1' in out and '"chat": "Bob Smith", "amount_ils": 50.0' in out and ".csv" not in out and ".md" in out
        monkeypatch.setattr(wad, "CHAT_CAP", 2)
        assert cli.main(["whatsapp", "collect", "payment"]) == 0
        assert "only the first 2 chats" in capsys.readouterr().out
    finally:
        a.close()
        arc._REG.pop(str(home.resolve()), None)
