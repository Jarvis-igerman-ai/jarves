"""#331: `jarvis chat` rendering (Markdown while streaming, compact tool lines, status line). Pure functions: no Core,
no terminal, no clock."""
import io
import os

from jarves import chat_cli, chat_render
from jarves.chat_cli import ChatSession
from jarves.chat_render import MarkdownStream, render

B, C, R = "\x1b[1m", "\x1b[36m", "\x1b[0m"


def stream(text: str, size: int = 1) -> str:
    md = MarkdownStream(True)
    return "".join(md.feed(text[i:i + size]) for i in range(0, len(text), size)) + md.flush()


def test_without_colour_the_text_is_unchanged():
    raw = "# Title\n**bold** and `code`\n- item\n```py\nx = 1\n```\n"
    assert render(raw, False) == raw
    md = MarkdownStream(False)
    assert md.feed(raw) == raw and md.flush() == ""


def test_markdown_renders_the_same_however_the_stream_is_chunked():
    raw = "# Title\nsay **hi** to `x`\n- one\n* two\n```python\nprint(1)\n```\nend 2*3\n#tag"
    whole = render(raw, True)
    assert whole == stream(raw, 1) == stream(raw, 3) == stream(raw, 7)
    assert f"{B}Title{R}\n" in whole
    assert f"say {B}hi{R} to {C}x{R}\n" in whole
    assert "• one\n• two\n" in whole
    assert "```" not in whole and f"{C}print(1){R}\n" in whole
    assert "end 2*3\n" in whole and whole.endswith("#tag")


def test_hebrew_lines_get_no_escape_codes_and_are_never_reordered():
    raw = "**שלום** עולם `קוד`\n# כותרת\n- פריט\n"
    out = render(raw, True)
    assert "\x1b" not in out
    assert out == "שלום עולם קוד\nכותרת\n• פריט\n"


def test_style_opened_before_hebrew_is_closed_before_it():
    out = render("**ok שלום**\n", True)
    assert out == f"{B}ok {R}שלום\n"


def test_tool_lines_plain_and_compact(monkeypatch):
    out = io.StringIO()
    s = ChatSession(None, None, out)
    s._tool_line({"name": "send_mail", "ok": False, "code": "TOOL_DECLINED"}, False)
    s._tool_line({"name": "get_time", "ok": True, "code": "ok"}, False)
    assert "  > send_mail  FAILED (not approved) [TOOL_DECLINED]" in out.getvalue()
    assert "  > get_time  ok\n" in out.getvalue()
    tty = io.StringIO()
    tty.isatty = lambda: True
    monkeypatch.delenv("NO_COLOR", raising=False)
    s = ChatSession(None, None, tty)
    s._tool_line({"name": "get_time", "ok": True}, False)
    s._tool_line({"name": "x", "ok": False, "code": "TOOL_TIMEOUT"}, False)
    assert "✓ get_time" in tty.getvalue() and "✗ x  timed out [TOOL_TIMEOUT]" in tty.getvalue()


def test_status_line_and_prompt():
    s = ChatSession(None, None, io.StringIO())
    s.state = {"text_model": "gpt-test"}
    assert s.prompt() == "> " and s.prompt(True) == "  ... "
    assert chat_render.status_line("text", False, "gpt-test") == "text · voice off · gpt-test"
    assert s.status_line() == "text · voice off · gpt-test"
    s.forced_silent, s.voice = True, True
    assert s.prompt() == "[silent] > " and "voice off" in s.status_line()


def test_tool_group_line_fits_the_width_and_keeps_the_newest_names():
    assert chat_render.tool_group(["get_time"]) == "  ✓ get_time"
    assert chat_render.tool_group(["a", "b", "c"]) == "  ✓ a, b, c  (3)"
    names = [f"tool_{i:02d}" for i in range(30)]
    line = chat_render.tool_group(names, 60)
    assert len(line) <= 59 and line.endswith("tool_29  (30)") and "…, " in line and "tool_00" not in line


def test_successful_tools_in_a_row_collapse_into_one_rewritten_line(monkeypatch):
    """#331: on a colour terminal a run of successful tools is ONE line, rewritten in place; a failure or any other
    output starts a new line. Pipes keep one line per tool (checked above)."""
    tty = io.StringIO()
    tty.isatty = lambda: True
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(chat_cli.shutil, "get_terminal_size", lambda *_: os.terminal_size((80, 24)))
    s = ChatSession(None, None, tty)
    for n in ("get_time", "web_search", "open_page"):
        s._tool_line({"name": n, "ok": True}, False)
    s._tool_line({"name": "send_mail", "ok": False, "code": "TOOL_DECLINED"}, False)
    s.say("answer")
    s._tool_line({"name": "get_time", "ok": True}, False)
    out = tty.getvalue()
    assert out.count(chat_cli.UP_ERASE) == 2                        # two rewrites: 1 -> 2 -> 3 tools
    assert "✓ get_time, web_search, open_page  (3)" in out
    assert "✗ send_mail  not approved [TOOL_DECLINED]" in out
    assert out.endswith("answer\n\x1b[2m  ✓ get_time\x1b[0m\n")       # after other output: a fresh line


def test_a_tool_line_after_streamed_text_starts_on_a_new_line():
    out = io.StringIO()
    s = ChatSession(None, None, out)
    s.say("partial", end="")
    s._tool_line({"name": "get_time", "ok": True}, True)
    s.say("done\n", end="")
    s._tool_line({"name": "get_time", "ok": True}, True)
    assert out.getvalue() == "partial\n  > get_time  ok\ndone\n  > get_time  ok\n"
