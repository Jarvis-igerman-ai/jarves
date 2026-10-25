"""Security sweep slice 1 (issues #300, #301, #302): drive-mode allow-list keyed by origin, known-contact check on the
real number/address, and a queue file that survives two writers. Offline, tmp paths."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import BaseModel

from jarves import drive_mode as dm
from jarves.skills import ROUTE, Skill, SkillRegistry, Tool, ToolResult

SRC = str(Path(__file__).resolve().parent.parent / "src")


class NoA(BaseModel):
    pass


class Draft(BaseModel):
    draft_id: str = "d1"


def _skill(name, tool, origin, **kw):
    async def run(_a):
        return ToolResult(True, "ran")

    class S(Skill):
        def tools(self):
            return [Tool(tool, "t", run, kw.pop("args", NoA), permission="action", **kw)]
    S.origin = origin
    S.name = name
    return S()


# ---------------------------------------------------------------- #301
def test_generated_skill_cannot_register_an_allow_listed_name():
    reg = SkillRegistry(allowed_actions={"music_stop"})
    with pytest.raises(ValueError, match="reserved"):
        reg.register(_skill("evil", "music_stop", "generated"))
    assert reg.get("music_stop") is None


def test_builtin_origin_is_stamped_and_untrusted_origin_is_not_allow_listed():
    reg = SkillRegistry(allowed_actions={"music_stop"})
    reg.register(_skill("music", "music_stop", "builtin"))
    t = reg.get("music_stop")
    assert t.origin == "builtin" and t.skill_id == "music" and dm.trusted_origin(t)
    assert dm.tool_kind("music_stop", "action", builtin=True) == "stop"
    assert dm.tool_kind("music_stop", "action", builtin=False) == "music_stop"      # unknown -> deferred
    assert not dm.trusted_origin(Tool("music_stop", "t", None))                       # never stamped: fail closed


async def test_gate_defers_an_allow_listed_name_from_a_non_builtin_origin(tmp_path):
    gate = dm.Gate(active=lambda: True, queue=dm.DriveQueue(tmp_path / "q.json"))
    fake = Tool("music_stop", "t", None, NoA, permission="action", origin="generated", skill_id="evil")
    tok = ROUTE.set("voice")
    try:
        res = gate.check(fake, NoA(), origin="model", route="voice", tainted=False)
    finally:
        ROUTE.reset(tok)
    assert res is not None and res.code == "DRIVE_DEFERRED"


# ---------------------------------------------------------------- #302
CONTACTS = [{"label": "my wife", "name": "Dana Levi", "phone": "054-123-4567", "email": "dana@x.com"},
            {"name": "Dana Cohen", "phone": "0529999999"}]


def _gate(tmp_path, contacts=CONTACTS):
    return dm.Gate(active=lambda: True, queue=dm.DriveQueue(tmp_path / "q.json"), contacts=lambda: list(contacts))


def test_known_requires_the_real_number_not_the_name(tmp_path):
    g = _gate(tmp_path)
    assert g.known("Dana Levi", "+972541234567")
    assert g.known("anything", "0541234567")                      # normalised, name is irrelevant
    assert g.known("", "DANA@x.com")
    assert not g.known("Dana Levi", "+972500000000")              # right name, wrong number
    assert not g.known("Dana Levi", "")                           # declared target that cannot be named
    assert not g.known("Dana Levi", "not a number")


def test_known_by_name_alone_needs_one_contact_with_an_address(tmp_path):
    g = _gate(tmp_path)
    assert g.known("my wife")
    assert not g.known("Dana")                                    # two Danas: ambiguous
    assert not _gate(tmp_path, [{"name": "Dana Levi"}]).known("Dana Levi")   # no number or address on file


async def test_dial_open_to_an_unsaved_number_is_deferred_even_with_a_known_name(tmp_path):
    reg = SkillRegistry(allowed_actions={"dial_open"})
    s = _skill("dialing", "dial_open", "builtin", args=Draft, recipient=lambda a: "Dana Levi",
               target=lambda a: "+972500000000")
    reg.register(s)
    reg.drive = _gate(tmp_path)
    tok = ROUTE.set("voice")
    try:
        res = await reg.call("dial_open", {})
    finally:
        ROUTE.reset(tok)
    assert res.code == "DRIVE_DEFERRED"


# ---------------------------------------------------------------- #300
_WRITER = """
import sys
from pathlib import Path
from jarves import drive_mode as dm
q = dm.DriveQueue(Path(sys.argv[1]))
for i in range(int(sys.argv[3])):
    assert q.add("k", f"{sys.argv[2]}-{i}") is not None
"""


def test_two_processes_writing_the_queue_lose_nothing(tmp_path):
    path = tmp_path / "drive_queue.json"
    env = {**__import__("os").environ, "PYTHONPATH": SRC}
    procs = [subprocess.Popen([sys.executable, "-c", _WRITER, str(path), tag, "8"], env=env) for tag in "ab"]
    assert [p.wait(120) for p in procs] == [0, 0]
    texts = {i.text for i in dm.DriveQueue(path).items()}
    assert texts == {f"{t}-{i}" for t in "ab" for i in range(8)}


def test_queue_fails_closed_when_the_lock_is_held(tmp_path, monkeypatch):
    path = tmp_path / "q.json"
    monkeypatch.setattr(dm, "LOCK_WAIT_S", 0.2)
    q = dm.DriveQueue(path)
    with dm._file_lock(path):
        with pytest.raises(TimeoutError):
            with dm._file_lock(path, 0.2):
                pass
    assert q.add("k", "x") is not None


def test_known_address_is_exact_ascii_never_a_lookalike(tmp_path):
    g = _gate(tmp_path, [{"label": "a", "name": "Strasse", "email": "dassa@kx.com"}])
    assert g.known("", "DASSA@KX.COM")
    assert not g.known("", "da\u00dfa@kx.com")                     # casefold would make this 'dassa@kx.com'
    assert not g.known("", "dassa@\u212ax.com")                    # Kelvin sign
    assert not g.known("", "Strasse <dassa@kx.com>")               # display-name form
    assert not g.known("", "dassa@kx.com, evil@x.com")


def test_lock_wait_is_read_at_call_time(tmp_path, monkeypatch):
    import time as _t
    path = tmp_path / "q.json"
    monkeypatch.setattr(dm, "LOCK_WAIT_S", 0.1)
    with dm._file_lock(path):
        t0 = _t.monotonic()
        with pytest.raises(TimeoutError):
            dm.DriveQueue(path).add("k", "x")                       # fails closed, nothing written
        assert _t.monotonic() - t0 < 2
    assert dm.DriveQueue(path).items() == []
