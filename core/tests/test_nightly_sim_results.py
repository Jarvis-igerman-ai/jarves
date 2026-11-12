"""JAR-261 (#897): GET /api/state survives a malformed or missing nightly-sim result file. Real loopback, hermetic."""
import json
import threading
import urllib.request

import pytest

from jarves.workflow.server import Server

MALFORMED = ["{not json", '{"at": 1.0, "ok": false, "failed": 5}', '{"at": NaN, "ok": true}',
             '{"at": "x", "ok": true}', "[1]", '{"at": 1' + "0" * 400 + ', "ok": true}', "[" * 100000]


@pytest.fixture
def server(tmp_path):
    srv = Server(tmp_path, 0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    thread.join(5)
    srv.server_close()


def _state(srv):
    req = urllib.request.Request(srv.origin + "/api/session", data=json.dumps({"token": srv.token}).encode(),
                                 headers={"Origin": srv.origin, "Content-Type": "application/json",
                                          "X-Workflow-Request": "1"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        cookie = resp.headers["Set-Cookie"].split(";", 1)[0]
    req = urllib.request.Request(srv.origin + "/api/state", headers={"Cookie": cookie, "Origin": srv.origin})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.code, json.loads(resp.read())


@pytest.mark.parametrize("body", MALFORMED)
def test_malformed_result_file_gives_200_with_warning(server, tmp_path, body):
    (tmp_path / "factory_sim_last.json").write_text(body, encoding="utf-8")
    status, state = _state(server)
    assert status == 200
    kinds = [a["kind"] for a in state["alerts"]]
    assert any(k.startswith("factory_sim_") for k in kinds), kinds


def test_missing_result_file_gives_200(server):
    status, state = _state(server)
    assert status == 200
    assert not [a for a in state["alerts"] if a["kind"].startswith("factory_sim_")]
