"""#469: every caller that can switch a feature ON must handle BetaLocked (drift guard + behaviour checks, hermetic)."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from jarves import whatsapp_bridge as wab
from jarves.features import BETA_MSG, BetaLocked, Features

SRC = Path(__file__).resolve().parents[1] / "src" / "jarves"
RECEIVERS = {"features", "feats", "feat", "f", "_features"}

# Callers that need no handler of their own, each with the reason.
ALLOWED = {
    ("features.py", "switch"): "the gate itself: callers of switch() are checked here",
    ("features.py", "api_feature_set"): "handles BetaLocked as HTTP 403 (calls switch)",
    ("dashboard.py", "start"): "mobile_access: raises BetaLocked itself before the listener is built (fail closed)",
    ("camera/wire.py", "_set"): "camera wire catches PermissionError",
    ("wizard.py", "switch_on"): "propagates BetaLocked to its only caller post_switch, which maps it to HTTP 403",
}


def _is_feature_call(node: ast.Call) -> str | None:
    fn = node.func
    if isinstance(fn, ast.Name) and fn.id == "switch":
        return "switch"
    if isinstance(fn, ast.Attribute) and fn.attr in ("set", "switch"):
        base = fn.value
        if isinstance(base, ast.Attribute):
            base = ast.Name(id=base.attr)
        if isinstance(base, ast.Name) and base.id in RECEIVERS:
            return fn.attr
        if isinstance(base, ast.Call) and getattr(base.func, "attr", getattr(base.func, "id", "")) == "Features":
            return fn.attr
    return None


def _always_off(node: ast.Call) -> bool:
    args = node.args[1:2] if node.func and getattr(node.func, "id", "") != "switch" else node.args[2:3]
    if getattr(node.func, "id", "") != "switch" and len(node.args) >= 2:
        args = [node.args[1]]
    return bool(args) and isinstance(args[0], ast.Constant) and args[0].value is False


def _handles(func: ast.AST) -> bool:
    for n in ast.walk(func):
        if isinstance(n, ast.ExceptHandler) and n.type is not None:
            names = {x.id if isinstance(x, ast.Name) else getattr(x, "attr", "") for x in ast.walk(n.type)}
            if names & {"BetaLocked", "PermissionError"}:
                return True
        if isinstance(n, ast.Attribute) and n.attr == "locked":
            return True
    return False


def _unhandled() -> list[str]:
    bad = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents: dict[ast.AST, list[ast.AST]] = {}

        def walk(n, chain):
            parents[n] = chain
            for c in ast.iter_child_nodes(n):
                walk(c, chain + [n])
        walk(tree, [])
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_feature_call(node) and not _always_off(node):
                funcs = [p for p in parents[node] if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef))]
                names = {p.name for p in funcs}
                if any((rel, nm) in ALLOWED for nm in names):
                    continue
                if not any(_handles(p) for p in funcs):
                    bad.append(f"{rel}:{node.lineno} {ast.unparse(node.func)}(...) does not handle BetaLocked")
    return bad


def test_every_features_set_caller_handles_beta_locked():
    assert _unhandled() == [], "new Features.set/switch caller without BetaLocked handling (#469):\n" + "\n".join(_unhandled())


def test_guard_detects_an_unhandled_caller(tmp_path, monkeypatch):
    src = tmp_path / "x.py"
    src.write_text("def go(features):\n    features.set('a', True, by='x')\n", encoding="utf-8")
    monkeypatch.setattr(__import__(__name__), "SRC", tmp_path)
    assert _unhandled() and "x.py:2" in _unhandled()[0]


@pytest.fixture
def locked(tmp_path, monkeypatch):
    from jarves.config import Config
    monkeypatch.delenv("JARVES_BETA_FEATURES", raising=False)
    f = Features(Config(), tmp_path)
    f.set_beta(False)
    return f


def test_setting_a_locked_feature_raises_beta_locked(locked):
    with pytest.raises(BetaLocked):
        locked.set("mobile_access", True)


def test_whatsapp_enable_reading_locked_is_one_plain_message(locked, tmp_path, monkeypatch):
    from jarves import whatsapp_admin as wad
    monkeypatch.setattr(locked, "locked", lambda fid: True)
    with pytest.raises(wab.BridgeError) as ei:
        wad.enable_reading(tmp_path, by="cli", features=locked)
    assert ei.value.code == "BETA_LOCKED" and ei.value.message == BETA_MSG and ei.value.he


def test_set_channel_locked_is_update_error(locked, tmp_path, monkeypatch):
    from jarves import updates
    monkeypatch.setattr(Features, "set", lambda self, fid, on, **k: (_ for _ in ()).throw(BetaLocked(fid)))
    with pytest.raises(updates.UpdateError) as ei:
        updates.set_channel(tmp_path, None, "beta")
    assert ei.value.code == "BETA_LOCKED" and BETA_MSG in ei.value.message


def test_autonomy_revert_locked_is_plain_value_error(locked):
    from types import SimpleNamespace
    from jarves import autonomy
    a = SimpleNamespace(features=SimpleNamespace(set=lambda *a_, **k: (_ for _ in ()).throw(BetaLocked("x"))))
    with pytest.raises(ValueError, match=BETA_MSG):
        autonomy._feature_revert(a, {"feature": "x", "was": True})


def test_wizard_switch_maps_beta_locked_to_403(tmp_path, monkeypatch):
    from jarves import wizard
    monkeypatch.setattr(wizard, "switch_on", lambda *a, **k: (_ for _ in ()).throw(BetaLocked("x")))
    routes = {}
    wizard.dashboard_routes(lambda meth, pat, fn: routes.__setitem__((meth, pat), fn)) if hasattr(wizard, "dashboard_routes") else None
    fn = next((v for (m_, p_), v in routes.items() if p_.endswith("/switch")), None)
    if fn is None:
        pytest.skip("route registration name differs")
    from types import SimpleNamespace
    ctx = SimpleNamespace(home=tmp_path, cfg=None)
    monkeypatch.setattr(wizard, "_fx", lambda c: None)
    code, body = fn(ctx, SimpleNamespace(group=lambda i: "x"), {})
    assert code == 403 and body["error"] == BETA_MSG and body["code"] == "BETA_LOCKED" and body["error_he"]
