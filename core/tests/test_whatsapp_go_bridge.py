"""SW-V4 F: the Core's client and start path against the REAL Go bridge binary (whatsmeow engine), offline.

Runs only where the binary was built (scripts/build_whatsapp_bridge.ps1 -> bridge/whatsapp/dist/; the CI job
`whatsapp-bridge` builds and tests it with `go test`). Mode stays OFF and no device is linked, so the engine never
connects to WhatsApp: this checks the shared contract surface - start via the Core's start_bridge (state dir
argument, endpoint.json, tokens), status, the user token refused on admin endpoints, the Host guard, the watch window,
the kill file within 2 s, an allow-list change, health mapping and the orderly shutdown. Where the binary is absent
the tests are not defined (never skipped)."""
from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest

from jarves import whatsapp_bridge as wab
from jarves.whatsapp_bridge import BridgeClient, BridgeError

BIN = Path(__file__).resolve().parents[2] / "bridge" / "whatsapp" / "dist" / "whatsapp_bridge.exe"

if BIN.is_file():
    @pytest.fixture
    def go_bridge(home, monkeypatch):
        monkeypatch.setenv(wab.BRIDGE_ENV, str(BIN))
        ok, why = wab.start_bridge(home, wait_s=30)
        assert ok, why
        yield home
        try:
            BridgeClient(home, admin=True, timeout=3).call("POST", "/shutdown")
        except BridgeError:
            pass
        end = time.monotonic() + 15
        while time.monotonic() < end and (home / "whatsapp_bridge" / "endpoint.json").exists():
            time.sleep(0.05)

    def test_go_bridge_contract_offline(go_bridge):
        home = go_bridge
        d = home / "whatsapp_bridge"
        st = BridgeClient(home).status()
        assert st["engine"] == "whatsmeow" and st["mode"] == "off" and st["connection"] == "not_linked"
        assert st["key_protection"] in ("dpapi", "file") and (d / "store.key").exists()
        assert wab.health_view(st)[0] == "degraded"
        tok = (d / "token").read_text().strip()
        port = wab.endpoint(home)["port"]
        r = httpx.get(f"http://127.0.0.1:{port}/status", headers={"Authorization": f"Bearer {tok}",
                                                                 "Host": "evil.example"}, trust_env=False)
        assert r.status_code == 403 and r.json()["error"]["code"] == "WA_BAD_HOST"
        for path, body in (("/mode", {"mode": "send", "override_watch": True}), ("/kill", {"on": False}),
                           ("/allow", {"list": "send", "op": "add", "chat": "+972541112222"})):
            with pytest.raises(BridgeError) as e:
                BridgeClient(home).call("POST", path, body=body)
            assert e.value.code == "WA_FORBIDDEN"
        ad = BridgeClient(home, admin=True)
        with pytest.raises(BridgeError) as e:
            ad.call("POST", "/mode", body={"mode": "send"})
        assert e.value.code == "WA_WATCH_WINDOW" and e.value.extra["days_left"] == 14
        ad.call("POST", "/allow", body={"list": "send", "op": "add", "chat": "+972541112222"})
        assert ad.call("GET", "/policy")["send_allow"] == ["972541112222@s.whatsapp.net"]
        with pytest.raises(BridgeError) as e:
            BridgeClient(home).chats()
        assert e.value.code == "WA_MODE_OFF"
        t0 = time.monotonic()
        (d / "KILL").write_text("owner_test\n")
        while not BridgeClient(home).status()["killed"]:
            assert time.monotonic() - t0 < 2.0
            time.sleep(0.05)
        assert "killed" in wab.health_view(BridgeClient(home).status())[1]
        ad.call("POST", "/kill", body={"on": False})
        assert not (d / "KILL").exists() and not BridgeClient(home).status()["killed"]
        with pytest.raises(BridgeError) as e:
            BridgeClient(home).send("972541112222@s.whatsapp.net", "x", "")
        assert e.value.code == "WA_MODE_OFF"
