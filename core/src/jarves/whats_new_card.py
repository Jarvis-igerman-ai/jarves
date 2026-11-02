"""'What changed since your version' card on the dashboard Home (#167, V4.5.1). Beta, OFF by default.

After an update the Home page shows ONE short card with only the user-facing changes between the version the owner ran
before and the one running now (CHANGELOG.md plus changelog.d fragments, the same source as the Release notes page and
`jarvis whatsnew`). The owner dismisses it; it comes back only after the next update to a newer release.

Where "the version before" comes from: the last release this copy saw (remembered while the switch is on), otherwise
the updater's rollback record (`updates/previous/previous.json`) when it says it updated to this release. A fresh install
or the first look after switching it on shows no card. Builds of one release (4.5.1-beta.3 -> beta.4) compare equal:
their notes share one CHANGELOG section, so there is no difference to show.

Read-only towards settings, the wake model and data: it stores only {last, from, dismissed} version strings in the
memory database. Offline, stdlib only, no paid call. The setup wizard's per-topic "ask again only what changed" logic
(#152, `wizard.again_reason`) already covers the second half of #167 and is not duplicated here.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import __version__, features as feat, release_notes
from .calm import targets

FEATURE = "whats_new_card"
SETTING = "whats_new.state"
MAX_ITEMS = 8                                            # a short card; the rest is one click away (Release notes)
_DEV = re.compile(r"developer[- ]workflow|nothing changes for users", re.I)
_KIND_RANK = {"Security": 0, "Added": 1, "Fixed": 2, "Changed": 3}


def vkey(v: str) -> tuple[int, ...]:
    """'4.5.1-beta.3' / '4.5.1.349' / 'v4.5.1' -> (4, 5, 1); () when there is no x.y.z."""
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", str(v or ""))
    return tuple(int(x) for x in m.groups()) if m else ()


def _db(home: Path):
    from .userdata import open_db
    return open_db(home)


def read_state(home: Path) -> dict:
    if not (Path(home) / "memory.db").exists():
        return {}
    db = _db(home)
    try:
        v = db.setting(SETTING)
    finally:
        db.close()
    return {k: str(v[k]) for k in ("last", "from", "dismissed") if isinstance(v, dict) and v.get(k)}


def _save(home: Path, st: dict) -> None:
    db = _db(home)
    try:
        db.set_setting(SETTING, st, actor="dashboard")
    finally:
        db.close()


def _from_rollback_record(home: Path, current: str) -> str:
    """The updater's record says 'updated from X to <this release>': X, else ''."""
    try:
        from .updates import read_previous
        rec = read_previous(home) or {}
    except Exception:
        return ""
    old, to = str(rec.get("version") or ""), str(rec.get("to") or "")
    return old if vkey(to) == vkey(current) and vkey(old) and vkey(old) < vkey(current) else ""


def observe(home: Path, current: str = __version__) -> dict:
    """Remember the running release; when it is newer than the last one seen, open a card from that release.
    An earlier card that was never dismissed keeps its older start, so two quick updates show both releases."""
    st = read_state(home)
    cur = release_notes.stable_of(current)
    last = st.get("last", "")
    changed = False
    if not last:
        start = _from_rollback_record(home, cur)
        if start:
            st["from"] = release_notes.stable_of(start)
        st["last"], changed = cur, True
    elif vkey(cur) and vkey(cur) > vkey(last):
        if not st.get("from"):                           # dismiss() clears it; an open card keeps its older start
            st["from"] = last
        st["last"], changed = cur, True
    if changed:
        _save(home, st)
    return st


