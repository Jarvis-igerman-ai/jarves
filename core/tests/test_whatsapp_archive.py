"""Issue #17: the opt-in, local, ENCRYPTED WhatsApp archive and its Hebrew + English full-text search, against the fake
bridge. Ingest (read scope only), retention for each setting, search (prefix letters, niqqud, case, punctuation,
contact/date filters), size report, wipe on keep-nothing / delete / unlink, nothing readable on disk, not in backups or
the diagnostic bundle, switch off stops ingesting, the send feature untouched."""
from __future__ import annotations

import asyncio
import json
import unicodedata
import zipfile

import pytest

from jarves import logs
from jarves import whatsapp_admin as wad
from jarves import whatsapp_archive as arc
from jarves import whatsapp_bridge as wab
from jarves.skills import SkillRegistry
from jarves.whatsapp_store import StoreLocked
from test_dashboard import dash, req  # noqa: F401
from whatsapp_fixtures import BOB, DANA, DANA2, GROUP, T0, bridge, clock, set_send_mode  # noqa: F401,E402
from tools import redaction_audit as ra  # noqa: E402

DAY = 86400.0
PLANT_HE = "האינסטלטור יבוא מחר בבוקר לתקן את הצנרת הסודית"
PLANT_EN = "ZEBRAQUARTZ the plumber needs the vault code PLANTEDVAULT77"


@pytest.fixture
def archive(home, clock):
    """The archive of this home with the test clock, switched on in the Features store."""
    from jarves.config import Config
    from jarves.features import Features
    Features(Config(), home).set(arc.FEATURE, True, by="test")
    a = arc.Archive(home, use_dpapi=False, clock=clock)
    arc._REG[str(home.resolve())] = a
    yield a
    a.close()
    arc._REG.pop(str(home.resolve()), None)


def client(home):
    return wab.BridgeClient(home)


def msg(text, *, chat=DANA, ts=T0, mid=None, **kw):
    return {"id": mid or f"m{abs(hash((text, chat, ts))) % 10**9}", "chat": chat, "ts": ts, "from_me": False,
            "sender_name": "x", "kind": "text", "text": text, "media": None, "edited": False, "deleted": False, **kw}


# ---------------------------------------------------------------------- folding
def test_fold_is_case_niqqud_punctuation_and_final_letter_insensitive():
    assert arc.fold("שָׁלוֹם, עוֹלָם!").split() == arc.fold("שלום עולם").split() == ["שלומ", "עולמ"]
    assert arc.fold("Don't STOP — Café 15:00").split() == ["dont", "stop", "cafe", "15", "00"]
    assert arc.fold("hello‏‫world") == "helloworld"            # bidi / invisible controls vanish
    assert arc.fold("😀 hi 😀").split() == ["hi"]
    assert arc.fold(unicodedata.normalize("NFD", "é")).strip() == "e"


def test_hebrew_prefix_stems():
    assert arc.stems("הכספת") == ["כספת"]
    assert arc.stems("וכספת") == ["כספת", "ספת"]
    assert arc.stems("שלומ") == ["לומ"]
    assert arc.stems("הימ") == []                                         # nothing useful would be left
    assert arc.stems("plumber") == []
    assert arc.match_expr("   !! ") is None


# ---------------------------------------------------------------------- search
@pytest.fixture
def stocked(archive):
    rows = [msg("אני צריך את האינסטלטור מחר בבוקר", chat=DANA, ts=T0 - 40 * DAY, mid="a1"),
            msg("הקוד של הכספת הוא 4821", chat=DANA, ts=T0 - 10 * DAY, mid="a2"),
            msg("הוא שם את הכסף בכספת בחדר", chat=DANA2, ts=T0 - 3 * DAY, mid="a3"),
            msg("Meeting with the Plumber tomorrow!", chat=BOB, ts=T0 - 2 * DAY, mid="b1"),
            msg("שָׁלוֹם לכולם, מה שלומכם?", chat=BOB, ts=T0 - 1 * DAY, mid="b2"),
            msg("", chat=BOB, ts=T0 - 1 * DAY + 5, mid="b3", kind="image",
                media={"mime": "image/jpeg", "size": 9, "caption": "the whiteboard drawing"}),
            msg("this one was deleted", chat=BOB, ts=T0 - 1 * DAY + 9, mid="b4", deleted=True)]
    archive.add(rows)
    return archive


