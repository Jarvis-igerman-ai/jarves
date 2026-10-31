"""Explicit owner model choice crosses existing surfaces without changing approval policy."""
import argparse
from types import SimpleNamespace

import pytest

from jarves.orchestrator import surfaces
from jarves.orchestrator.service import Orchestrator


@pytest.mark.parametrize("provider", ["claude", "codex", "opencode"])
def test_existing_service_dispatch_preserves_exact_model_and_requires_approval(provider):
    service = Orchestrator.__new__(Orchestrator)
    seen = {}

    def start(p, project, task, **kwargs):
        seen.update(provider=p, project=project, task=task, **kwargs)
        return {"ok": False, "code": "NOT_APPROVED"}

    service.start_session = start
    result = service.call("start", provider=provider, project="jarves", task="Review task T", model="owner/model")
    assert result["code"] == "NOT_APPROVED"
    assert seen["model"] == "owner/model"
    assert seen["provider"] == provider
    assert seen["approved"] is False and seen["here"] is False


def test_cli_model_is_per_request_and_uses_existing_explicit_confirmation(tmp_path, monkeypatch, capsys):
    parser = argparse.ArgumentParser()
    surfaces.add_cli(parser.add_subparsers(dest="command"))
    parsed = parser.parse_args(["agents", "start", "codex", "jarves", "Review T",
                               "--model", "owner-model", "--yes", "--json"])
    monkeypatch.setattr("jarves.features.Features", lambda *_: SimpleNamespace(enabled=lambda _: True))
    seen = {}

    def call(home, cfg, op, **kwargs):
        seen.update(op=op, **kwargs)
        return {"ok": True}

    monkeypatch.setattr(surfaces.svc, "call", call)
    assert surfaces.cmd_agents(parsed, object(), tmp_path) == 0
    assert seen["model"] == "owner-model" and seen["provider"] == "codex"
    assert seen["approved"] is True and seen["here"] is False
    capsys.readouterr()


@pytest.mark.asyncio
async def test_native_tool_keeps_owner_model_but_does_not_grant_approval(tmp_path, monkeypatch):
    seen = {}

    def call(home, cfg, op, **kwargs):
        seen.update(op=op, **kwargs)
        return {"ok": False, "code": "NOT_APPROVED", "text": "Owner approval needed"}

    monkeypatch.setattr(surfaces.svc, "call", call)
    tool = next(t for t in surfaces.make_skill(tmp_path, object()).tools() if t.name == "agents_start")
    result = await tool.handler(tool.args(provider="opencode", project="jarves", task="Review T", model="local/chosen"))
    assert not result.ok and tool.permission == "action"
    assert seen["provider"] == "opencode" and seen["model"] == "local/chosen"
    assert "approved" not in seen


@pytest.mark.parametrize("model", ["unsafe\nmodel", "unsafe\x00model", "x" * 201, 123])
def test_invalid_model_is_refused_before_provider_or_project_access(model):
    service = Orchestrator.__new__(Orchestrator)
    assert service.start_session("codex", "jarves", "Review T", model=model)["ok"] is False
