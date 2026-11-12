"""JAR-272: WhatsApp long history backfill against the local fake bridge. The bridge asks the phone for older history
(50 per request, no age limit by default), keeps it only in a memory buffer, and the Core archive drains it. Real-phone
behaviour is UNVERIFIED (WhatsApp may cap on-demand history)."""
from __future__ import annotations

import json
import threading
import time

from jarves import cli
from jarves import whatsapp_admin as wad
from jarves import whatsapp_archive as arc
from test_dashboard import dash, req  # noqa: F401
from test_whatsapp_archive import archive  # noqa: F401
from whatsapp_fixtures import BOB, DANA, bridge, clock  # noqa: F401

DAY = 86400.0
OLD = "QUOKKAPLANET the old invoice from three years ago"


def _old_history(bridge, chat=DANA, n=120, start_days=30):
    """n messages the phone still has, one a day going back from start_days ago (the oldest ~4 months+)."""
    now = bridge.now()
    return [bridge.phone_has(chat, OLD if i == n - 1 else f"old note {i}", ts=now - (start_days + i) * DAY)
            for i in range(n)]


def test_backfill_copies_every_old_message_in_batches_of_50(bridge, home, archive):
    archive.set_keep_days(0)                                         # forever
    ids = _old_history(bridge, n=120, start_days=3 * 365)            # three years and older
    seen = []
    r = archive.backfill(wad.admin(home), progress=seen.append, sleep=lambda s: None)
    assert r["added"] == 120 and r["state"] == "done" and r["chats_done"] == r["chats_total"]
    assert [n for c, n in bridge.history_requests if c == DANA] == [50, 50, 50, 50]      # 50, 50, 20, then empty
    assert seen and seen[-1]["added"] == 120
    hits = archive.search("quokkaplanet")
    assert [h["id"] for h in hits] == [ids[-1]]
    assert not any(m["id"] in ids for m in bridge.messages[DANA])   # never kept in the bridge store
    assert bridge.bf_buf == []                                       # drained and acknowledged


def test_backfill_from_contact_and_since(bridge, home, archive):
    archive.set_keep_days(0)
    _old_history(bridge, DANA, n=10, start_days=20)
    _old_history(bridge, BOB, n=5, start_days=20)
    since = bridge.now() - 25 * DAY
    r = archive.backfill(wad.admin(home), chat=DANA, since=since, sleep=lambda s: None)
    assert r["added"] == 6 and {c for c, _ in bridge.history_requests} == {DANA}       # days 20..25 only
    assert all(m["ts"] >= since for m in archive.search("old note", limit=50))


def test_backfill_respects_the_switches(bridge, home, archive):
    ad = wad.admin(home)
    bridge.policy["keep_nothing"] = True
    assert archive.backfill(ad, sleep=lambda s: None)["skipped"] == "WA_KEEP_NOTHING"
    bridge.policy["keep_nothing"] = False
    bridge.kill("owner")
    assert archive.backfill(ad, sleep=lambda s: None)["skipped"] == "WA_KILLED"
    bridge.release()
    bridge.policy["mode"] = "off"
    assert archive.backfill(ad, sleep=lambda s: None)["skipped"] == "WA_MODE_OFF"
    bridge.policy["mode"] = "read"
    bridge.locked = True
    assert archive.backfill(ad, sleep=lambda s: None)["skipped"] == "WA_STORE_LOCKED"
    bridge.locked = False
    bridge.policy["read_scope"]["all_direct"] = False                # Dana no longer readable
    _old_history(bridge, DANA, n=3)
    assert archive.backfill(ad, chat=DANA, sleep=lambda s: None)["skipped"] == "WA_CHAT_NOT_FOUND"
    assert archive.count() == 0


def test_backfill_stop_clears_the_bridge_buffer(bridge, home, archive):
    archive.set_keep_days(0)
    bridge.bf_buf_max = 60                                           # one batch fits, the next waits for a drain
    _old_history(bridge, n=200)
    stop = threading.Event()
    r = archive.backfill(wad.admin(home), stop=stop, progress=lambda p: stop.set(), sleep=lambda s: None)
    assert r["state"] == "stopped" and r["added"] == 50
    assert bridge.bf["state"] == "stopped" and bridge.bf_buf == []


def test_cli_backfill_needs_the_archive_and_prints_progress_without_text(bridge, home, capsys, archive):
    from jarves.config import Config
    from jarves.features import Features
    _old_history(bridge, n=60, start_days=400)
    Features(Config(), home).set(arc.FEATURE, False, by="test")
    assert cli.main(["whatsapp", "archive", "backfill"]) == 1
    assert "archive on" in capsys.readouterr().out
    Features(Config(), home).set(arc.FEATURE, True, by="test")
    rc = cli.main(["whatsapp", "archive", "backfill", "--from", "+972541112222"])
    out = capsys.readouterr().out
    assert rc == 0 and "60" in out and "retention forever" in out    # the 14-day archive would drop them: says so
    assert OLD not in out and "old note" not in out
    assert cli.main(["whatsapp", "archive", "backfill", "--since", "bad-date"]) == 2
    assert cli.main(["whatsapp", "archive", "backfill", "--stop"]) == 0
    text = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in (home / "logs").glob("*") if p.is_file())
    assert OLD not in text and "old note" not in text


def test_dashboard_backfill_button_runs_in_the_background(dash, bridge, home, archive):
    archive.set_keep_days(0)
    _old_history(bridge, n=70)
    h = {"X-Jarves": "1"}
    r, body = req(dash, "POST", "/api/whatsapp/archive", headers=h, body={"op": "backfill"})
    assert r.status == 200 and json.loads(body)["backfill"]["state"] in ("running", "done")
    end = time.time() + 10
    while time.time() < end and arc.backfill_status(home).get("state") == "running":
        time.sleep(0.02)
    r, body = req(dash, "GET", "/api/whatsapp")
    bf = json.loads(body)["archive"]["backfill"]
    assert bf["state"] == "done" and bf["added"] == 70
    r, body = req(dash, "POST", "/api/whatsapp/archive", headers=h, body={"op": "backfill_stop"})
    assert r.status == 200
