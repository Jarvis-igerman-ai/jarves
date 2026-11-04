"""Terminal rendering for `jarvis chat` (#331): light Markdown while the answer streams, compact tool lines and the
one-line status. Standard library only.

Rules (also for #329): text is never reversed or reordered; ANSI styling is added only on a real colour terminal
(the caller decides) and never inside a line that contains Hebrew, so right-to-left text is not split by escape
codes. Without colour every function returns the text unchanged, so pipes and logs keep the raw Markdown.
"""
from __future__ import annotations

from . import clilang

RESET = "\x1b[0m"
BOLD = "1"
CODE = "36"


def T(he: str, en: str) -> str:
    return clilang.fmt(he, en)


def _hebrew(ch: str) -> bool:
    return "֐" <= ch <= "׿"


class MarkdownStream:
    """Feed streamed text chunk by chunk; get terminal text back at once (only a line-start marker or a single `*`
    is held until the next character decides it). Handles: # headings (bold), **bold**, `code`, - / * bullets (as
    a dot) and ``` fenced blocks (fence lines dropped, code in cyan)."""

    def __init__(self, color: bool) -> None:
        self.color = color
        self.in_fence = False
        self._line_reset()

    def _line_reset(self) -> None:
        self.at_start = True
        self.held = ""
        self.skip_line = False
        self.star = False
        self.bold = self.code = self.heading = False
        self.hebrew = False
        self.painted = ""                         # the escape codes currently in effect

    # ---------------------------------------------------------------- styling
    def _codes(self) -> str:
        c = []
        if self.bold or self.heading:
            c.append(BOLD)
        if self.code or self.in_fence:
            c.append(CODE)
        return ";".join(c)

    def _put(self, ch: str, out: list[str]) -> None:
        """Emit one visible character; styles change lazily here, so no empty escape pair is ever written."""
        if not self.hebrew:
            want = self._codes()
            if want != self.painted:
                if self.painted:
                    out.append(RESET)
                if want:
                    out.append(f"\x1b[{want}m")
                self.painted = want
        out.append(ch)

    def _end_line(self, out: list[str]) -> None:
        if self.painted:
            out.append(RESET)
            self.painted = ""

    # ---------------------------------------------------------------- characters
    def _inline(self, ch: str, out: list[str]) -> None:
        if _hebrew(ch) and not self.hebrew:
            self._end_line(out)                       # #329: no escape codes inside a right-to-left line
            self.hebrew = True
        if self.in_fence:
            self._put(ch, out)
            return
        if self.star:
            self.star = False
            if ch == "*":
                self.bold = not self.bold
                return
            self._put("*", out)
        if ch == "*":
            self.star = True
            return
        if ch == "`":
            self.code = not self.code
            return
        self._put(ch, out)

    def _start(self, ch: str, out: list[str]) -> None:
        """Classify the beginning of a line: indent, heading, bullet or fence."""
        self.held += ch
        s = self.held
        t = s.lstrip(" ")
        indent = s[:len(s) - len(t)]
        if t == "" or (len(t) < 3 and "```".startswith(t)):
            return                                    # still ambiguous
        if t.startswith("```"):
            self.skip_line = True                     # the fence line (and its language word) is not shown
            self.held = ""
            return
        if self.in_fence:
            self.at_start, self.held = False, ""
            out.append(indent)
            for c in t:
                self._inline(c, out)
            return
        if t.strip("#") == "" and len(t) <= 6:
            return                                    # "#", "##" ... wait for the space
        if t in ("-", "*"):
            return
        self.at_start, self.held = False, ""
        out.append(indent)
        head = len(t) - len(t.lstrip("#"))
        if 1 <= head <= 6 and t[head:head + 1] == " ":
            self.heading = True
            rest = t[head + 1:]
        elif t[:2] in ("- ", "* "):
            out.append("• ")
            rest = t[2:]
        else:
            rest = t
        for c in rest:
            self._inline(c, out)

    def feed(self, text: str) -> str:
        if not self.color:
            return text
        out: list[str] = []
        for ch in text:
            if ch == "\n":
                if self.skip_line:
                    self.in_fence = not self.in_fence
                    self._line_reset()
                    continue
                self._flush_line(out)
                out.append("\n")
                self._line_reset()
            elif self.skip_line:
                continue
            elif self.at_start:
                self._start(ch, out)
            else:
                self._inline(ch, out)
        return "".join(out)

    def _flush_line(self, out: list[str]) -> None:
        if self.held:
            held, self.held, self.at_start = self.held, "", False
            out.append(held)                          # a lone "#" or "-" was plain text after all
        if self.star:
            self.star = False
            out.append("*")
        self._end_line(out)

    def flush(self) -> str:
        """End of the answer: release anything held and close open styles."""
        if not self.color:
            return ""
        out: list[str] = []
        if self.skip_line:
            self.in_fence = not self.in_fence
        else:
            self._flush_line(out)
        self._line_reset()
        self.in_fence = False
        return "".join(out)


def render(text: str, color: bool) -> str:
    """A whole (non-streamed) answer."""
    md = MarkdownStream(color)
    return md.feed(text) + md.flush()


# ---------------------------------------------------------------------- tool lines
REASONS = {
    "TOOL_UNKNOWN": ("כלי לא מוכר", "unknown tool"),
    "TOOL_DECLINED": ("לא אושר", "not approved"),
    "TOOL_APPROVAL_TIMEOUT": ("לא נענה בזמן", "approval timed out"),
    "TOOL_APPROVAL_UNAVAILABLE": ("אין דרך לאשר", "no way to approve"),
    "TOOL_FORBIDDEN": ("אסור לפי הכללים", "not allowed"),
    "TOOL_TIMEOUT": ("לקח יותר מדי זמן", "timed out"),
    "TOOL_CANCELLED": ("בוטל", "cancelled"),
    "TOOL_BAD_ARGS": ("פרטים חסרים או שגויים", "bad arguments"),
    "TOOL_REFUSED": ("סורב", "refused"),
    "TOOL_NOT_CONFIRMED": ("לא אושר סופית", "not confirmed"),
}


def reason(code: str) -> str:
    he_en = REASONS.get(code)
    return T(*he_en) if he_en else ""


def tool_group(names: list[str], width: int = 80) -> str:
    """Consecutive successful tools of one answer as ONE line (#331, like Claude Code collapsing its tool calls):
    "  ✓ get_time" for one, "  ✓ a, b, c  (3)" for more. The newest names are kept, older ones become "…", and the
    line fits `width` so a terminal never wraps it (the caller rewrites it in place). No words, so no Hebrew (#329)."""
    room = max(20, width - 1)
    if len(names) == 1:
        return f"  ✓ {names[0]}"[:room]
    tail = f"  ({len(names)})"
    shown: list[str] = []
    for n in reversed(names):                          # newest first, then restore the order
        if shown and 4 + len(", ".join([n] + shown)) + 3 + len(tail) > room:
            break
        shown.insert(0, n)
    more = "…, " if len(shown) < len(names) else ""
    return ("  ✓ " + more + ", ".join(shown))[:room - len(tail)] + tail


def status_line(mode: str, voice: bool, model: str) -> str:
    """One quiet line like Claude Code's footer: session mode, voice and the text model."""
    parts = [mode, T("קול: ", "voice ") + (T("פועל", "on") if voice else T("כבוי", "off"))]
    if model:
        parts.append(str(model)[:40])
    return " · ".join(parts)
