"""JAR-220: the owner asks a question in the chat and JARVES answers from a web search with its sources named.
The real Agents SDK + openai SDK talk to a stubbed search backend (openai_fakes.ResponsesHTTPServer, a local
Responses server returning web_search_call items and url_citation annotations). Offline, no key, no clock."""
from __future__ import annotations

import tomllib
from pathlib import Path

from openai_fakes import ResponsesHTTPServer
from jarves.brain import AgentBrain, Brain, cited_urls, sources_line
from jarves.config import AIConfig
from jarves.memory import MemoryStore
from jarves.skills import SkillRegistry

GUIDE = Path(__file__).resolve().parents[1] / "src" / "jarves" / "guides" / "data" / "web_search.toml"


def cite(url: str) -> dict:
    return {"type": "url_citation", "url": url, "title": "t", "start_index": 0, "end_index": 1}


async def chat(tmp_path, monkeypatch, script: dict, *, web_search: bool, question: str = "Weather in Tel Aviv?"):
    """One chat turn; returns (reply text, request bodies the backend received)."""
    from agents import OpenAIResponsesModel
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    http = ResponsesHTTPServer([script])
    try:
        model = OpenAIResponsesModel(model="gpt-test", openai_client=http.client())
        cfg = AIConfig(daily_request_limit=10, web_search=web_search)
        agent = AgentBrain(cfg, SkillRegistry(), MemoryStore(tmp_path / "m.db"), model=model)
        reply = await Brain(SkillRegistry(), agent).respond(question, on_delta=lambda d: None)
        return reply.text, http.bodies
    finally:
        http.close()


def tool_types(body: dict) -> list[str]:
    return [t.get("type") for t in body.get("tools") or []]


async def test_switch_on_offers_web_search_and_the_reply_cites_its_sources(tmp_path, monkeypatch):
    text, bodies = await chat(tmp_path, monkeypatch, {"searches": 1, "text": "It is 24 degrees in Tel Aviv.",
                                                      "annotations": [cite("https://www.weather.example/tlv"),
                                                                      cite("https://news.example.org/a")]},
                              web_search=True)
    assert "web_search" in tool_types(bodies[0])
    assert text == "It is 24 degrees in Tel Aviv.\n\nSources: weather.example, news.example.org"


async def test_hebrew_chat_gets_the_hebrew_sources_label_the_guide_quotes(tmp_path, monkeypatch):
    """brain.sources_line(hebrew_label=...) localizes the label for a Hebrew answer: fix_he quotes "מקורות:"."""
    text, _ = await chat(tmp_path, monkeypatch, {"searches": 1, "text": "בתל אביב 24 מעלות.",
                                                 "annotations": [cite("https://www.weather.example/tlv")]},
                         web_search=True, question="מה מזג האוויר בתל אביב?")
    assert text == "בתל אביב 24 מעלות.\n\nמקורות: weather.example"
    assert "\"מקורות:\"" in tomllib.loads(GUIDE.read_text(encoding="utf-8"))["problems"][-1]["fix_he"]


async def test_switch_off_never_offers_web_search(tmp_path, monkeypatch):
    text, bodies = await chat(tmp_path, monkeypatch, {"text": "I cannot check the web right now."}, web_search=False)
    assert bodies and all("web_search" not in tool_types(b) for b in bodies)
    assert "Sources" not in text


def test_cited_urls_reads_only_url_citations_on_answer_messages():
    resp = {"output": [
        {"type": "web_search_call", "annotations": [cite("https://ignored.example/")]},
        {"type": "message", "content": [{"type": "output_text", "annotations": [
            cite("https://a.example/1"), {"type": "file_citation", "url": "https://file.example/"},
            cite("https://b.example/2")]}]}]}
    assert cited_urls(resp) == ["https://a.example/1", "https://b.example/2"]


def test_sources_line_dedupes_domains_caps_at_three_and_hides_urls():
    urls = ["https://www.a.example/x?q=1", "https://a.example/y", "https://b.example/", "https://c.example/",
            "https://d.example/", "not a url"]
    assert sources_line(urls) == "Sources: a.example, b.example, c.example"
    assert sources_line([]) == "" and sources_line(["not a url"]) == ""


def test_guide_explains_the_sources_line_in_both_languages():
    guide = tomllib.loads(GUIDE.read_text(encoding="utf-8"))
    sources = [p for p in guide["problems"] if "Sources:" in p.get("fix_en", "")]
    assert sources and all(p.get(k) for p in sources for k in ("he", "en", "fix_he", "fix_en"))
