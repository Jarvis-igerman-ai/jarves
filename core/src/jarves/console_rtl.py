"""Hebrew in a terminal that does no bidi (Windows conhost / Windows Terminal / VS Code terminal).

Those terminals print characters strictly left to right, so logical-order Hebrew shows reversed and mixed lines
(Hebrew + `jarvis key` + `[2]:`) fall apart. Modern bidi terminals (mintty, most Linux/macOS terminals, WSL) need
nothing. `render()` turns a logical line into the VISUAL order such a terminal must be given: every Hebrew run is
reversed, Latin/digit runs (command names, keys, paths, numbers) keep their left-to-right spelling, the order of
the runs is reversed (right-to-left paragraph) and brackets are mirrored. Long lines are wrapped BEFORE reversing.

Switch: env JARVES_CONSOLE_RTL = auto (default) | logical | visual, and `--no-rtl-fix` (same as logical).
All functions are pure (no console access) except `install()`. The graphical Setup page renders Hebrew correctly;
the wizard offers it first when the language is Hebrew."""
from __future__ import annotations

import os
import re
import shutil
import sys
import textwrap

MODES = ("auto", "logical", "visual")
ENV = "JARVES_CONSOLE_RTL"
_HEB = re.compile(r"[֐-׿]")
_HEB_RUN = re.compile(r"[֐-׿]+")
_MIRROR = str.maketrans("()[]{}<>", ")(][}{><")
# a word without Hebrew (command, key, path, number); may contain punctuation inside
_LTR_UNIT = re.compile(r"[A-Za-z0-9%~$/\\][A-Za-z0-9._:/\\@%+#~=&?$-]*")


def has_hebrew(text: str) -> bool:
    return bool(_HEB.search(text))


def _units(line: str) -> list[tuple[str, str]]:
    """Split into ('R'|'L'|'N', text): Hebrew letters, Latin/digit words, neutrals (space, punctuation)."""
    out: list[tuple[str, str]] = []
    i = 0
    while i < len(line):
        m = _HEB_RUN.match(line, i)
        if m:
            out.append(("R", m.group()))
            i = m.end()
            continue
        m = _LTR_UNIT.match(line, i)
        if m:
            s = m.group()
            while len(s) > 1 and s[-1] in ".:-=&?,":        # trailing punctuation is a neutral, not part of the word
                s = s[:-1]
            out.append(("L", s))
            i += len(s)
            continue
            continue
        if out and out[-1][0] == "N":
            out[-1] = ("N", out[-1][1] + line[i])
        else:
            out.append(("N", line[i]))
        i += 1
    return out


def to_visual(line: str) -> str:
    """One logical line (right-to-left paragraph) -> the string a left-to-right-only terminal must print."""
    if not has_hebrew(line):
        return line
    units = _units(line)
    runs: list[list] = []                       # [dir, text]
    for k, (kind, text) in enumerate(units):
        if kind == "N":
            prev = units[k - 1][0] if k else "N"
            nxt = units[k + 1][0] if k + 1 < len(units) else "N"
            # a neutral between two Latin/digit words stays with them (left to right); otherwise it is RTL
            kind = "L" if prev == "L" and nxt == "L" else "R"
        if runs and runs[-1][0] == kind:
            runs[-1][1] += text
        else:
            runs.append([kind, text])
    return "".join(t[::-1].translate(_MIRROR) if d == "R" else t for d, t in reversed(runs))


def wrap_logical(text: str, width: int) -> list[str]:
    if width <= 0 or len(text) <= width:
        return [text]
    indent = text[: len(text) - len(text.lstrip())]
    return textwrap.wrap(text, width, subsequent_indent=indent, break_long_words=False, break_on_hyphens=False,
                         drop_whitespace=True, replace_whitespace=False) or [text]


def render(text: str, width: int = 0, mode: str = "visual") -> str:
    """Convert text (may hold several lines) for `mode`. 'logical' returns it unchanged."""
    if mode != "visual" or not has_hebrew(text):
        return text
    lines: list[str] = []
    for line in text.split("\n"):
        if has_hebrew(line):
            lines.extend(to_visual(p) for p in wrap_logical(line, width))
        else:
            lines.append(line)
    return "\n".join(lines)


def detect_mode(env: dict | None = None, platform: str | None = None, isatty: bool = True) -> str:
    """'visual' only where the terminal is known not to do bidi; 'logical' everywhere else (also pipes and files)."""
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    want = (env.get(ENV) or "auto").strip().lower()
    if want in ("logical", "visual"):
        return want
    if not isatty or not platform.startswith("win"):
        return "logical"
    if env.get("TERM_PROGRAM") == "mintty" or env.get("MSYSTEM") or env.get("WSL_DISTRO_NAME"):
        return "logical"                                           # mintty (Git Bash) and WSL terminals have bidi
    return "visual"                                                # conhost, Windows Terminal, VS Code terminal


class RtlStream:
    """Wraps a text stream: lines holding Hebrew are wrapped to the terminal width and written in visual order."""

    def __init__(self, stream, width_fn=None) -> None:
        self._s = stream
        self._buf = ""
        self._width = width_fn or (lambda: shutil.get_terminal_size((100, 25)).columns - 1)

    def write(self, text: str) -> int:
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._s.write(render(line, self._width()) + "\n")
        return len(text)

    def flush(self) -> None:
        if self._buf:                                              # a prompt without newline
            core = self._buf.rstrip()                              # the cursor stays after the prompt's trailing space
            self._s.write(render(core, 0) + self._buf[len(core):])
            self._buf = ""
        self._s.flush()

    def __getattr__(self, name):
        return getattr(self._s, name)


def install(argv: list[str] | None = None) -> str:
    """Wrap sys.stdout/sys.stderr when the terminal needs it. Returns the mode used."""
    argv = sys.argv[1:] if argv is None else argv
    if "--no-rtl-fix" in argv:
        return "logical"
    mode = detect_mode(isatty=bool(getattr(sys.stdout, "isatty", lambda: False)()))
    if mode == "visual":
        for name in ("stdout", "stderr"):
            s = getattr(sys, name)
            if not isinstance(s, RtlStream):
                setattr(sys, name, RtlStream(s))
    return mode
