"""Approval popup UX (owner feedback 20 Oct: "where is the button for approve?", "too much text", "not beautiful").

The real Tk window is built with the test harness (`hold`: a mapped, invisible window that never answers) and its
geometry is checked: both buttons lie fully inside the window and on the screen, the window is inside the work
area and at most 60% of the screen high - also for very long titles / prompts / reasons / details, with Details
open, for typed-code requests, in Hebrew, and at 150% / 200% scaling. The pure layout helpers run everywhere; on a
machine without a desktop (headless Linux CI) the window part asserts that the popup is honestly not offered.
"""
from __future__ import annotations

import gc
import json

import pytest

from jarves import approval_popup as ap
from jarves import approvals as appr

LONG = ("Run a PowerShell maintenance script that cleans the temporary folders of every user profile, rebuilds the "
        "search index and then restarts the Windows Search service so that the start menu search works again")
BASE = {"id": "t1", "kind": "shell", "risk": "medium", "timeout_s": 60, "title": "whoami in the sandbox folder",
        "prompt": "JARVES wants to run a command on this PC.", "why": "Test only.",
        "details": {"command": "whoami", "cwd": "sandbox"}}
LONG_REQ = {**BASE, "title": LONG * 2, "prompt": LONG * 3, "why": LONG * 6,
            "details": {"command": "powershell -File C:\\x\\" + "y" * 900, "cwd": "C:\\" + "deep\\" * 80,
                        "reasons": ["a", "b"], "purpose": LONG * 4}}


# ---------------------------------------------------------------------- pure helpers
def test_shorten_keeps_one_short_sentence():
    assert ap.shorten("  a   b  ") == "a b"
    s = ap.shorten(LONG)
    assert len(s) <= ap.SENTENCE_MAX and s.endswith("…") and "  " not in s
    assert ap.shorten("x" * 300).endswith("…") and len(ap.shorten("x" * 300)) <= ap.SENTENCE_MAX


def test_english_by_default_hebrew_only_when_asked():
    assert ap.pick_lang({}) == "en" and ap.pick_lang({"lang": "en"}) == "en"
    assert ap.pick_lang({"lang": "he"}) == "he" and ap.pick_lang({"lang": "HE-il"}) == "he"
    assert ap.headline({"kind": "shell"}, "en") == "Run a command?"
    assert ap.headline({"kind": "nope"}, "en") == "Allow this?"
    he = ap.headline({"kind": "shell"}, "he")
    assert ap.is_rtl(he) and not ap.is_rtl(ap.headline({"kind": "shell"}, "en"))
    # never both languages: no Hebrew in any English string and no "/ English" pairs
    en = json.dumps(ap.STRINGS["en"], ensure_ascii=False)
    assert not ap.is_rtl(en) and " / " not in en
    assert set(ap.STRINGS["en"]) == set(ap.STRINGS["he"])


def test_popup_language_setting_reaches_the_request(tmp_path):
    from jarves.config import Config
    assert Config().ui.popup_language == "en"
    assert Config.model_validate({"ui": {"popup_language": "he"}}).ui.popup_language == "he"
    with pytest.raises(Exception):
        Config.model_validate({"ui": {"popup_language": "fr"}})
    assert appr.PopupChannel(appr.ApprovalService(tmp_path)).lang == "en"


def test_details_hold_everything_the_card_leaves_out():
    rows = ap.detail_rows(LONG_REQ, "en")
    labels = [r[0] for r in rows]
    assert "Command" in labels and "Folder" in labels and "Why (JARVES's words, not verified)" in labels
    assert any(r[2] == "mono" for r in rows)                      # commands / paths in a monospace font
    assert ap.sentence(LONG_REQ) != LONG_REQ["title"]             # the card shows a short line, details the rest
    assert any(LONG_REQ["title"][:200] in r[1] for r in rows)
    assert ap.detail_rows({"title": "short"}, "en") == []         # nothing to hide: no Details toggle


@pytest.mark.parametrize("where", ["bottom-right", "center"])
def test_place_keeps_the_whole_window_in_the_work_area(where):
    work = (0, 0, 1920, 1040)                                     # taskbar below 1040
    frame = (8, 31, 8, 8)
    for size in ((420, 300), (420, 1000), (2000, 300), (840, 720)):
        x, y = ap.place(work, size, frame, margin=16, where=where)
        ow, oh = size[0] + 16, size[1] + 39
        if ow <= 1920 and oh <= 1040:
            assert ap.inside((x, y, ow, oh), (0, 0, 1920, 1040)), (size, x, y)
        assert x >= 0 and y >= 0
    x, y = ap.place((100, 50, 1380, 770), (420, 300), frame)      # a work area that does not start at 0,0
    assert 100 <= x and 50 <= y and x + 436 <= 1380 and y + 339 <= 770


def test_inside():
    assert ap.inside((10, 10, 5, 5), (0, 0, 20, 20))
    assert not ap.inside((10, 10, 15, 5), (0, 0, 20, 20))
    assert not ap.inside((10, 10, 0, 5), (0, 0, 20, 20))         # an unmapped (0-size) button is not "inside"


