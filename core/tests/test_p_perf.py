"""SW-V4 package P (performance): backend prompt size regression, schema slimming, gated rule paragraphs, dashboard
static caching, music health back-off. Budgets here only ever shrink (see docs/reviews/SWV4_PERF.md)."""
import gzip
import json

import pytest
from pydantic import BaseModel, Field

from jarves import brain as brain_mod
from jarves.skills import slim_schema
from jarves.voice.live import live_tools

from test_dashboard import dash, req  # noqa: F401  (fixture + helper)

# Measured on the test Core (all built-in skills, nothing connected): tools JSON 37.5k chars, backend rules 14.6k.
# V3/V4 baseline before package P: tools 42.7k + rules 16.7k = 59.4k. A 10% growth fails (spec: token regression).
BACKEND_CHARS_BUDGET = 52_100 * 1.10


async def test_backend_prompt_does_not_grow(core):
    tools = json.dumps(live_tools(core.registry), ensure_ascii=False)
    total = len(tools) + len(core.agent.instructions())
    assert total <= BACKEND_CHARS_BUDGET, f"backend prompt+tools is {total} chars (budget {BACKEND_CHARS_BUDGET:.0f})"
    assert '"title": "' not in tools                      # slimmed schemas carry no pydantic titles
    assert total <= 59_400 * 0.96                      # V5: 8% below the V4 baseline plus the Goals + Adaptive Memory tools (~2k chars, owner-approved pillars)


class _Args(BaseModel):
    title: str = Field(description="the note title", min_length=1, max_length=40)
    n: int = Field(default=3, ge=1, le=9)


def test_slim_schema_drops_titles_and_length_bounds_but_keeps_property_names():
    full = _Args.model_json_schema()
    slim = slim_schema(full)
    assert "title" not in slim and "title" in slim["properties"]            # a property NAMED title stays
    assert set(slim["properties"]["title"]) == {"description", "type"}
    assert slim["properties"]["n"]["minimum"] == 1 and slim["properties"]["n"]["maximum"] == 9   # numeric bounds stay
    assert slim["required"] == full["required"] if "required" in full else True
    assert len(json.dumps(slim)) < len(json.dumps(full))


def test_rules_for_absent_tools_are_left_out_but_the_camera_guard_stays():
    class Reg:
        def __init__(self, names):
            self.names = names

        def tools(self):
            return [type("T", (), {"name": n}) for n in self.names]

    bare = brain_mod.render_instructions(Reg(["get_time", "calculate"]), "JARVES", "- (none)")
    assert "Before addressing mail" not in bare and "Background tasks:" not in bare
    assert "No camera tool is provided" in bare and "ALWAYS call `calculate`" in bare
    full = brain_mod.render_instructions(Reg(["gmail_send_draft", "camera_status", "task_start"]), "JARVES", "- x")
    assert "Before addressing mail" in full and "Before claiming a camera" in full and "Background tasks:" in full
    assert "No camera tool is provided" not in full
    assert brain_mod.render_instructions(None, "JARVES", "- x") == brain_mod.INSTRUCTIONS.format(name="JARVES",
                                                                                                 memories="- x")
    assert len(bare) < len(full) - 1500


def test_static_files_are_gzipped_and_revalidated_with_etag(dash):  # noqa: F811
    r, plain = req(dash, "GET", "/static/app.js")
    etag = r.getheader("ETag")
    assert r.status == 200 and etag and r.getheader("Cache-Control") == "no-cache"
    assert r.getheader("Content-Encoding") is None
    r2, packed = req(dash, "GET", "/static/app.js", headers={"Accept-Encoding": "gzip"})
    assert r2.getheader("Content-Encoding") == "gzip" and gzip.decompress(packed) == plain
    assert len(packed) < len(plain) * 0.5
    r3, empty = req(dash, "GET", "/static/app.js", headers={"If-None-Match": etag})
    assert r3.status == 304 and empty == b""
    # API answers are never cached
    r4, _ = req(dash, "GET", "/api/overview")
    assert r4.getheader("Cache-Control") == "no-store"


async def test_unreachable_music_is_asked_less_and_less_often(core, monkeypatch):
    import asyncio
    calls = []

    class Gone:
        async def health(self):
            calls.append(1)
            return "unavailable", "Pear is closed"

    original = core.music
    core.music = Gone()
    real_sleep = asyncio.sleep

    async def fast(_s):
        await real_sleep(0)
    monkeypatch.setattr("jarves.app.asyncio.sleep", fast)
    task = asyncio.create_task(core._health_loop())
    await real_sleep(0.3)
    task.cancel()
    core.music = original
    # the loop ticks thousands of times in 0.3 s here; with the back-off the player is asked at most once
    # (the gap doubles from 20 s and monotonic time barely moves)
    assert len(calls) <= 2