def ids(res):
    return sorted(m["id"] for m in res)


def test_search_hebrew_prefix_letters_and_inflections(stocked):
    s = stocked.search
    assert ids(s("אינסטלטור")) == ["a1"]
    assert ids(s("לאינסטלטור")) == ["a1"]                  # query with a prefix letter finds the bare word
    assert ids(s("והאינסטלטור")) == ["a1"]                 # two prefix letters
    assert ids(s("כספת")) == ["a2", "a3"]                  # bare query finds ה-כספת and ב-כספת
    assert ids(s("בכספת")) == ["a2", "a3"]
    assert ids(s("הכספת")) == ["a2", "a3"]
    assert ids(s("שלום")) == ["b2"]                        # niqqud in the stored text; ם/מ final letter
    assert ids(s("מה שלומכם")) == ["b2"]                   # every word must match
    assert ids(s("כספת 4821")) == ["a2"]


def test_search_english_case_prefix_punctuation_caption(stocked):
    s = stocked.search
    assert ids(s("PLUMBER")) == ["b1"]
    assert ids(s("plumb")) == ["b1"]                       # prefix match
    assert ids(s("tomorrow!!")) == ["b1"]
    assert ids(s("whiteboard")) == ["b3"]                  # a media caption is searchable
    assert s("nothing-like-this-xyz") == []
    assert not s("deleted")                                # a deleted message keeps no text
    assert s("אינסטלטור ומשהו שלא קיים") == []


def test_search_fuzzy_substring_fallback_only_when_nothing_else_matches(stocked):
    assert ids(stocked.search("ינסטלט")) == ["a1"]         # an inner part of a word, no prefix hit


def test_search_filters_contact_date_readable_and_order(stocked, clock):
    s = stocked.search
    assert ids(s("כספת", chat=DANA)) == ["a2"]
    assert ids(s("כספת", since=T0 - 5 * DAY)) == ["a3"]
    assert ids(s("כספת", until=T0 - 5 * DAY)) == ["a2"]
    assert [m["id"] for m in s("כספת")] == ["a3", "a2"]    # newest first
    assert ids(s("כספת", readable={DANA})) == ["a2"]       # an unreadable chat never shows
    assert s("כספת", readable=set()) == []
    assert len(s("כספת", limit=1)) == 1
    # dates the owner types
    assert arc.parse_when("2026-09-01", clock()) == arc.parse_when("2026-09-01", 0)
    assert arc.parse_when("2026-09-01", 0, end=True) - arc.parse_when("2026-09-01", 0) in (86400.0, 82800.0, 90000.0)
    assert arc.parse_when("30d", T0) == T0 - 30 * DAY and arc.parse_when("1y", T0) == T0 - 365 * DAY
    assert arc.parse_when("", T0) is None
    with pytest.raises(arc.DateError):
        arc.parse_when("next tuesday", T0)


def test_edits_and_deletions_update_the_index(archive):
    archive.add([msg("the red dragon", mid="e1")])
    assert ids(archive.search("dragon")) == ["e1"]
    archive.add([msg("the blue unicorn", mid="e1", edited=True)])
    assert archive.search("dragon") == [] and ids(archive.search("unicorn")) == ["e1"]
    archive.add([msg("", mid="e1", deleted=True)])
    assert archive.search("unicorn") == [] and archive.count() == 1


def test_a_large_archive_is_searched_instantly(archive):
    import time
    archive.add([msg(f"הודעה מספר {i} על נושא{i % 50} ו-word{i % 97}", mid=f"x{i}", ts=T0 - i) for i in range(20000)])
    t = time.perf_counter()
    r = archive.search("word42 נושא42", limit=10)
    assert r and (time.perf_counter() - t) < 0.5


