"""Test-wide network guard: any outbound connection to a non-loopback address raises NetworkBlocked.

Allowed: loopback (127.0.0.0/8, ::1, "localhost"), unix sockets. Real network needs `@pytest.mark.network`
(none currently; the list must stay tiny). The message names the code that dialled out so the test can be fixed
with an injected fake (e.g. the `http` parameter of UpdateChecker, httpx.MockTransport) or JARVES_TEST_OFFLINE=1."""
from __future__ import annotations

import ipaddress
import socket
import traceback

_installed = False
_allow = False
_orig: dict = {}


class NetworkBlocked(AssertionError):
    pass


def _is_local(host) -> bool:
    if isinstance(host, bytes):
        host = host.decode("ascii", "ignore")
    if not isinstance(host, str):
        return True
    h = host.strip("[]").split("%")[0]
    if h in ("", "localhost", "localhost.localdomain", "0.0.0.0", "::"):
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def _caller() -> str:
    for fr in reversed(traceback.extract_stack()[:-3]):
        f = fr.filename.replace("\\", "/")
        if "/jarves/" in f or "/tests/" in f or "/tools/" in f:
            if "netguard" not in f:
                return f"{f.rsplit('/', 3)[-3]}/{f.rsplit('/', 2)[-2]}/{fr.filename.replace(chr(92), '/').rsplit('/', 1)[-1]}:{fr.lineno} in {fr.name}"
    return "unknown caller"


def _check(address) -> None:
    if _allow or not isinstance(address, tuple) or not address:
        return                                    # unix socket (str/bytes path) or allowed
    if not _is_local(address[0]):
        raise NetworkBlocked(
            f"network guard: outbound connection to {address[0]!r} blocked (called from {_caller()}). Tests run "
            "offline: inject a fake (UpdateChecker(http=...), httpx.MockTransport) or set JARVES_TEST_OFFLINE=1; "
            "a real-network test needs @pytest.mark.network.")


def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    for name in ("connect", "connect_ex"):
        orig = getattr(socket.socket, name)
        _orig[name] = orig

        def make(orig=orig):
            def guarded(self, address, *a, **k):
                _check(address)
                return orig(self, address, *a, **k)
            return guarded
        setattr(socket.socket, name, make())
    gai = socket.getaddrinfo

    def guarded_gai(host, *a, **k):
        _check((host,))
        return gai(host, *a, **k)
    socket.getaddrinfo = guarded_gai


def allow(on: bool) -> None:
    global _allow
    _allow = on
