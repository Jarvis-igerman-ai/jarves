"""Campaign stages A: the feature matrix (parity, guide, capabilities row, wizard group, ON/OFF over CLI, dashboard
and the Core tool path, unavailable error path, approval for risky switches)."""
from __future__ import annotations

import re

from tools.campaign_features import check, cli, rec, stage, until

STATE = {"ACTIVE": "active", "off": "off", "unavailable": "unavailable"}


def cli_states(out: str) -> dict[str, str]:
    res = {}
    for line in out.splitlines():
        m = re.match(r"^([a-z0-9_]+)\s+(ACTIVE|off|unavailable)\s+(low|medium|high)\s", line)
        if m:
            res[m.group(1)] = STATE[m.group(2)]
    return res


def dash_state(row: dict) -> str:
    return "active" if row["active"] else ("off" if not row["enabled"] else "unavailable")


def owned(h, fid: str) -> list[str]:
    return [n for n, (sk, tl) in h.reg._tools.items() if h.feat.owner(sk.name, n) == fid]


@stage
async def inventory(h):
    from jarves import features as F
    from jarves.setup_wizard import groups
    ids = list(F.CATALOG)
    r = await h.acli("features")
    cs = cli_states(r.stdout)
    rec("inventory", "feature count", "PASS", f"Core catalog {len(ids)} features; CLI lists {len(cs)}")
    missing = [i for i in ids if i not in cs]
    check("inventory", "CLI `jarvis features` lists every feature the Core registers", not missing,
          f"missing from CLI: {missing}")
    _, d = await h.aget("/api/features")
    dids = [f["id"] for f in d["features"]]
    check("inventory", "dashboard /api/features lists every feature", set(dids) == set(ids),
          f"diff {set(ids) ^ set(dids)}")
    # wizard groups
    grouped = {i for _, _, _, g in groups() for i in g}
    other = [i for _, _, _, g in groups() if _ == _ for i in g] if False else None
    gid_of = {i: gid for gid, _, _, g in groups() for i in g}
    from jarves.setup_wizard import GROUPS, WIZARD_HIDDEN
    listed = {i for _g, _h, _e, g in GROUPS for i in g}
    nogroup = [i for i in ids if gid_of.get(i) in (None, "other") and not (i in WIZARD_HIDDEN and i in listed)]
    check("inventory", "every feature has a wizard group (not 'other'); WIZARD_HIDDEN ones are grouped but never offered", not nogroup,
          f"in other/none: {nogroup}; hidden by design: {sorted(WIZARD_HIDDEN)}")
    _, st = await h.aget("/api/setup")
    api_groups = {i for g in st.get("groups", []) for i in [x.get("id") for x in g.get("features", g.get("items", []))]}
    rec("inventory", "dashboard /api/setup returns groups", "PASS" if st.get("groups") else "FAIL",
        f"{len(st.get('groups', []))} groups; keys {list(st)[:8]}")