# ---------------------------------------------------------------------- ingest
def test_sync_copies_only_readable_chats_and_follows_scope(bridge, home, archive):
    r = archive.sync(client(home))
    assert r["added"] == 8 and not r.get("skipped")                    # 9 seeded, the group is not readable
    assert archive.last_ts(GROUP) is None
    assert ids(archive.search("chatter")) == []                         # group text is not archived
    assert archive.sync(client(home))["added"] == 0                     # idempotent
    bridge.deliver(BOB, "new plumber message")
    assert archive.sync(client(home), only_chat=BOB)["added"] == 1
    # groups only when the read scope says so
    bridge.policy["read_scope"]["groups"] = True
    bridge.policy["read_allow"] = [GROUP]
    bridge._save_policy()
    assert archive.sync(client(home))["added"] == 1 and ids(archive.search("chatter"))
    # the chat stops being readable: its archive goes at the next sync (and search never shows it meanwhile)
    bridge.policy["read_allow"] = []
    bridge._save_policy()
    assert archive.search("chatter", readable={BOB}) == []
    assert archive.sync(client(home))["dropped"] == 1 and archive.search("chatter") == []


def test_sync_stops_for_mode_off_kill_keep_nothing(bridge, home, archive):
    bridge.policy["mode"] = "off"
    bridge._save_policy()
    assert archive.sync(client(home)) == {"added": 0, "skipped": "WA_MODE_OFF"} and archive.count() == 0
    bridge.policy["mode"] = "read"
    bridge._save_policy()
    bridge.kill("test")
    assert archive.sync(client(home))["skipped"] == "WA_KILLED" and archive.count() == 0
    bridge.release()
    assert archive.sync(client(home))["added"] == 8
    bridge.policy["keep_nothing"] = True
    bridge._save_policy()
    assert archive.sync(client(home))["skipped"] == "KEEP_NOTHING" and archive.count() == 0
    assert not (archive.dir / "store.enc").exists()


def test_bridge_down_is_a_skip_not_a_crash(home, archive):
    assert archive.sync(client(home))["skipped"] in ("WA_BRIDGE_DOWN", "WA_NOT_SET_UP")


# ---------------------------------------------------------------------- retention
@pytest.mark.parametrize("keep,left", [(14, ["new"]), (90, ["new", "d20", "d60"]), (365, ["new", "d20", "d60", "d200"]),
                                       (0, ["new", "d20", "d60", "d200", "d800"])])
def test_retention_purges_rows_and_index_for_each_setting(archive, keep, left):
    archive.add([msg(f"keepme {n}", mid=n, ts=T0 - d * DAY) for n, d in
                 (("new", 1), ("d20", 20), ("d60", 60), ("d200", 200), ("d800", 800))])
    out = archive.set_keep_days(keep)
    assert out["keep_days"] == keep and archive.keep_days == keep
    assert sorted(m["id"] for m in archive.search("keepme", limit=50)) == sorted(left)
    assert archive.count() == len(left)
    assert archive.enforce() == 0                                      # idempotent


def test_default_is_14_and_a_bad_value_is_refused(archive):
    assert archive.keep_days == 14
    for bad in (-1, True, "x", 1.5):
        with pytest.raises(ValueError):
            archive.set_keep_days(bad)


def test_deleted_text_is_not_left_in_the_file_after_a_purge(archive, home):
    archive.add([msg("ZEBRAQUARTZ old secret", mid="o1", ts=T0 - 100 * DAY), msg("fresh", mid="o2", ts=T0)])
    archive.flush(force=True)
    archive.set_keep_days(14)
    archive.close()
    again = arc.Archive(home, use_dpapi=False, clock=lambda: T0)
    raw = again._open().serialize()                                    # the decrypted database image itself
    assert b"ZEBRAQUARTZ" not in raw and again.count() == 1


# ---------------------------------------------------------------------- retention / purge / unlink through the admin
def test_cli_retention_values_and_the_archive_requirement(bridge, home, capsys, archive):
    from jarves import cli

    def run(*argv):
        rc = cli.main(["whatsapp", *argv])
        return rc, capsys.readouterr().out
    from jarves.features import Features
    from jarves.config import Config
    Features(Config(), home).set(arc.FEATURE, False, by="test")
    rc, out = run("retention", "year")
    assert rc == 1 and "WA_ARCHIVE_OFF" in out and bridge.policy["retention_days"] == 14
    rc, out = run("retention", "forever")
    assert rc == 1 and "WA_ARCHIVE_OFF" in out
    rc, out = run("retention", "90")
    assert rc == 0 and bridge.policy["retention_days"] == 90 and archive.keep_days == 90
    rc, out = run("retention", "banana")
    assert rc == 2
    Features(Config(), home).set(arc.FEATURE, True, by="test")
    rc, out = run("retention", "year")
    assert rc == 0 and bridge.policy["retention_days"] == 90 and archive.keep_days == 365
    assert "limited history" in out                                    # the honest note
    rc, out = run("retention", "forever", "--json")
    assert rc == 0 and archive.keep_days == 0 and '"keep_days": 0' in out
    rc, out = run("retention", "14")
    assert rc == 0 and bridge.policy["retention_days"] == 14 and archive.keep_days == 14


