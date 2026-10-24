import socket

import pytest

import netguard


def test_outbound_connection_is_blocked_with_a_clear_message():
    with pytest.raises(netguard.NetworkBlocked) as e:
        socket.create_connection(("api.github.com", 443), timeout=1)
    assert str(e.value).startswith("network guard: outbound connection to 'api.github.com' blocked")
    with pytest.raises(netguard.NetworkBlocked):
        socket.socket().connect(("8.8.8.8", 53))


def test_loopback_is_allowed():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    c = socket.create_connection(srv.getsockname(), timeout=5)
    c.close()
    srv.close()


def test_updates_page_check_is_offline_and_deterministic(home):
    from jarves.updates import UpdateChecker
    r = UpdateChecker(home).check(force=True)
    assert "403" not in str(r) and "no connection" not in str(r)