# ---------------------------------------------------------------------- the real window
CASES = [
    ("short", BASE, {}, False),
    ("long", LONG_REQ, {}, False),
    ("long_details_open", LONG_REQ, {}, True),
    ("dangerous", {**LONG_REQ, "risk": "high", "typed_code": "4821"}, {}, True),
    ("hebrew", {**LONG_REQ, "lang": "he", "risk": "high", "typed_code": "2468", "title": "למחוק " * 60}, {}, True),
    ("light", LONG_REQ, {"theme": "light"}, True),
    ("dark_150", LONG_REQ, {"theme": "dark", "scale": 1.5}, True),
    ("dangerous_200", {**LONG_REQ, "risk": "high", "typed_code": "1357"}, {"scale": 2.0}, True),
]


def _window(req, tmp_path, **kw):
    return ap.build(req, tmp_path / "out.json", "hold", **kw)


@pytest.mark.parametrize("name,req,kw,open_details", CASES, ids=[c[0] for c in CASES])
def test_buttons_always_inside_window_and_screen(tmp_path, name, req, kw, open_details):
    ok, why = appr.desktop_available()
    if not ok:                                   # headless CI: the popup is honestly not offered (no skip)
        assert why
        return
    root = _window(req, tmp_path, **kw)
    try:
        if open_details:
            root.jarves.toggle_details()
        root.update()
        rep = ap.layout_report(root)
        ok_ = dict(rep["ok"])
        if not root.winfo_viewable():            # CI runners have no interactive desktop: never mapped
            ok_.pop("buttons_viewable")
        assert all(ok_.values()), (name, rep)
        a, d = rep["approve"], rep["deny"]
        assert a[1] == d[1] and a[3] == d[3]                      # one row, same size
        assert a[1] + a[3] <= rep["window"][1] + rep["window"][3]  # anchored at the bottom, never clipped
        # Hebrew: primary on the left (reading end); English: primary on the right
        assert (a[0] < d[0]) == (root.jarves.lang == "he")
        assert not (tmp_path / "out.json").exists()               # hold never answers
    finally:
        root.destroy()


def test_typed_code_gates_approve_and_details_toggle(tmp_path):
    ok, why = appr.desktop_available()
    if not ok:
        assert why
        return
    out = tmp_path / "out.json"
    root = ap.build({**BASE, "risk": "high", "typed_code": "4821"}, out, "hold")
    try:
        j = root.jarves
        j.arm()
        assert j.entry is not None and not j.approve.enabled
        j.typed.set("0000")
        j.approve.invoke()
        assert not out.exists() and not j.approve.enabled
        j.typed.set("4821")
        assert j.approve.enabled
        assert not j.details.winfo_manager()                      # Details collapsed by default (not managed)
        j.toggle_details()
        root.update_idletasks()
        assert j.details.winfo_manager() == "pack" and j.details.pack_info()   # shown; no visible desktop needed
        j.toggle_details()
        root.update_idletasks()
        assert not j.details.winfo_manager()
        j.approve.invoke()                                        # writes the result, closes the window
    except Exception:
        root.destroy()
        raise
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d == {"id": "t1", "decision": "approve", "typed": "4821"}


def test_no_code_field_for_normal_requests_and_approve_waits_for_the_delay(tmp_path):
    ok, why = appr.desktop_available()
    if not ok:
        assert why
        return
    out = tmp_path / "out.json"
    root = ap.build(BASE, out, "hold")
    try:
        j = root.jarves
        assert j.entry is None and not j.approve.enabled          # armed only after APPROVE_DELAY_MS
        j.approve.invoke()
        assert not out.exists()
        j.arm()
        assert j.approve.enabled
        j.deny.invoke()
    except Exception:
        root.destroy()
        raise
    assert json.loads(out.read_text(encoding="utf-8"))["decision"] == "deny"


# ---------------------------------------------------------------------- calm palette: WCAG AA contrast
def _lum(h):
    h = h.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _ratio(a, b):
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("risk", ["low", "medium", "high"])
def test_palette_contrast_is_wcag_aa(theme, risk):
    P = ap.PALETTES[theme]
    cb, cf = P["chip"][risk]
    assert _ratio(cf, cb) >= 4.5, "risk chip"
    for fg in ("text", "muted", "link"):
        assert _ratio(P[fg], P["bg"]) >= 4.5, fg
    assert _ratio(P["text"], P["surface"]) >= 4.5 and _ratio(P["muted"], P["surface"]) >= 4.5
    prim = "danger" if risk == "high" else "primary"
    for state in (prim, prim + "_hover"):
        assert _ratio(P["on_primary"], P[state]) >= 4.5, state
    for state in ("secondary", "secondary_hover"):
        assert _ratio(P["on_secondary"], P[state]) >= 4.5, state
    assert _ratio(P["focus"], P["bg"]) >= 3.0
    if risk == "high":
        assert _ratio(P["chip"]["high"][1], P["bg"]) >= 4.5  # the typed-code hint text


@pytest.fixture(autouse=True)
def _tk_objects_die_on_the_main_thread():
    """Tk variables garbage-collected on another thread print 'main thread is not in main loop'; collect here."""
    yield
    gc.collect()