def test_keep_nothing_delete_messages_and_unlink_wipe_the_archive(bridge, home, archive):
    def fill():
        archive.sync(client(home))
        archive.flush(force=True)
        assert archive.count() == 8 and (archive.dir / "store.enc").exists()
    fill()
    wad.set_retention(home, keep_nothing=True, archive_on=True)
    assert archive.count() == 0 and not (archive.dir / "store.enc").exists() and not (archive.dir / "store.key").exists()
    wad.set_retention(home, keep_nothing=False, archive_on=True)
    fill()
    r = wad.purge_messages(home, None)
    assert r["archive_deleted"] == 8 and archive.count() == 0 and archive.search("מחר") == []
    assert not (archive.dir / "store.enc").exists()
    bridge.deliver(BOB, "fresh after the purge")
    archive.sync(client(home))
    archive.add([msg("ancient", mid="z", ts=T0 - 50 * DAY)])
    assert archive.count() == 2
    assert wad.purge_messages(home, 30)["archive_deleted"] == 1 and archive.count() == 1
    wad.unlink_bridge(home)
    assert archive.count() == 0 and not (archive.dir / "store.enc").exists()
    assert archive.keep_days == 14                                      # the owner's choice survives, the data does not


def test_a_logged_out_event_wipes_the_archive(bridge, home, archive):
    archive.sync(client(home))
    archive.flush(force=True)
    w = wab.Watcher(home, archive=lambda: archive)
    w.handle({"type": "logged_out", "reason": "unlinked_on_phone"})
    assert archive.count() == 0 and not (archive.dir / "store.enc").exists()


def test_switch_off_stops_ingesting_and_the_event_feeds_it_when_on(bridge, home, archive):
    on = {"v": True}
    w = wab.Watcher(home, archive=lambda: archive if on["v"] else None)
    bridge.phone_last_seen = None
    w.handle({"type": "message.new", "chat": BOB})
    assert archive.count() == 2                                         # BOB's two messages came in
    on["v"] = False
    bridge.deliver(BOB, "while the archive is off")
    w.handle({"type": "message.new", "chat": BOB})
    assert archive.count() == 2 and archive.search("while") == []
    on["v"] = True
    w.handle({"type": "message.new", "chat": BOB})
    assert ids(archive.search("while")) and archive.count() == 3


# ---------------------------------------------------------------------- the files: encrypted, private, excluded
def test_nothing_readable_on_disk_and_excluded_from_backup_and_diag(bridge, home, archive, tmp_path, clock):
    bridge.add_chat(DANA, "דנה כהן")
    bridge.deliver(DANA, PLANT_HE)
    bridge.deliver(DANA, PLANT_EN)
    logs.setup(home / "logs", "VERBOSE", console=False, privacy=False)
    try:
        archive.sync(client(home))
        archive.flush(force=True)
        assert archive.search("ZEBRAQUARTZ") and archive.search("האינסטלטור")
    finally:
        import logging
        for h in list(logging.getLogger().handlers):
            h.flush()
    blob = (archive.dir / "store.enc").read_bytes()
    assert blob[:4] == b"JWS1" and not blob.startswith(b"SQLite format 3")
    for needle in ("ZEBRAQUARTZ", "PLANTEDVAULT77", "האינסטלטור", "הצנרת הסודית", "plumber"):
        forms = ra.forms(needle)
        for p in sorted(home.rglob("*")):
            if p.is_file() and "whatsapp_bridge/store" not in p.as_posix():       # the bridge's own sealed store too
                data = p.read_bytes()
                assert not any(b in data for b in forms.values()), (needle, p.relative_to(home).as_posix())
    if archive.key_protection == "file":
        assert (archive.dir / "store.key").stat().st_mode & 0o077 == 0 or __import__("sys").platform == "win32"
    # a copy to another PC/user does not open
    other = tmp_path / "other"
    (other / "whatsapp_bridge" / "archive").mkdir(parents=True)
    (other / "whatsapp_bridge" / "archive" / "store.enc").write_bytes(blob)
    (other / "whatsapp_bridge" / "archive" / "store.key").write_bytes(b"k" * 32)
    with pytest.raises(StoreLocked):
        arc.Archive(other, use_dpapi=False).search("plumber")
    assert arc.Archive(other, use_dpapi=False).stats()["locked"] is True
    # not in a backup
    from jarves import backup
    take, _ = backup.plan(home)
    assert not [n for _, n in take if "whatsapp_bridge" in n or "archive" in n]
    # not in the diagnostic bundle (not even listed)
    from jarves import diagbundle
    from jarves.config import Config
    out = tmp_path / "diag.zip"
    diagbundle.build(home, Config(), out)
    with zipfile.ZipFile(out) as z:
        everything = b"".join(z.read(n) for n in z.namelist())
    assert b"ZEBRAQUARTZ" not in everything and "ZEBRAQUARTZ".encode("utf-16-le") not in everything
    assert b"archive/store" not in everything and b"archive" not in b"".join(n.encode() for n in zipfile.ZipFile(out).namelist())
    assert not ra.scan_blob("diag", out.read_bytes()) or not any(
        h[0].startswith(("content", "secret")) for hs in ra.scan_blob("diag", out.read_bytes()).values() for h in hs)
    # the logs carry no message text
    logtext = b"".join(p.read_bytes() for p in (home / "logs").rglob("*") if p.is_file())
    assert b"ZEBRAQUARTZ" not in logtext and "האינסטלטור".encode() not in logtext


