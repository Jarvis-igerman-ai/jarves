"""SW-V3 P0.2c (issue #17): the dashboard is multi-language with ENGLISH as the default.

Catalogs live in dashboard_static/locales/<code>.js (strict JSON after the "="). en.js lists every source key;
Hebrew must have all of them (a missing key fails), the other locales may fall back to English and their coverage
is reported. Also: registry order, dir per locale, default English, the language picker, and the server locale
extension point (locales.py)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from test_dashboard import dash, get, req  # noqa: F401  (fixture + helpers)

STATIC = Path(__file__).resolve().parents[1] / "src" / "jarves" / "dashboard_static"
LOCALES = STATIC / "locales"
RTL = {"he"}
COMPLETE = {"he"}                   # locales that must cover every key (others report coverage)


def catalog(code: str) -> dict:
    src = (LOCALES / f"{code}.js").read_text(encoding="utf-8").replace("\r\n", "\n")   # CRLF checkouts
    head, body = src.split("=\n", 1)
    assert f"(window.JARVES_CATALOGS = window.JARVES_CATALOGS || {{}}).{code}" in head
    return json.loads(body.strip().rstrip(";"))


def codes() -> list[str]:
    return sorted(p.stem for p in LOCALES.glob("*.js"))


def test_owner_languages_are_english_and_hebrew_only():
    """SW-V4 K (owner decision): English (default) and Hebrew only; the multi-locale mechanism stays generic.
    A saved choice that is no longer registered (ar / ru from V3) falls back to English."""
    assert codes() == ["en", "he"]
    js = (STATIC / "i18n.js").read_text(encoding="utf-8")
    assert "if (s && LOC[s]) lang = s;" in js and "lang = LOC[l] ? l : DEFAULT;" in js
    assert "forced && LOC[forced]" in js
    for f in list(STATIC.glob("*.js")) + list(STATIC.glob("*.css")) + [STATIC / "index.html"]:
        src = f.read_text(encoding="utf-8")
        assert not re.search(r'lang="(?:ar|ru)"|locales/(?:ar|ru)\.js|Noto Sans Arabic', src), f.name


def test_every_catalog_is_registered_in_order_before_i18n():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    tags = re.findall(r'<script src="/static/locales/([a-z]{2,3})\.js" defer></script>', html)
    assert sorted(tags) == codes(), "every catalog file needs its <script> tag (the registry) and vice versa"
    assert tags[0] == "en"
    last = max(html.index(f"/static/locales/{c}.js") for c in tags)
    assert last < html.index("/static/i18n.js") < html.index("/static/app.js")


@pytest.mark.parametrize("code", codes())
def test_catalog_meta_and_direction(code):
    c = catalog(code)
    assert c["code"] == code and c["name"] and c["bcp47"]
    assert c["dir"] == ("rtl" if code in RTL else "ltr")
    for k, v in c["dict"].items():
        assert isinstance(v, str) and v, (code, k)
    for pat, to in c.get("rules", []):
        re.compile(pat)                                   # the same simple syntax in JS and Python
        groups = re.compile(pat).groups
        assert all(int(n) <= groups for n in re.findall(r"\$(\d)", to)), (code, pat)


def test_english_is_the_identity_source():
    en = catalog("en")["dict"]
    assert len(en) > 700
    assert all(k == v for k, v in en.items()), "en.js values are the English wording of the key"


@pytest.mark.parametrize("code", [c for c in codes() if c != "en"])
def test_locale_covers_the_english_keys(code):
    en, d = catalog("en")["dict"], catalog(code)["dict"]
    extra = sorted(set(d) - set(en))
    assert not extra, f"{code}: keys that are not English source texts (typo or stale): {extra[:10]}"
    missing = [k for k in en if k not in d]
    coverage = 100 * (len(en) - len(missing)) / len(en)
    print(f"\n{code}: {len(en) - len(missing)}/{len(en)} keys ({coverage:.0f} %)")
    if code in COMPLETE:
        assert not missing, f"{code} is missing {len(missing)} keys, e.g. {missing[:10]}"
    else:
        assert coverage >= 20, f"{code} coverage {coverage:.0f} %: shell, navigation and home at least"
        for k in ("Home", "Settings", "Conversations", "Language", "Navigation", "Unknown"):
            assert k in d, (code, k)


def test_rtl_catalogs_mirror_arrows():
    """Catalog texts are written for their own direction: "Open →" points backwards in RTL, so it is "←"."""
    for code in RTL:
        for k, v in catalog(code)["dict"].items():
            if "→" in k and "←" not in k:
                assert "→" not in v, (code, k, v)


def _inline_pairs():
    """English literals of tx("he", "en") / L(...) / bi(...) and [he, en] tables in every dashboard script."""
    rx = re.compile(r'\b(?:tx|L|bi)\(\s*"((?:[^"\\]|\\.)*)"\s*,\s*"((?:[^"\\]|\\.)*)"')
    ax = re.compile(r'\[\s*(?:"[a-z_]+"\s*,\s*)?"([^"]*[֐-׿][^"]*)"\s*,\s*"([^"]*[A-Za-z][^"]*)"')
    for js in STATIC.glob("*.js"):
        if js.name == "i18n.js":
            continue
        src = js.read_text(encoding="utf-8")
        for he, en in rx.findall(src) + ax.findall(src):
            yield js.name, json.loads(f'"{en}"'), json.loads(f'"{he}"')


def test_every_inline_english_text_is_a_catalog_key():
    """The inline Hebrew pages write tx(he, en); the English must be a key so every other locale can translate it."""
    en = catalog("en")["dict"]
    missing = sorted({(f, e) for f, e, _ in _inline_pairs() if e and e not in en})
    assert not missing, f"add to locales/en.js and he.js: {missing[:10]}"


def test_default_is_english_and_never_the_browser_language():
    js = (STATIC / "i18n.js").read_text(encoding="utf-8")
    assert 'DEFAULT = "en"' in js
    assert "navigator.language" not in js and "navigator.languages" not in js
    assert "jarves-guide-lang" not in js                 # an old key is not an explicit choice
    assert js.count('localStorage.setItem(KEY') == 1     # only the picker (JL.set) saves a choice
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert '<html lang="en" dir="ltr">' in html
    assert 'id="langSlot"' in html and 'id="langBtn"' not in html


def test_picker_and_generic_helpers():
    js = (STATIC / "i18n.js").read_text(encoding="utf-8")
    for needle in ("picker(", 'createElement("select")', "textDir:", "pick:", "window.txh", "window.txo",
                   'o[k + "_" + lang]', "FSI + r + PDI"):
        assert needle in js, needle
    shell = (STATIC / "shell.js").read_text(encoding="utf-8")
    assert 'JL.picker("lang-pick")' in shell
    for f in ("guides.js", "setup.js", "strip.js", "shell.js", "home.js", "usage.js"):
        src = (STATIC / f).read_text(encoding="utf-8")
        assert not re.search(r'lang === "he" \? "en" : "he"', src), f       # no two-language toggles left
        assert not re.search(r'\? "rtl" : "ltr"', src), f                    # direction comes from the locale


def test_css_uses_logical_properties():
    for css in STATIC.glob("*.css"):
        src = re.sub(r"/\*.*?\*/", "", css.read_text(encoding="utf-8"), flags=re.S)
        bad = re.findall(r"(?:margin|padding|border)-(?:left|right)\s*:|(?<![-\w])(?:left|right)\s*:\s*\d|"
                         r"text-align:\s*(?:left|right)\s*;(?![^}]*rtl)|float:\s*(?:left|right)", src)
        # the only physical alignment allowed is inside html[dir="rtl"] overrides (numbers / kv cells)
        bad = [b for b in bad if not b.startswith("text-align")]
        assert not bad, (css.name, bad)
    for js in STATIC.glob("*.js"):
        assert not re.search(r"margin-(?:left|right)|padding-(?:left|right)", js.read_text(encoding="utf-8")), js.name


def test_shell_serves_the_catalogs(dash):
    r, html = req(dash, "GET", "/")
    assert r.status == 200 and b'<html lang="en" dir="ltr">' in html
    for c in codes():
        r, body = req(dash, "GET", f"/static/locales/{c}.js")
        assert r.status == 200 and b".innerHTML" not in body and b"eval(" not in body
        assert r.getheader("Content-Type").startswith("text/javascript")


# ---------------------------------------------------------------------- server locales (locales.py)
def test_server_locale_registry_is_generic(monkeypatch):
    from jarves import locales
    assert locales.DEFAULT == "en" and "he" in locales.TRANSLATORS
    text = "no microphone"
    assert locales.text("he", text) == "אין מיקרופון" and locales.text("en", text) is None
    monkeypatch.setitem(locales.TRANSLATORS, "xx", lambda s: f"[xx] {s}" if s == text else None)
    obj = locales.attach({"reason": text, "other": "user words"}, "reason", "other")
    assert obj["reason"] == text and obj["reason_he"] == "אין מיקרופון" and obj["reason_xx"] == "[xx] no microphone"
    assert "other_he" not in obj and "other_xx" not in obj      # unknown / user text gets no sibling
    assert locales.siblings("note", text) == {"note_he": "אין מיקרופון", "note_xx": "[xx] no microphone"}
    assert locales.list_siblings("notes", [text, "zzz"])["notes_he"] == ["אין מיקרופון", None]


def test_english_display_form_drops_the_spoken_hebrew_hint():
    """D-11: a reason that carries the model's Hebrew line ('tell the user: "<Hebrew>"') gets an English display
    sibling (<key>_en) with the bare command; the dashboard's txh prefers it on English pages."""
    from jarves import locales
    src = 'Google account not connected (calendar); tell the user: "תריץ במחשב jarvis google start, ואני אדריך אותך"'
    sib = locales.siblings("reason", src)
    assert sib["reason_en"] == "Google account not connected (calendar); run: jarvis google start"
    assert re.search(r"[֐-׿]", sib["reason_he"])
    assert "reason_en" not in locales.siblings("reason", "no microphone")       # plain English: no sibling
    js = (STATIC / "i18n.js").read_text(encoding="utf-8")
    assert 'window.txh = (o, k) => srv(o, k, o && (o[k + "_en"] || o[k]));' in js


