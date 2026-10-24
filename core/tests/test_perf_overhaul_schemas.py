"""Perf overhaul 4.5 (#159): tool JSON schemas are built once per args model and copied per call; the shipped guide
folder is not re-listed on every read. Output must be IDENTICAL to the uncached path (the model sees the same tools)."""
import statistics
import time

from pydantic import BaseModel, Field

from jarves import guides
from jarves.skills import Tool, ToolResult, slim_schema, tool_schema


async def _noop(args) -> ToolResult:
    return ToolResult(True, "ok")


def _tools(n: int) -> list[Tool]:
    out = []
    for i in range(n):
        class Args(BaseModel):
            query: str = Field(..., description=f"what to look for {i}", max_length=200)
            limit: int = Field(5, ge=1, le=50)
            day: str | None = Field(None, description="YYYY-MM-DD")
            tags: list[str] = Field(default_factory=list)
        out.append(Tool(f"t{i}", f"tool {i}", _noop, Args, permission="read"))
    return out


def test_cached_schema_is_identical_to_uncached():
    for t in _tools(5):
        assert tool_schema(t) == slim_schema(t.args.model_json_schema())
        assert tool_schema(t) == tool_schema(t)


def test_each_call_gets_its_own_copy():
    t = _tools(1)[0]
    a = tool_schema(t)
    a["properties"]["query"]["description"] = "changed by a caller"
    a["required"].append("x")
    b = tool_schema(t)
    assert b["properties"]["query"]["description"] == "what to look for 0"
    assert "x" not in b["required"]


def test_real_registry_schemas_unchanged():
    """Every Skill the Core registers at start (clock, calc, memory, features...) gives the same schema cached."""
    from jarves.skills.builtin import CalcSkill, ClockSkill
    for skill in (ClockSkill(), CalcSkill()):
        for t in skill.tools():
            assert tool_schema(t) == slim_schema(t.args.model_json_schema())


def test_repeat_tool_list_is_much_faster():
    """Median of 5 rounds over 60 tools: the cached rounds cost a fraction of pydantic's schema generation (ratio, so a
    slow CI runner does not make it flaky)."""
    tools = _tools(60)

    def uncached() -> float:
        t = time.perf_counter()
        for x in tools:
            slim_schema(x.args.model_json_schema())
        return time.perf_counter() - t

    def cached() -> float:
        t = time.perf_counter()
        for x in tools:
            tool_schema(x)
        return time.perf_counter() - t

    cached()                                            # fills the cache
    base = statistics.median(uncached() for _ in range(5))
    fast = statistics.median(cached() for _ in range(5))
    assert fast < base * 0.5, (fast, base)


def test_shipped_guides_not_restated_on_every_read(monkeypatch):
    guides.load()                                       # fills the cache
    calls = []
    real_glob = type(guides.DATA).glob

    def counting_glob(self, pattern):
        calls.append(pattern)
        return real_glob(self, pattern)

    monkeypatch.setattr(type(guides.DATA), "glob", counting_glob)
    for _ in range(20):
        assert guides.load()
    assert len(calls) <= 1                              # at most one re-check inside RECHECK_S
    monkeypatch.setattr(guides, "RECHECK_S", 0.0)
    guides.load()
    assert len(calls) >= 1                              # past the window the folder is checked again (hot reload)


def test_explicit_guide_folder_is_always_checked(tmp_path):
    src = sorted(guides.DATA.glob("*.toml"))[:2]
    (tmp_path / src[0].name).write_bytes(src[0].read_bytes())
    first = set(guides.load(tmp_path))
    assert len(first) == 1
    (tmp_path / src[1].name).write_bytes(src[1].read_bytes())
    assert len(set(guides.load(tmp_path))) == 2         # a given folder (tests, tools) re-lists every time