def test_another_process_wiping_the_files_is_not_undone_by_this_one(archive, home):
    archive.add([msg("alpha message", mid="p1")])
    archive.flush(force=True)
    arc.Archive(home, use_dpapi=False).wipe()                           # the CLI in another process
    archive.add([msg("beta message", mid="p2")])
    assert archive.count() == 1 and archive.search("alpha") == [] and ids(archive.search("beta")) == ["p2"]


def test_stats_report_counts_and_size_without_text(archive):
    assert archive.stats()["messages"] == 0 and archive.stats()["bytes"] == 0
    archive.add([msg("hello world", mid="s1", ts=T0 - DAY), msg("שלום עולם", mid="s2", chat=BOB, ts=T0)])
    archive.flush(force=True)
    st = archive.stats()
    assert st["messages"] == 2 and st["chats"] == 2 and st["bytes"] > 0 and st["mb"] >= 0
    assert st["oldest"] == T0 - DAY and st["newest"] == T0 and st["keep_days"] == 14 and st["locked"] is False
    assert "hello" not in json.dumps(st) and "שלום" not in json.dumps(st)


# ---------------------------------------------------------------------- the tool the model uses
def make_reg(home, clock, archive_on):
    reg = SkillRegistry()
    reg.register(wab.WhatsAppBridgeSkill(home, clock=clock, archive=(lambda: arc.get(home)) if archive_on else None))
    return reg


async def test_whatsapp_search_tool_uses_the_archive_with_contact_and_date_filters(bridge, home, archive, clock):
    archive.add([msg("הקוד של הכספת הוא 4821", chat=DANA, ts=T0 - 40 * DAY, mid="old"),
                 msg("שוב הכספת", chat=DANA, ts=T0 - 2 * DAY, mid="newer"),
                 msg("safe at the office", chat=BOB, ts=T0 - 2 * DAY, mid="bobs"),
                 msg("בכספת של הקבוצה", chat=GROUP, ts=T0 - 2 * DAY, mid="grp")])     # group is not readable
    reg = make_reg(home, clock, True)
    r = await reg.call("whatsapp_search", {"query": "כספת"})
    assert r.ok and r.code == "WA_SEARCH"
    got = r.data["untrusted_messages"]
    assert len(got) == 2 and {m["date"][:4] for m in got} == {"2026"} and all(m["chat"] for m in got)
    reg._untrusted.clear()
    r = await reg.call("whatsapp_search", {"query": "כספת", "who": "דנה כהן", "since": "10d"})
    assert r.ok and [m["text"] for m in r.data["untrusted_messages"]] == ["שוב הכספת"]
    reg._untrusted.clear()
    r = await reg.call("whatsapp_search", {"query": "כספת", "until": "2026-08-31"})
    assert r.ok and [m["text"] for m in r.data["untrusted_messages"]] == ["הקוד של הכספת הוא 4821"]
    reg._untrusted.clear()
    r = await reg.call("whatsapp_search", {"query": "כספת", "since": "next tuesday"})
    assert not r.ok and r.code == "WA_BAD_DATE"
    reg._untrusted.clear()
    r = await reg.call("whatsapp_search", {"query": "כספת", "who": "דנה"})                # two Danas
    assert not r.ok and r.code == "WA_AMBIGUOUS"
    reg._untrusted.clear()
    bridge.policy["mode"] = "off"
    bridge._save_policy()
    r = await reg.call("whatsapp_search", {"query": "כספת"})                               # the live read gate
    assert not r.ok and r.code == "WA_MODE_OFF"


