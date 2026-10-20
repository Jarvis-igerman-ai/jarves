"""SW-V3 P0.2b (#17): server-supplied detail text has a Hebrew sibling (`<field>_he`), English stays the fallback.

Every registered feature (description, platform evidence and blockers), every editable setting, every voice,
every usage meter text and the rule-based issue titles must have a Hebrew text; user content is never translated."""
from __future__ import annotations

import ast
import importlib
import json
import re
from pathlib import Path

import pytest

from jarves import capabilities, config_edit, dashboard, features, hebrew, identity, usage
from jarves.config import Config
from jarves.skills.settings import LIVE_VOICES

from test_dashboard import dash, get, req  # noqa: F401  (fixture + helpers)

HEBREW = re.compile(r"[֐-׿]")
SRC = Path(__file__).resolve().parents[1] / "src" / "jarves"
STATIC = SRC / "dashboard_static"


@pytest.fixture(scope="module", autouse=True)
def all_plugins():
    for name in dashboard.PLUGIN_MODULES:
        importlib.import_module(name)


def is_he(text) -> bool:
    return isinstance(text, str) and bool(HEBREW.search(text))


def test_every_registered_feature_has_a_hebrew_description_evidence_and_blocker():
    missing = []
    assert len(features.CATALOG) >= 30
    for fid, f in features.CATALOG.items():
        if f.about and not is_he(hebrew.he(f.about)):
            missing.append(f"{fid}: about {f.about!r}")
        for name, t in capabilities.view(fid)["targets"].items():
            for fld in ("evidence", "blocker"):
                if t[fld] and not is_he(hebrew.he(t[fld])):
                    missing.append(f"{fid}/{name}: {fld} {t[fld]!r}")
    assert not missing, "no Hebrew text for:\n" + "\n".join(missing)


def test_wake_phrase_capability_texts_have_hebrew():
    cap = identity._wake_capability()
    for group in (cap["targets"], cap["custom"]):
        for t in group.values():
            for fld in ("evidence", "blocker"):
                if t.get(fld):
                    assert is_he(t.get(fld + "_he")), t[fld]
    assert "evidence_he" not in capabilities.WAKE_PHRASE["targets"]["windows"]      # the shared constant is untouched


def test_feature_status_carries_hebrew_siblings_and_keeps_english(home):
    f = features.Features(Config(), home)
    for fid in features.CATALOG:
        s = f.status(fid)
        assert s["about"] == features.CATALOG[fid].about                              # English unchanged
        assert is_he(s["about_he"]) or not s["about"]
        for t in s["targets"].values():
            assert not t["blocker"] or is_he(t["blocker_he"])
            assert not t["evidence"] or is_he(t["evidence_he"])
    off = f.status("music")
    assert off["reason"] == "" or is_he(off.get("reason_he")) or "reason_he" not in off


def test_fixed_probe_reasons_have_hebrew(home):
    f = features.Features(Config(), home)
    seen = {f.status(fid)["reason"] for fid in features.CATALOG}
    for why in filter(None, seen):
        s = next(x for x in (f.status(i) for i in features.CATALOG) if x["reason"] == why)
        # environment-dependent details (camera driver messages, component details) may stay English; the fixed ones
        # must be translated
        if why in hebrew.TEXT or why.startswith("Google"):
            assert is_he(s["reason_he"]), why


def test_google_reason_keeps_the_scope_and_drops_the_instruction_for_the_model():
    t = hebrew.he('Google account not connected (calendar); tell the user: "תריץ במחשב jarvis google login"')
    assert is_he(t) and "tell the user" not in t and "יומן" in t
    t = hebrew.he("Google consent required for contacts: run `jarvis google login --contacts` and approve the "
                  "requested permission in the browser. No sign-in or permission change was performed.")
    assert "`jarvis google login --contacts`" in t and is_he(t)


def test_every_editable_setting_has_a_hebrew_group_and_label():
    for key, (group, label) in config_edit.EDITABLE.items():
        assert is_he(hebrew.he(group)), (key, group)
        assert is_he(hebrew.he(label)), (key, label)


