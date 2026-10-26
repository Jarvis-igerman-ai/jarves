"""#534: a duplicate key in a phone-app dictionary silently lets the later text win (it showed 'Waiting for the connection'
where 'Waiting for approval' was meant). Every language dictionary in the phone JS files must define each key once, and
en and he must have the same keys in app.js."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

MOBILE = Path(__file__).resolve().parents[1] / "src" / "jarves" / "dashboard_static" / "mobile"


def _strip_strings(src: str) -> str:
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if c in "\"'`":
            q, i = c, i + 1
            while i < n and src[i] != q:
                i += 2 if src[i] == "\\" else 1
            i += 1
            out.append('""')
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _dicts(src: str) -> list[tuple[str, list[str]]]:
    """(language, top-level keys in order) for every `en: {...}` / `he: {...}` object literal."""
    code = _strip_strings(src)
    found = []
    for m in re.finditer(r"\b(en|he)\s*:\s*\{", code):
        depth, i, start = 1, m.end(), m.end()
        while i < len(code) and depth:
            depth += {"{": 1, "}": -1}.get(code[i], 0)
            i += 1
        body = code[start:i - 1]
        d = 0
        buf = ""
        for ch in body:
            if ch in "{[(":
                d += 1
            elif ch in "}])":
                d -= 1
            if d == 0:
                buf += ch
        top = re.findall(r"(?:^|,)\s*([A-Za-z_]\w*)\s*:", buf)
        found.append((m.group(1), top))
    return found


@pytest.mark.parametrize("name", sorted(p.name for p in MOBILE.glob("*.js")))
def test_no_duplicate_keys_in_a_language_dictionary(name):
    for lang, keys in _dicts((MOBILE / name).read_text(encoding="utf-8")):
        dup = sorted({k for k in keys if keys.count(k) > 1})
        assert not dup, f"{name} [{lang}] defines these keys twice: {dup}"


def test_app_js_english_and_hebrew_have_the_same_keys():
    d = dict(_dicts((MOBILE / "app.js").read_text(encoding="utf-8")))
    assert d["en"] and set(d["en"]) == set(d["he"]), sorted(set(d["en"]) ^ set(d["he"]))