async def test_search_result_is_untrusted_and_taints(bridge, home, archive, clock):
    archive.sync(client(home))
    tainted = []
    skill = wab.WhatsAppBridgeSkill(home, clock=clock, archive=lambda: archive, on_untrusted=lambda: tainted.append(1))
    assert next(t for t in skill.tools() if t.name == "whatsapp_search").untrusted_output
    reg = SkillRegistry()
    reg.register(skill)
    res = await reg.call("whatsapp_search", {"query": "מחר"})
    assert res.ok and tainted and "untrusted" in json.dumps(res.data)


async def test_tool_falls_back_to_the_bridge_search_when_the_archive_is_off(bridge, home, clock):
    reg = make_reg(home, clock, False)
    r = await reg.call("whatsapp_search", {"query": "Meeting"})
    assert r.ok and len(r.data["untrusted_messages"]) == 1


async def test_a_locked_archive_is_a_clear_refusal(bridge, home, archive, clock):
    archive.add([msg("locked soon", mid="l1")])
    archive.flush(force=True)
    (archive.dir / "store.key").write_bytes(b"q" * 32)
    archive._conn = None
    r = await make_reg(home, clock, True).call("whatsapp_search", {"query": "locked"})
    assert not r.ok and r.code == "WA_STORE_LOCKED"


def test_send_feature_is_untouched(bridge, home, archive, clock):
    assert [t.name for t in wab.WhatsAppBridgeSkill(home).tools()] == [t.name for t in
                                                                       wab.WhatsAppBridgeSkill(home, archive=lambda: archive).tools()]


# ---------------------------------------------------------------------- CLI search + archive
def test_cli_search_archive_on_off_wipe(bridge, home, capsys, archive, monkeypatch):
    from jarves import cli
    from jarves.config import Config
    from jarves.features import Features

    def run(*argv):
        rc = cli.main(["whatsapp", *argv])
        return rc, capsys.readouterr().out
    Features(Config(), home).set(arc.FEATURE, False, by="test")
    rc, out = run("search", "Meeting")                                    # archive off: the bridge's own window
    assert rc == 0 and "Meeting moved" in out and "archive on" in out
    monkeypatch.setattr("builtins.input", lambda _p: "n")
    rc, out = run("archive", "on")
    assert rc == 1 and "AES-256-GCM" in out and "DPAPI" in out and "forever" in out and "backups" in out
    assert not Features(Config(), home).enabled(arc.FEATURE)              # nothing turned on without a yes
    rc, out = run("archive", "on", "--yes")
    assert rc == 0 and Features(Config(), home).enabled(arc.FEATURE)
    rc, out = run("archive", "sync")
    assert rc == 0 and json.loads(out)["added"] == 8
    rc, out = run("search", "meeting", "--from", "Bob", "--since", "2020-01-01")
    assert rc == 0 and "Meeting moved" in out and "Bob Smith" in out
    rc, out = run("search", "meeting", "--since", "2030-01-01")
    assert rc == 0 and "No messages found" in out
    rc, out = run("search", "מחר", "--from", "דנה")
    assert rc == 1 and "WA_AMBIGUOUS" in out or "WA_UNKNOWN_CONTACT" in out    # two Danas: asks which one
    rc, out = run("search", "x", "--since", "soon")
    assert rc == 2 and "WA_BAD_DATE" in out
    rc, out = run("archive", "status", "--json")
    st = json.loads(out)
    assert rc == 0 and st["enabled"] is True and st["messages"] == 8 and st["bytes"] > 0
    rc, out = run("status", "--json")
    assert json.loads(out)["archive"]["messages"] == 8
    rc, out = run("archive", "off")
    assert rc == 0 and not Features(Config(), home).enabled(arc.FEATURE) and arc.get(home).count() == 8
    rc, out = run("archive", "wipe", "--yes")
    assert rc == 0 and arc.get(home).count() == 0 and not (arc.get(home).dir / "store.enc").exists()
    rc, out = run("archive", "bogus")
    assert rc == 2