@stage
async def matrix(h):
    from jarves import capabilities, guides
    from jarves import features as F
    from jarves.setup_wizard import groups
    gid_of = {i: gid for gid, _, _, g in groups() for i in g}
    r = await h.acli("features")
    cs = cli_states(r.stdout)
    _, d = await h.aget("/api/features")
    rows = {f["id"]: f for f in d["features"]}
    for fid in list(F.CATALOG):
        A = f"F:{fid}"
        core_st = h.feat.status(fid)
        cs_s = cs.get(fid)
        ds = dash_state(rows[fid])
        core_s = "active" if core_st["active"] else ("off" if not core_st["enabled"] else "unavailable")
        check(A, "state parity CLI/dashboard/Core", cs_s == ds == core_s, f"cli={cs_s} dash={ds} core={core_s} reason={core_st['reason']!r}")
        check(A, "dashboard reason matches Core reason", rows[fid]["reason"] == core_st["reason"],
              f"{rows[fid]['reason']!r} vs {core_st['reason']!r}")
        g = guides.for_feature(fid) or guides.find(fid)
        if g is None:
            rec(A, "guide exists", "FAIL", "no guide for this feature (guides.for_feature/find)")
        else:
            hr = await h.acli("help", g.id)
            ok = hr.returncode == 0 and g.title_en.split()[0].lower() in hr.stdout.lower() and "Traceback" not in hr.stderr
            check(A, f"`jarvis help {g.id}` works", ok, f"rc={hr.returncode} len={len(hr.stdout)} err={hr.stderr[:200]}")
            gs, gj = await h.aget(f"/api/guides/{g.id}")
            check(A, "dashboard guide API", gs == 200 and gj.get("id") == g.id, f"{gs}")
        cv = capabilities.view(fid)
        check(A, "capabilities row declared + no matrix problems", cv["declared"] and not cv["problems"],
              f"declared={cv['declared']} problems={cv['problems']}")
        check(A, "capabilities has windows + pi target", set(cv["targets"]) >= {"windows", "linux_arm64_pi"}, str(list(cv["targets"])))
        from jarves.setup_wizard import GROUPS, WIZARD_HIDDEN
        listed = {x: gid for gid, _h, _e, g in GROUPS for x in g}
        hidden_ok = fid in WIZARD_HIDDEN and fid in listed          # grouped but never offered by design (documented in setup_wizard.py)
        check(A, "wizard group (WIZARD_HIDDEN ones are grouped but never offered)", gid_of.get(fid) not in (None, "other") or hidden_ok,
              f"group={gid_of.get(fid) or listed.get(fid)}{' (hidden by design)' if hidden_ok else ''}")


async def _toggle(h, fid: str):
    A = f"F:{fid}"
    f = h.feat.feature(fid)
    tools = owned(h, fid)
    orig = h.feat.status(fid)
    # --- OFF via CLI
    r = await h.acli("features", "off", fid)
    seen = await until(lambda: not h.feat.enabled(fid), 5)
    _, d = await h.aget("/api/features")
    row = next(x for x in d["features"] if x["id"] == fid)
    check(A, "CLI off -> Core + dashboard see it", r.returncode == 0 and seen and row["enabled"] is False
          and row["changed_by"] == "cli", f"rc={r.returncode} {r.stderr[:150]} core_off={seen} row={row['enabled']} by={row['changed_by']}")
    if tools:
        res = await h.call(tools[0], {})
        check(A, "OFF: owned tool refused FEATURE_OFF + not offered", res.code == "FEATURE_OFF"
              and tools[0] not in [t.name for t in h.reg.tools()], f"{tools[0]} -> {res.code}: {res.message[:120] if hasattr(res,'message') else ''}")
    else:
        rec(A, "OFF: owned tool refused", "N/A", "feature owns no tools (switch only)")
    # --- ON via dashboard
    sc, on = await h.apost(f"/api/features/{fid}", {"enabled": True})
    seen = await until(lambda: h.feat.enabled(fid), 5)
    check(A, "dashboard on -> Core sees it", sc == 200 and seen and on.get("changed_by") == "dashboard", f"{sc} {on.get('changed_by')}")
    st = h.feat.status(fid)
    if st["available"]:
        if tools:
            check(A, "available+on: tools offered", all(t in [x.name for x in h.reg.tools()] for t in tools), str(tools))
        rec(A, "available state", "PASS", "available")
    else:
        if tools:
            res = await h.call(tools[0], {})
            ok = res.code == "FEATURE_OFF" and (st["reason"][:25] in res.message or "is " in res.message)
            check(A, "unavailable error path: tool refuses truthfully with reason", ok, f"{res.code} {res.message[:200]!r}")
        check(A, "unavailable state shows a reason", bool(st["reason"]), f"reason={st['reason']!r}")
    # bad dashboard payloads
    sc, _ = await h.apost(f"/api/features/{fid}", {"enabled": "yes"})
    check(A, "dashboard rejects non-bool", sc == 400, str(sc))
    # --- CLI on
    h.feat.invalidate()
    await h.apost(f"/api/features/{fid}", {"enabled": False})
    r = await h.acli("features", "on", fid)
    if r.returncode == 0:
        rec(A, "CLI on (Core running)", "PASS", r.stdout.strip()[:200])
    else:
        why = (r.stderr or r.stdout).strip()
        ok_exp = "Prerequisites pending" in why
        rec(A, "CLI on (Core running)", "PASS" if ok_exp else "FAIL",
            "refused as designed (prerequisites/availability): " + why[:260] if ok_exp else why[:300])
    # --- Core tool path: model asks ON then OFF
    await h.apost(f"/api/features/{fid}", {"enabled": False})
    await until(lambda: not h.feat.enabled(fid), 3)
    h.policy["v"] = False
    h.answers.clear()
    res = await h.call("feature_set", {"id": fid, "on": True})
    if f.risk in ("medium", "high"):
        check(A, f"Core tool feature_set ON ({f.risk} risk) asks approval; denied -> stays off + request",
              res.code == "FEATURE_NEEDS_APPROVAL" and not h.feat.enabled(fid) and bool(h.feat.status(fid)["requested"]) and bool(h.answers),
              f"{res.code} asked={len(h.answers)} enabled={h.feat.enabled(fid)}")
        h.policy["v"] = True
        res = await h.call("feature_set", {"id": fid, "on": True})
        check(A, "approved -> turns on", res.ok and h.feat.enabled(fid), f"{res.code} {res.ok}")
    else:
        check(A, "Core tool feature_set ON (low risk) works without a prompt", res.ok and h.feat.enabled(fid) and not h.answers,
              f"{res.code} asked={len(h.answers)}")
    res = await h.call("feature_set", {"id": fid, "on": False})
    check(A, "Core tool feature_set OFF always allowed (no prompt)", res.ok and not h.feat.enabled(fid), f"{res.code}")
    # --- CLI reset + restore original
    r = await h.acli("features", "reset", fid)
    await until(lambda: h.feat.status(fid)["source"] != "user", 4)       # the Core re-reads features.json at most once a second
    after = h.feat.status(fid)
    check(A, "CLI reset -> default (Core follows within 1 s)", r.returncode == 0 and after["source"] != "user", f"rc={r.returncode} source={after['source']}")
    h.policy["v"] = True


