"""JAR-194 (GitHub #950): the workflow console Health (.hl-*) and Timeline (.tl-*) styles follow the theme.

Every .hl-* / .tl-* rule must take its colours from the theme tokens (var(--...)); a raw hex colour there
keeps the dark palette in the light theme (dark banner, unreadable layer names). Hex is allowed only inside
the :root token blocks."""
from __future__ import annotations

import re
from pathlib import Path

CSS = Path(__file__).resolve().parents[1] / "src" / "jarves" / "workflow" / "static" / "style.css"
HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
# .demo-notice is the "demo data" banner drawn at the top of the Health and Timeline views (JAR-236).
OURS = re.compile(r"\.(?:hl|tl)-|\.demo-notice\b|@keyframes\s+(?:hl|tl)-")


def _rules(text: str, outer: str = ""):
    """Yield (selector, body) for every leaf rule, with the enclosing at-rule prefixed to the selector."""
    i, n = 0, len(text)
    while i < n:
        start = text.find("{", i)
        if start < 0:
            return
        selector = text[i:start].strip()
        depth, j = 1, start + 1
        while j < n and depth:
            depth += {"{": 1, "}": -1}.get(text[j], 0)
            j += 1
        body = text[start + 1:j - 1]
        if selector.startswith("@media") or selector.startswith("@supports"):
            yield from _rules(body, selector)
        elif selector.startswith("@keyframes"):
            yield selector, body
        else:
            yield f"{outer} {selector}".strip(), body
        i = j


def _theme_rules():
    text = re.sub(r"/\*.*?\*/", "", CSS.read_text(encoding="utf-8"), flags=re.S)
    return [(sel, body) for sel, body in _rules(text) if OURS.search(sel)]


def test_health_and_timeline_rules_exist():
    selectors = " ".join(sel for sel, _ in _theme_rules())
    for name in (".hl-banner", ".hl-name", ".hl-note", ".hl-status", ".hl-age", ".hl-row", ".tl-legend", ".demo-notice"):
        assert name in selectors, name


def test_health_and_timeline_rules_use_theme_tokens_not_raw_hex():
    offenders = [f"{sel} {{ {HEX.findall(body)} }}" for sel, body in _theme_rules() if HEX.search(body)]
    assert not offenders, "raw hex colours in .hl-*/.tl-* rules:\n" + "\n".join(offenders)


def _theme_tokens():
    """Custom properties per theme block: dark default `:root`, OS-light media block, explicit light."""
    text = re.sub(r"/\*.*?\*/", "", CSS.read_text(encoding="utf-8"), flags=re.S)
    themes = {"dark": set(), "light-os": set(), "light": set()}
    for sel, body in _rules(text):
        key = {":root": "dark", '@media(prefers-color-scheme:light) :root:not([data-theme="dark"])': "light-os",
               ':root[data-theme="light"]': "light"}.get(sel)
        if key:
            themes[key] |= set(re.findall(r"(--[\w-]+)\s*:", body))
    return themes


def test_every_token_used_is_defined_in_every_theme():
    """A typo (var(--subtel)) or an undefined token silently falls back to the inherited colour."""
    used = {name for _, body in _theme_rules() for name in re.findall(r"var\((--[\w-]+)", body)}
    assert used, "no var(--...) found in .hl-*/.tl-* rules"
    for theme, defined in _theme_tokens().items():
        missing = sorted(used - defined)
        assert not missing, f"{theme} theme does not define {missing}"


def test_shadow_token_is_a_full_box_shadow():
    text = CSS.read_text(encoding="utf-8")
    for value in re.findall(r"--shadow:([^;}]+)", text):
        assert re.fullmatch(r"\s*-?\d+(px)? -?\d+(px)? \d+(px)? #[0-9a-fA-F]{3,8}\s*", value), value


def test_named_text_rules_take_a_colour_token():
    bodies = {}
    for sel, body in _theme_rules():
        for part in sel.split(","):
            bodies.setdefault(part.strip(), []).append(body)
    for name in (".hl-banner", ".hl-name", ".hl-note", ".hl-status", ".hl-age", ".tl-legend", ".demo-notice"):
        joined = ";".join(bodies.get(name, []))
        assert re.search(r"(?<![-\w])color:\s*var\(--", joined), name
    assert re.search(r"border-top:[^;]*var\(--line", ";".join(bodies[".hl-row+.hl-row"]))
