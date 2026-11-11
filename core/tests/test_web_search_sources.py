"""JAR-195: a web-search answer names its sources. The real Agents SDK + openai SDK talk to a local Responses server
(openai_fakes.ResponsesHTTPServer) that returns a web_search_call and url_citation annotations. Offline, no key."""
from __future__ import annotations

from openai_fakes import ResponsesHTTPServer
from jarves.brain import AgentBrain, Brain
from jarves.config import AIConfig
from jarves.memory import MemoryStore
from jarves.skills import SkillRegistry


def cite(url: str) -> dict:
    return {"type": "url_citation", "url": url, "title": "t", "start_index": 0, "end_index": 1}


async def ask(tmp_path, monkeypatch, script: dict, *, spoken: bool = False, question: str = "מה מזג האוויר בתל אביב?"):
    """Returns (reply text, the agent's in-RAM conversation items after the turn)."""
    from agents import OpenAIResponsesModel
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    http = ResponsesHTTPServer([script])
    try:
        model = OpenAIResponsesModel(model="gpt-test", openai_client=http.client())
        cfg = AIConfig(daily_request_limit=10, web_search=False)
        agent = AgentBrain(cfg, SkillRegistry(), MemoryStore(tmp_path / "m.db"), model=model)
        reply = await Brain(SkillRegistry(), agent).respond(question, spoken=spoken, on_delta=lambda d: None)
        return reply.text, await agent._session.get_items()
    finally:
        http.close()


async def test_hebrew_reply_ends_with_the_hebrew_label_and_cited_domains(tmp_path, monkeypatch):
    text, _ = await ask(tmp_path, monkeypatch, {"searches": 1, "text": "בתל אביב 24 מעלות.", "annotations": [
        cite("https://www.example.com/a?x=1"), cite("https://other.org/b"), cite("https://example.com/c")]})
    assert text.splitlines()[-1] == "מקורות: example.com, other.org"     # label from hebrew_parts
    assert text.startswith("בתל אביב 24 מעלות.")


async def test_english_reply_keeps_the_english_label(tmp_path, monkeypatch):
    text, _ = await ask(tmp_path, monkeypatch, {"searches": 1, "text": "It is 24 degrees in Tel Aviv.", "annotations": [
        cite("https://example.com/a"), cite("https://other.org/b")]}, question="Weather in Tel Aviv?")
    assert text.splitlines()[-1] == "Sources: example.com, other.org"


async def test_spoken_reply_has_hebrew_label_and_domains_but_no_raw_urls(tmp_path, monkeypatch):
    text, _ = await ask(tmp_path, monkeypatch, {"searches": 1, "text": "חדשות.", "annotations": [
        cite(f"https://s{i}.example.com/p") for i in range(5)]}, spoken=True)
    assert "http" not in text and "/p" not in text and "Sources" not in text
    assert text.endswith("מקורות: s0.example.com, s1.example.com, s2.example.com")


async def test_no_citation_adds_nothing(tmp_path, monkeypatch):
    text, _ = await ask(tmp_path, monkeypatch, {"searches": 1, "text": "בתל אביב 24 מעלות."})
    assert text == "בתל אביב 24 מעלות."


async def test_sources_line_is_not_kept_in_the_conversation_memory(tmp_path, monkeypatch):
    """The model never sees the line in later turns, so it does not learn to write source lines itself."""
    text, items = await ask(tmp_path, monkeypatch, {"searches": 1, "text": "בתל אביב 24 מעלות.",
                                                    "annotations": [cite("https://example.com/a")]})
    assert "מקורות" in text and "מקורות" not in str(items) and "בתל אביב 24 מעלות." in str(items)