# ---------------------------------------------------------------------- dashboard
def test_dashboard_archive_retention_search_wipe(dash, bridge, home, archive):
    from jarves.config import Config
    from jarves.features import Features
    Features(Config(), home).set(arc.FEATURE, False, by="test")
    h = {"X-Jarves": "1"}
    r, body = req(dash, "GET", "/api/whatsapp")
    d = json.loads(body)
    assert r.status == 200 and d["archive"]["feature"]["id"] == "whatsapp_archive" and d["archive"]["enabled"] is False
    assert "DPAPI" in d["archive"]["about"] and d["archive"]["about_he"]
    r, body = req(dash, "POST", "/api/whatsapp/retention", headers=h, body={"days": 365})
    assert r.status == 409 and json.loads(body)["code"] == "WA_ARCHIVE_OFF"
    r, body = req(dash, "POST", "/api/whatsapp/archive", headers=h, body={"op": "on"})
    assert r.status == 200 and json.loads(body)["feature"]["enabled"] is True
    r, body = req(dash, "POST", "/api/whatsapp/retention", headers=h, body={"days": 365})
    assert r.status == 200 and archive.keep_days == 365 and bridge.policy["retention_days"] == 90
    r, body = req(dash, "POST", "/api/whatsapp/retention", headers=h, body={"days": 0})
    assert r.status == 200 and archive.keep_days == 0
    archive.sync(client(home))
    r, body = req(dash, "GET", "/api/whatsapp/search?q=Meeting&from=Bob")
    s = json.loads(body)
    assert r.status == 200 and s["archive"] is True and s["untrusted"] and len(s["messages"]) == 1
    r, body = req(dash, "GET", "/api/whatsapp/search?q=Meeting&since=bad")
    assert r.status == 400 and json.loads(body)["code"] == "WA_BAD_DATE"
    r, body = req(dash, "GET", "/api/whatsapp")
    assert json.loads(body)["archive"]["messages"] == 8
    r, body = req(dash, "POST", "/api/whatsapp/archive", headers=h, body={"op": "wipe"})
    assert r.status == 400                                              # needs {"confirm": true}
    r, body = req(dash, "POST", "/api/whatsapp/archive", headers=h, body={"op": "wipe", "confirm": True})
    assert r.status == 200 and archive.count() == 0
    r, body = req(dash, "POST", "/api/whatsapp/purge", headers=h, body={"confirm": True})
    assert r.status == 200 and "archive_deleted" in json.loads(body)
    from jarves import dashboard
    js = (dashboard.STATIC / "whatsapp.js").read_text(encoding="utf-8")
    assert "Days kept" in js and "innerHTML" not in js and "Forever" in js and "limited history" in js


# ---------------------------------------------------------------------- the feature is registered, opt-in, high risk
def test_feature_is_off_by_default_high_risk_and_needs_the_bridge():
    from jarves import features
    f = features.CATALOG[arc.FEATURE]
    assert f.default_enabled is False and f.risk == "high" and f.needs == ["whatsapp_bridge"]
    assert "second copy" in f.about and "limited history" in f.about


async def test_bridge_search_with_who_and_words_honours_who_without_dates(bridge, home, clock):
    # #353: archive unavailable, query + who, no dates: who used to be ignored (all chats were searched)
    reg = make_reg(home, clock, False)
    r = await reg.call("whatsapp_search", {"query": "Meeting", "who": "Bob"})
    assert r.ok and len(r.data["untrusted_messages"]) == 1
    r = await reg.call("whatsapp_search", {"query": "Meeting", "who": "דנה כהן"})     # Dana never wrote it
    assert r.ok and r.data["untrusted_messages"] == []
    r = await reg.call("whatsapp_search", {"query": "Meeting", "who": "nobody-here-xyz"})
    assert not r.ok