def changes(secs: list[dict], since: str, current: str, limit: int = MAX_ITEMS) -> dict:
    """User-facing items of every release after `since` up to and including `current`, newest release first,
    most useful kinds first inside a release. {versions, items:[{version, kind, scope, text}], more}."""
    lo, hi = vkey(since), vkey(current)
    picked = [s for s in secs if vkey(s["version"]) and lo < vkey(s["version"]) <= hi] if lo and hi else []
    picked.sort(key=lambda s: vkey(s["version"]), reverse=True)
    items: list[dict] = []
    for s in picked:
        users = [i for i in s["items"] if not _DEV.search(i["text"]) and not _DEV.search(i["scope"])]
        users.sort(key=lambda i: _KIND_RANK.get(i["kind"], 9))
        items += [{"version": s["version"], "kind": i["kind"], "scope": i["scope"], "text": _short(i["text"])}
                  for i in users]
    return {"versions": [s["version"] for s in picked], "items": items[:limit], "more": max(0, len(items) - limit)}


def _short(t: str, n: int = 160) -> str:
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= n else t[:n - 1].rsplit(" ", 1)[0] + "…"


def card(home: Path, current: str = __version__, secs: list[dict] | None = None) -> dict | None:
    """The card to show now, or None (no older release known, dismissed, or nothing user-facing changed)."""
    st = observe(home, current)
    cur = release_notes.stable_of(current)
    start = st.get("from", "")
    if not start or vkey(st.get("dismissed", "")) >= vkey(cur):
        return None
    ch = changes(release_notes.sections() if secs is None else secs, start, cur)
    if not ch["items"]:
        return None
    return {"from": start, "to": cur, **ch}


def dismiss(home: Path, current: str = __version__) -> dict:
    """The owner closed the card: it stays closed until a newer release runs. Settings and data are untouched."""
    st = read_state(home)
    cur = release_notes.stable_of(current)
    st["dismissed"], st["last"] = cur, st.get("last") or cur
    st.pop("from", None)
    _save(home, st)
    return {"dismissed": cur}


def since_lines(since: str, current: str | None = None, he: bool = False,
                secs: list[dict] | None = None) -> tuple[bool, list[str]]:
    """CLI text for `jarvis whatsnew --since V`: the same items as the card."""
    current = current or __version__
    if not vkey(since):
        return False, ["גרסה לא תקינה." if he else "Not a version (expected x.y.z)."]
    ch = changes(release_notes.sections() if secs is None else secs, since, release_notes.stable_of(current), limit=50)
    if not ch["items"]:
        return False, [f"אין שינויים למשתמש מאז {since}." if he else f"No user-facing changes since {since}."]
    kinds = release_notes.KIND_HE if he else {}
    head = (f"מה השתנה מאז {since}:" if he else f"What changed since {since}:")
    lines = [head] + [f"- {i['version']} {kinds.get(i['kind'], i['kind'])}"
                      f"{' (' + i['scope'] + ')' if i['scope'] else ''}: {i['text']}" for i in ch["items"]]
    return True, lines


def dashboard_routes(register) -> None:
    def _on(ctx) -> bool:
        try:
            return feat.Features(ctx.cfg, ctx.home).enabled(FEATURE)
        except Exception:
            return False

    def get(ctx, _m, _b):
        if not _on(ctx):
            return 200, {"enabled": False, "card": None}
        return 200, {"enabled": True, "card": card(ctx.home)}

    def post(ctx, _m, body):
        if not _on(ctx):
            return 403, {"error": "the what's-new card is off (beta): jarvis features on " + FEATURE}
        if str((body or {}).get("action") or "") != "dismiss":
            return 400, {"error": "action must be 'dismiss'"}
        return 200, dismiss(ctx.home)
    register("GET", r"/api/home/whats-new", get)
    register("POST", r"/api/home/whats-new", post)


def _probe(c: feat.ProbeCtx) -> tuple[bool, str]:
    return True, ""


feat.register(FEATURE, "כרטיס מה השתנה מאז הגרסה שלך", "What changed since your version card", False, probe=_probe,
              risk="low",
              about="after an update show one short dismissable card on the dashboard Home with only the changes between "
                    "your previous version and this one (from the release notes); settings and data are not touched",
              targets=targets("test_whats_new_card.py"),
              permissions=["read"], settings="jarvis features on whats_new_card")