def test_features_api_carries_locale_siblings(dash):
    d = get(dash, "/api/features")
    assert "note" in d and "note_he" in d


def test_timers_and_lists_pages_are_translated_once():
    """V4 P0.3: the two new pages' every English text is a catalog key with a Hebrew value (P1.3 does not redo them)."""
    en, he = catalog("en")["dict"], catalog("he")["dict"]
    src = (STATIC / "m3.js").read_text(encoding="utf-8")
    page = src[src.index("// ------------------------------------------------------------- Timers & alarms"):
               src.index("Object.assign(PAGES")]
    pairs = re.findall(r'tx\(\s*"((?:[^"\\]|\\.)*)"\s*,\s*"((?:[^"\\]|\\.)*)"', page)
    assert len(pairs) >= 30
    for hebrew, english in pairs:
        assert english in en and english in he, english
        assert re.search(r"[֐-׿]", he[english]), english
        assert re.search(r"[֐-׿]", hebrew), english                          # and the inline Hebrew is Hebrew
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for label in re.findall(r'data-page="(?:timers|lists)"><span data-he="[^"]+" data-en="([^"]+)"', html):
        assert label.replace("&amp;", "&") in en and label.replace("&amp;", "&") in he


def test_new_pages_search_keywords_in_both_languages():
    js = (STATIC / "shell.js").read_text(encoding="utf-8")
    for page in ("timers", "lists"):
        m = re.search(rf'\b{page}: "([^"]+)"', js)
        assert m and re.search(r"[A-Za-z]", m.group(1)) and re.search(r"[֐-׿]", m.group(1)), page
