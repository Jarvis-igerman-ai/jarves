"""JAR-195: a web-search answer names its sources. The real Agents SDK + openai SDK talk to a local Responses server
(openai_fakes.ResponsesHTTPServer) that returns a web_search_call and url_citation annotations. Offline, no key."""
from __future__ import annotations

import json
from pathlib import Path

from openai_fakes import ResponsesHTTPServer
from jarves.brain import AgentBrain, Brain
from jarves.config import AIConfig
from jarves.memory import MemoryStore
from jarves.skills import SkillRegistry

PKG = Path(__file__).resolve().parents[1] / "src" / "jarves"


def cite(url: str) -> dict:
    return {"type": "url_citation", "url": url, "title": "t", "start_index": 0, "end_index": 1}


async def ask(tmp_path, monkeypatch, script: dict, *, spoken: bool = False):
    from agents import OpenAIResponsesModel
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    http = ResponsesHTTPServer([script])
    try:
        model = OpenAIResponsesModel(model="gpt-test", openai_client=http.client())
        cfg = AIConfig(daily_request_limit=10, web_search=False)
        agent = AgentBrain(cfg, SkillRegistry(), MemoryStore(tmp_path / "m.db"), model=model)
        return (await Brain(SkillRegistry(), agent).respond("מה מזג האוויר בתל אביב?", spoken=spoken,
                                                                    on_delta=lambda d: None)).text
    finally:
        http.close()


async def test_reply_ends_with_the_cited_domains(tmp_path, monkeypatch):
    text = await ask(tmp_path, monkeypatch, {"searches": 1, "text": "בתל אביב 24 מעלות.", "annotations": [
        cite("https://www.example.com/a?x=1"), cite("https://other.org/b"), cite("https://example.com/c")]})
    assert text.splitlines()[-1] == "Sources: example.com, other.org"
    assert text.startswith("בתל אביב 24 מעלות.")


async def test_spoken_reply_has_domains_but_no_raw_urls(tmp_path, monkeypatch):
    text = await ask(tmp_path, monkeypatch, {"searches": 1, "text": "חדשות.", "annotations": [
        cite(f"https://s{i}.example.com/p") for i in range(5)]}, spoken=True)
    assert "http" not in text and "/p" not in text
    assert text.endswith("Sources: s0.example.com, s1.example.com, s2.example.com")


async def test_no_citation_adds_nothing(tmp_path, monkeypatch):
    text = await ask(tmp_path, monkeypatch, {"searches": 1, "text": "בתל אביב 24 מעלות."})
    assert text == "בתל אביב 24 מעלות."


def test_sources_label_has_hebrew_in_server_and_dashboard_parts():
    name = "2026-11-11-web-sources.json"
    server = json.loads((PKG / "hebrew_parts" / name).read_text(encoding="utf-8"))
    dash = json.loads((PKG / "dashboard_static" / "locales" / "parts" / name).read_text(encoding="utf-8"))
    assert server["Sources"] == dash["dict"]["Sources"] == "מקורות"
