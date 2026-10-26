"""Companion API v1 contract (V7-A1): every documented route exists, every route the server code or the PWA uses is
documented (drift guard), constants match, and the probe routes answer in the documented shape (fake Core, real HTTP)."""
import json
import re
import time
from pathlib import Path

from jarves import mobile, mobile_companion, mobile_plans
from test_mobile_access import admin, fake, pair, req, srv  # noqa: F401  (fixtures and helpers)

ROOT = Path(__file__).resolve().parents[2]
SRC = Path(mobile.__file__).parent
CONTRACT = json.loads((ROOT / "docs" / "api" / "companion_v1.json").read_text(encoding="utf-8"))
ROUTES = CONTRACT["routes"]
TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "object": dict, "array": list}


def _norm(p: str) -> str:
    return re.sub(r"\{[a-z]+\}", "{id}", p)


def _used(text: str) -> set[str]:
    text = re.sub(r"\([^)\"]*\)", "{id}", text)
    return {_norm(m) for m in re.findall(r"/api/m/[A-Za-z0-9_/{}]*", text)}


def _server_code() -> str:
    return "".join(f.read_text(encoding="utf-8") for f in [*SRC.glob("mobile*.py"), SRC / "phone_location.py", SRC / "drive_actions.py"])


def test_every_route_the_server_and_the_pwa_use_is_documented():
    doc = {_norm(r["path"]) for r in ROUTES}
    pwa = "".join(f.read_text(encoding="utf-8") for f in (SRC / "dashboard_static" / "mobile").glob("*.js"))
    for where, text in (("server", _server_code()), ("PWA", pwa)):
        for p in _used(text):
            if p in ("/api/m/", "/api/m"):
                continue
            ok = p in doc or (p.endswith("/") and any(d.startswith(p) for d in doc))
            assert ok, f"{where} uses {p} but docs/api/companion_v1.json does not list it: document it (drift guard)"
    used = _used(_server_code())
    for r in ROUTES:                                    # documented but gone from the code
        assert _norm(r["path"]) in used, f"documented route {r['path']} is not in the server code"


def test_contract_matches_the_code_constants():
    assert set(CONTRACT["scopes"]["default"]) == set(mobile.DEFAULT_SCOPES)
    assert set(CONTRACT["scopes"]["default"]) | set(CONTRACT["scopes"]["optional"]) == set(mobile.ALL_SCOPES)
    assert set(mobile_companion.SUPPORTED_SCOPES) == set(mobile.ALL_SCOPES)
    assert CONTRACT["not_available"]["pc_only_segments"] == sorted(mobile.PC_ONLY_SEGMENTS)
    assert set(CONTRACT["not_available"]["plan_ops"]) == set(mobile_plans.PC_ONLY_OPS)
    assert CONTRACT["version"] == mobile_companion.API_VERSION
    assert CONTRACT["rate_limits"]["chat_per_min"] == mobile.CHAT_RATE_PER_MIN
    assert CONTRACT["rate_limits"]["per_device_per_min"] == mobile.DEVICE_RATE_PER_MIN
    for r in ROUTES:
        assert r["scope"] in (None, *mobile.ALL_SCOPES) and r["page"] in (None, *mobile.PAGES)


def _check(obj, schema, where):
    for k in schema["required"]:
        assert k in obj, f"{where}: missing {k}"
    for k, t in schema.get("types", {}).items():
        if k in obj and obj[k] is not None:
            assert isinstance(obj[k], TYPES[t]) and not (t == "integer" and isinstance(obj[k], bool)), f"{where}: {k} is not {t}"


def test_probe_routes_answer_in_the_documented_shape(srv):
    tok, dev, _ = pair(srv)
    st, _ = admin(srv, "POST", f"/api/mobile/devices/{dev}/scopes", {"scopes": list(mobile.ALL_SCOPES)})
    assert st == 200
    probes = [r for r in ROUTES if r.get("probe")]
    assert {r["path"] for r in probes} >= {mobile_companion.INFO_PATH, "/api/m/me", "/api/m/status", "/api/m/sync"}
    for r in probes:
        st, body, _resp = req(srv, r["method"], r["path"], token=tok)
        assert st == 200, (r["path"], body)
        _check(body, r.get("response", {"required": []}), r["path"])
    st, body, resp = req(srv, "GET", mobile_companion.INFO_PATH, token=tok)
    assert resp.getheader("X-Jarves-Api") == "1" and body["pc"]["state"] == "online" and abs(body["server_time"] - time.time()) < 30
    assert body["phone_approvals"] is False and body["features"]["companion_api"]["stage"] == "beta"


def test_info_needs_a_valid_token_and_no_scope(srv):
    assert req(srv, "GET", mobile_companion.INFO_PATH)[0] == 401
    tok, dev, _ = pair(srv)
    admin(srv, "POST", f"/api/mobile/devices/{dev}/scopes", {"scopes": []})
    assert req(srv, "GET", mobile_companion.INFO_PATH, token=tok)[0] == 200
    assert req(srv, "POST", mobile_companion.INFO_PATH, token=tok, body={})[0] == 404      # read-only: no write on this path
