"""SW-V4 E (#49): the one local transport (Windows named pipe / Unix socket) - mutual HMAC auth with the per-instance
token, a wrong or missing token refused, a silent client dropped, oversize and bad frames, pushes, two profiles
(two homes) side by side, a restart rewrites the token (the old one stops working), and the stop is clean."""
from __future__ import annotations

import json
import sys
import threading
import time
from multiprocessing import connection as mpc

import pytest

from jarves.orchestrator import bridge


def echo(conn, msg):
    if msg.get("op") == "echo":
        return {"ok": True, "x": msg.get("x"), "role": conn.role}
    if msg.get("op") == "push":
        conn.push({"push": "message", "n": 1})
        return {"ok": True}
    return {"ok": False, "error": "unknown op"}


def test_auth_request_reply_push_and_stop(tmp_path):
    s = bridge.BridgeServer(tmp_path, echo).start()
    try:
        c = bridge.BridgeClient.connect(tmp_path)
        got = []
        cv = threading.Condition()

        def on_push(m):
            with cv:
                got.append(m)
                cv.notify_all()
        c.listen(on_push)
        assert c.request("echo", x=[1, "שלום"]) == {"ok": True, "x": [1, "שלום"], "role": "", "rid": 1}
        assert c.request("push")["ok"] is True
        with cv:
            assert cv.wait_for(lambda: any(m.get("push") == "message" for m in got), 20)
        assert bridge.try_request(tmp_path, "echo", x=2)["x"] == 2
        c.close()
    finally:
        s.stop()
    assert bridge.try_request(tmp_path, "echo", x=3) is None        # stopped: nobody answers


def test_wrong_and_missing_token_refused(tmp_path):
    s = bridge.BridgeServer(tmp_path, echo).start()
    try:
        t0 = time.monotonic()
        with pytest.raises(bridge.BridgeError, match="authentication"):
            bridge.BridgeClient.connect(tmp_path, token=b"0" * 64)
        assert time.monotonic() - t0 < bridge.STEP_S                 # refused at once, not by a timeout
        assert s.refused >= 1
        (tmp_path / "agents" / "bridge.token").unlink()
        with pytest.raises(bridge.BridgeError, match="no bridge token"):
            bridge.BridgeClient.connect(tmp_path)
    finally:
        s.stop()


def test_silent_client_dropped_and_server_keeps_serving(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "STEP_S", 0.5)
    s = bridge.BridgeServer(tmp_path, echo).start()
    try:
        raw = mpc.Client(*bridge.address(tmp_path))                  # connects, never answers the challenge
        assert raw.recv_bytes().startswith(bridge.MAGIC)
        deadline = time.monotonic() + 20
        while s.refused < 1 and time.monotonic() < deadline:
            threading.Event().wait(0.05)
        assert s.refused >= 1
        raw.close()
        assert bridge.try_request(tmp_path, "echo", x=5)["x"] == 5
    finally:
        s.stop()


def test_bad_and_oversize_frames(tmp_path):
    s = bridge.BridgeServer(tmp_path, echo).start()
    try:
        c = bridge.BridgeClient.connect(tmp_path)
        c.raw.send_bytes(b"not json")
        assert json.loads(c.raw.recv_bytes()) == {"ok": False, "error": "bad frame"}
        with pytest.raises((EOFError, OSError)):                     # too big: the server closes the connection
            c.raw.send_bytes(b"x" * (bridge.MAX_FRAME + 10))         # (the write itself may already fail)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if c.raw.poll(0.5):
                    c.raw.recv_bytes()
        assert bridge.try_request(tmp_path, "echo", x=6)["x"] == 6   # others unaffected
    finally:
        s.stop()


def test_two_profiles_have_separate_pipes_and_tokens(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    assert bridge.address(a) != bridge.address(b)
    sa = bridge.BridgeServer(a, lambda c, m: {"ok": True, "home": "a"}).start()
    sb = bridge.BridgeServer(b, lambda c, m: {"ok": True, "home": "b"}).start()
    try:
        assert bridge.try_request(a, "x")["home"] == "a"
        assert bridge.try_request(b, "x")["home"] == "b"
        with pytest.raises(bridge.BridgeError):                      # a's token does not open b
            bridge.BridgeClient.connect(b, token=bridge.read_token(a))
    finally:
        sa.stop()
        sb.stop()


def test_restart_rotates_the_token(tmp_path):
    s = bridge.BridgeServer(tmp_path, echo).start()
    old = bridge.read_token(tmp_path)
    s.stop()
    s2 = bridge.BridgeServer(tmp_path, echo).start()
    try:
        assert bridge.read_token(tmp_path) != old
        with pytest.raises(bridge.BridgeError):
            bridge.BridgeClient.connect(tmp_path, token=old)
        assert bridge.try_request(tmp_path, "echo", x=1)["x"] == 1
    finally:
        s2.stop()


@pytest.mark.windows_only
def test_windows_pipe_rejects_remote_clients_flag(tmp_path):
    addr, fam = bridge.address(tmp_path)
    assert fam == "AF_PIPE" and addr.startswith(r"\\.\pipe\jarves-agents-")
    assert bridge.PIPE_REJECT_REMOTE_CLIENTS == 0x8


@pytest.mark.linux_only
def test_unix_socket_is_owner_only(tmp_path):
    import os
    import stat
    s = bridge.BridgeServer(tmp_path, echo).start()
    try:
        addr, fam = bridge.address(tmp_path)
        assert fam == "AF_UNIX"
        assert stat.S_IMODE(os.stat(addr).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(tmp_path / "agents" / "bridge.token").st_mode) == 0o600
    finally:
        s.stop()


def test_token_file_never_logged(tmp_path, capsys):
    from jarves import logs
    s = bridge.BridgeServer(tmp_path, echo).start()
    tok = bridge.read_token(tmp_path).decode()
    s.stop()
    out = capsys.readouterr()
    assert tok not in out.out + out.err
    assert tok not in json.dumps(bridge.safe_view(tmp_path))
    assert sys.platform == "win32" or logs is not None