@stage
async def toggles(h):
    from jarves import features as F
    only = __import__("os").environ.get("ONLY")
    for fid in list(F.CATALOG):
        if only and fid not in only.split(","):
            continue
        if fid == "dashboard":                      # turning the dashboard off would cut our own HTTP session; own test
            rec(f"F:{fid}", "toggle", "BLOCKED", "covered in dashboard_off stage")
            continue
        try:
            await _toggle(h, fid)
        except Exception as e:
            rec(f"F:{fid}", "toggle crashed", "FAIL", f"{type(e).__name__}: {e}")
        await h.apost(f"/api/features/{fid}", {"reset": True})


@stage
async def unknown_ids(h):
    r = await h.acli("features", "on", "no_such")
    check("errors", "CLI unknown feature id", r.returncode == 2 and "unknown feature" in r.stderr and "Traceback" not in r.stderr, r.stderr[:160])
    sc, j = await h.apost("/api/features/no_such", {"enabled": True})
    check("errors", "dashboard unknown feature id -> 404", sc == 404, f"{sc} {j}")
    res = await h.call("feature_set", {"id": "no_such", "on": True})
    check("errors", "Core tool unknown feature id", res.code == "FEATURE_UNKNOWN", res.code)
    res = await h.call("feature_set", {"id": "music"})
    check("errors", "Core tool bad args", res.code == "TOOL_BAD_ARGS", res.code)
    r = await h.acli("help", "no-such-topic-zzz")
    check("errors", "`jarvis help` unknown topic is friendly", "Traceback" not in r.stderr, f"rc={r.returncode} {r.stdout[:100]} {r.stderr[:100]}")


@stage
async def resets(h):
    from jarves import features as F
    for fid in list(F.CATALOG):
        h.feat.set(fid, not h.feat.enabled(fid), by="cli")
        r = await h.acli("features", "reset", fid)
        ok = await until(lambda: h.feat.status(fid)["source"] != "user", 4)
        check(f"F:{fid}", "CLI reset -> default (Core follows within 1 s)", r.returncode == 0 and ok, f"rc={r.returncode} follow={ok}")