def test_settings_view_adds_hebrew(home):
    rows = config_edit.view(home / "config.toml", Config())
    assert len(rows) == len(config_edit.EDITABLE)
    for r in rows:
        assert is_he(r["label_he"]) and is_he(r["group_he"]) and HEBREW.search(r["label_he"])
        assert r["label"] == config_edit.EDITABLE[r["key"]][1]


def test_every_voice_description_has_hebrew():
    for name, about in {**LIVE_VOICES, **identity.REALTIME_VOICES}.items():
        assert is_he(hebrew.he(about)), (name, about)
    assert is_he(hebrew.he(identity.PREVIEW_NOTE))
    rows = identity.voice_catalog(Config())
    assert rows and all(is_he(r["about_he"]) and is_he(r["languages_he"]) and is_he(r["preview_note_he"]) for r in rows)


def _meter_strings() -> list[str]:
    """Every fixed (non f-string) title / metric / source / window / limit_source / note literal of usage.Meter(...)."""
    tree = ast.parse((SRC / "usage.py").read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Meter":
            vals = [a for i, a in enumerate(node.args) if i in (2, 3, 6)]
            vals += [k.value for k in node.keywords if k.arg in ("note", "window", "limit_source")]
            out += [v.value for v in vals if isinstance(v, ast.Constant) and isinstance(v.value, str)]
    return out


def test_usage_meter_texts_have_hebrew(home):
    texts = _meter_strings()
    assert len(texts) > 20
    for t in texts:
        assert is_he(hebrew.he(t)), t
    ms = usage.Monitor(home, Config()).meters()
    for m in ms:
        d = m.to_dict()
        for fld in ("title", "metric", "source", "note"):
            if d[fld]:
                assert is_he(d.get(fld + "_he")), (m.id, fld, d[fld])
        if d["window"]:
            assert is_he(d["window_he"]), d["window"]
    assert is_he(hebrew.he("5 min")) and "5" in hebrew.he("5 min")
    assert "requests" in hebrew.he("API rate limit (requests, realtime)")
    snap = usage.Monitor(home, Config()).snapshot()
    assert len(snap["notes_he"]) == len(snap["notes"]) and all(is_he(n) for n in snap["notes_he"])


def test_usage_notes_that_are_joined_strings_are_in_the_catalog():
    # the two notes built from several literals / an f-string
    admin = "estimate of this install only; your OpenAI bill is the truth. Add a read-only OPENAI_ADMIN_KEY for the real organization cost."
    assert usage.ADMIN_ENV == "OPENAI_ADMIN_KEY" and is_he(hebrew.he(admin))
    stale = ("daily buckets; today may lag. The project budget set on platform.openai.com is not readable by API"
             " (last refresh failed)")
    assert is_he(hebrew.he(stale))


def test_backup_guidance_has_hebrew():
    from jarves import backup
    assert len(backup.GUIDANCE) == 5
    for g in backup.GUIDANCE:
        assert is_he(hebrew.he(g)), g


def test_issue_titles_and_details_from_the_rules_have_hebrew_and_keep_user_text():
    cases = [
        "Request stalled 45 s (answered late)",
        "Dead air while waiting: 12 s of silence",
        "Dead air before replies: 3x over 6 s (worst 9.5 s)",
        'Confusing filler "בסדר" said 4x - the user asked what it means',
        "Tool play_music failed 2x", "Tool gmail_search never returned (1x)", "Slow tool web_search: 14.2 s",
        "Tool set_volume called 3x within 30 s", "Slow backend response: 22.0 s", "Backend response failed 2x",
        "Model kept answering while the user was talking (7 audio chunks dropped)",
        "Expensive conversation: ~$1.40 (estimate)", "High cost on 2026-10-18: ~$3.20 (estimate)",
        "The user did not understand JARVES", "The user had to repeat himself",
        "False wake: conversation opened but nobody spoke", "Daily AI limit reached mid-conversation",
    ]
    for c in cases:
        assert is_he(hebrew.he(c)), c
    t = hebrew.he('Confusing filler "בסדר" said 4x')
    assert '"בסדר"' in t and "4" in t
    d = hebrew.he('"תנגן משהו שקט" got only progress fillers (3) for 45 s before answer audio began '
                  '(or its first recorded speech bucket for older logs).')
    assert "תנגן משהו שקט" in d and is_he(d)
    d = hebrew.he('JARVES answers actions with a bare "סבבה". Users do not understand it as "done"; say what was done '
                  'instead ("הגברתי לשבעים").')
    assert "סבבה" in d and "הגברתי לשבעים" in d
    for c in ("The user did not understand JARVES",):
        assert hebrew.he(c) != c


def test_unknown_and_user_texts_are_not_translated():
    assert hebrew.he("AI review: something the model wrote") is None            # model-written text stays as it is
    assert hebrew.he("VOICE_NO_SPEECH: no speech") is None                      # log messages stay as they are
    assert hebrew.he("") is None and hebrew.he(None) is None
    row = hebrew.issue({"title": "AI review: x", "detail": "free text"})
    assert "title_he" not in row and "detail_he" not in row


def test_fixed_issue_details_in_diagnose_are_covered():
    """Every fixed detail literal passed to Finder.issue(...) has a Hebrew text (f-strings go through the rules)."""
    tree = ast.parse((SRC / "diagnose.py").read_text(encoding="utf-8"))
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "issue" and len(node.args) >= 7:
            for a in (node.args[5], node.args[6]):
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    n += 1
                    assert is_he(hebrew.he(a.value)), a.value
    assert n >= 8


def test_dashboard_api_adds_hebrew_fields(dash):
    r, body = req(dash, "GET", "/api/features")
    d = json.loads(body)
    assert is_he(d["note_he"])
    assert all(is_he(f["about_he"]) for f in d["features"] if f["about"])
    r, body = req(dash, "GET", "/api/settings")
    d = json.loads(body)
    assert is_he(d["hint_he"]) and all(is_he(e["label_he"]) for e in d["editable"])
    r, body = req(dash, "GET", "/api/issues?status=all")
    issues = json.loads(body)
    assert issues and all(i["title"] for i in issues)
    translated = [i for i in issues if "title_he" in i]
    assert translated and all(is_he(i["title_he"]) for i in translated)
    r, body = req(dash, "GET", "/api/usage")
    d = json.loads(body)
    assert all(is_he(m["title_he"]) for m in d["meters"])
    r, body = req(dash, "GET", "/api/backup")
    d = json.loads(body)
    assert len(d["guidance_he"]) == len(d["guidance"]) and all(is_he(g) for g in d["guidance_he"])
    r, body = req(dash, "GET", "/api/guides")
    d = json.loads(body)
    assert d["guides"]


def test_guide_state_has_hebrew_reasons(home):
    from jarves import guides
    f = features.Features(Config(), home)
    seen = 0
    for g in guides.load().values():
        s = guides.state(g, f)
        if s["state"] in ("off", "setup", "partly") and s["reason"]:
            seen += 1
            assert "reason_he" in s
    assert seen
    off = guides.state(guides.load()["greeting"], f) if "greeting" in guides.load() else None
    if off and off["state"] == "off":
        assert off["reason_he"] == "כבוי"


def test_dashboard_js_picks_the_hebrew_fields_with_an_english_fallback():
    i18n = (STATIC / "i18n.js").read_text(encoding="utf-8")
    assert "window.txh" in i18n and '"_he"' in i18n
    expect = {"app.js": ['txh(f, "about")', 'txh(i, "title")', 'txh(i, "detail")', 'txh(d, "hint")'],
              "m4.js": ['txh(f, "about")', 'txh(w, "blocker")', 'txh(v, "about")'],
              "pages.js": ['txh(e, "label")', 'txh(list[0], "group")'],
              "usage.js": ['txh(m, "note")', 'txh(m, "title")', 'd["guidance_" + JL.lang]'],
              "setup.js": ['txh(f, "about")', 'txh(s, "reason")'],
              "guides.js": ['txh(t, "blocker")', 'txh(g, "reason")'],
              "m2.js": ['txh(t, "evidence")'], "home.js": ['txh(f, "reason")']}
    for js, needles in expect.items():
        src = (STATIC / js).read_text(encoding="utf-8")
        for n in needles:
            assert n in src, (js, n)
        assert ".innerHTML" not in src
    assert ".innerHTML" not in i18n
