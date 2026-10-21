"""Shared fixtures of the SW-V4 F WhatsApp tests: a local FAKE bridge (core/tools/fake_whatsapp_bridge.py) in a tmp
JARVES home, a deterministic clock, seeded chats (Hebrew / English / Arabic, emoji, LID vs phone ids, same-name
contacts, a group, media) and small helpers. Nothing here talks to WhatsApp."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from tools.fake_whatsapp_bridge import FakeBridge  # noqa: E402

DANA = "972541112222@s.whatsapp.net"
DANA2 = "972549990000@s.whatsapp.net"          # a second "Dana" (same-name contacts)
BOB = "972543334444@s.whatsapp.net"
LAYLA = "972526667777@s.whatsapp.net"
LIDUSER = "123456789012345@lid"
GROUP = "120363000000000001@g.us"
STRANGER = "15550001111@s.whatsapp.net"
T0 = 1_790_000_000.0                           # Tue 2026-09-22, a fixed instant


class Clock:
    def __init__(self, t: float = T0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


def seed(b: FakeBridge, t: float = T0) -> None:
    b.add_chat(DANA, "דנה כהן")
    b.add_chat(DANA2, "דנה לוי")
    b.add_chat(BOB, "Bob Smith")
    b.add_chat(LAYLA, "ليلى")
    b.add_chat(LIDUSER, "Noa (LID)")
    b.add_chat(GROUP, "משפחה", is_group=True)
    b.deliver(DANA, "היי, אתה מגיע הערב? 🙂", ts=t - 3600)
    b.deliver(DANA, "תביא את המטען", ts=t - 1800)
    b.deliver(DANA2, "מה נשמע?", ts=t - 2400)
    b.deliver(BOB, "Meeting moved to 15:00.", ts=t - 1200)
    b.deliver(LAYLA, "مرحبا، كيف حالك؟", ts=t - 900)
    b.deliver(LIDUSER, "hi from a LID contact", ts=t - 600)
    b.deliver(GROUP, "group chatter", sender_name="Uncle", ts=t - 300)
    b.deliver(BOB, "", kind="image", media={"mime": "image/jpeg", "size": 120_000, "caption": "the whiteboard"},
              ts=t - 200)
    b.deliver(DANA, "", from_me=True, ts=t - 100)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def home(tmp_path) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture
def bridge(home, clock):
    """A running fake bridge in <home>/whatsapp_bridge, linked, connected, seeded, mode read, noon local time."""
    b = FakeBridge(home / "whatsapp_bridge", clock=clock, hour=lambda: 12)
    seed(b, clock())
    b.policy["mode"] = "read"
    b.policy["read_since"] = clock()
    b._save_policy()
    b.start()
    yield b
    b.stop()


def set_send_mode(b: FakeBridge, *allow: str) -> None:
    b.policy.update(mode="send", send_allow=list(allow))
    b._save_policy()


def wait_until(pred, timeout: float = 10.0, step: float = 0.02):
    """Event-style wait for a condition set by another thread (the fake's HTTP / kill-file threads)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = pred()
        if v:
            return v
        time.sleep(step)
    raise AssertionError("condition not reached")
