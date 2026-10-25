"""V6 #220: Skills Platform 2.0 wiring. Manifest v2 trust in the generated-Skill flow (propose / approve / load / run),
`jarvis skills update` (plan with added permissions, owner approval by sha256, tampered refused) and `rollback`
(grants cleared, stored copy sha256-checked). OFF by default; v1 Skills unchanged. Local files only, no network."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from jarves import features, logs
from jarves.skills import SkillRegistry
from jarves.skills import generated as gen
from jarves.skills import manifest2 as m2
from jarves.skills import platform2 as p2

KEY = b"0123456789abcdef-owner-key"
SECRET_BODY = "ZZ_BODY_MARKER_never_in_logs"
SECRET_CODE = "ZZ_CODE_MARKER_never_in_logs"

V1_MD = f"---\nname: shopping\ntitle: Shopping\ndescription: Keeps a list\ncapabilities: [storage]\n---\n{SECRET_BODY}\n"
V1_PY = f'''
"""{SECRET_CODE}"""
TOOLS = [
    {{"name": "shopping_add", "description": "Add one item", "permission": "action", "args": {{"item": "string"}}}},
    {{"name": "shopping_list", "description": "Read the list", "permission": "read", "args": {{}}}},
]


def shopping_add(args, ctx):
    items = ctx.storage_get("items", [])
    items.append(args["item"])
    ctx.storage_set("items", items)
    return "added"


def shopping_list(args, ctx):
    return ", ".join(ctx.storage_get("items", [])) or "empty"
'''


def v2_md(version="1.0.0", perms="[read, action, storage]", body=SECRET_BODY, extra=""):
    return (f"---\nname: shopping\ntitle: Shopping\ndescription: Keeps a list\nmanifest: 2\nversion: {version}\n"
            f"permissions: {perms}\nsurfaces: [voice, cli]\n{extra}---\n{body}\n")


def signed(md, py, key=KEY):
    sig = m2.sign(md, py, key)
    return md.replace("\n---\n", f"\nsigned_by: owner\nsignature: {sig}\n---\n", 1)


def setup(tmp_path, v2=True):
    reg = SkillRegistry()
    return reg, gen.GeneratedSkills(tmp_path, v2=lambda: v2)


def put_key(tmp_path, key=KEY):
    (tmp_path / "skills").mkdir(parents=True, exist_ok=True)
    (tmp_path / "skills" / "owner.key").write_bytes(key)


def install(mgr, md, py=V1_PY):
    rep = mgr.propose(md, py, by="voice")
    assert rep["ok"], rep["errors"]
    return mgr.approve("shopping", rep["sha256"], by="dashboard"), rep


@pytest.fixture
def captured(monkeypatch):
    seen = []
    for name in ("info", "warn", "error"):
        orig = getattr(logs, name)

        def cap(*a, _orig=orig, **k):
            seen.append(repr((a, k)))
            return _orig(*a, **k)
        monkeypatch.setattr(logs, name, cap)
    return seen


# ---------------------------------------------------------------------- default off, v1 unchanged
def test_switch_is_off_by_default():
    assert features.CATALOG["skills_manifest2"].default_enabled is False
    assert gen.GeneratedSkills(__import__("pathlib").Path(".")).v2() is False


@pytest.mark.parametrize("v2", [False, True], ids=["switch_off", "switch_on"])
async def test_v1_skill_flow_unchanged(tmp_path, v2):
    reg, mgr = setup(tmp_path, v2)
    view, rep = install(mgr, V1_MD)
    assert view["version"] == 1 and view["enabled"] is False and view["grants"] == {"capabilities": [], "actions": False}
    assert rep["capabilities"] == ["storage"] and rep["permissions"] == ["action", "read"]
    assert ("manifest2" in rep) is v2
    if v2:
        assert rep["manifest2"]["legacy"] and rep["manifest2"]["version"] == "0.0.0"
        assert rep["manifest2"]["trust"] == "unsigned" and "trust: unsigned" in rep["summary_en"]
    mgr.grant("shopping", ["storage"], True, by="dashboard")
    mgr.set_enabled("shopping", True, by="dashboard")
    gen.sync(mgr, reg)
    assert (await reg.call("shopping_add", {"item": "milk"}, origin="model")).ok
    assert (await reg.call("shopping_list", {}, origin="model")).message == "milk"


# ---------------------------------------------------------------------- trust states on propose / approve
async def test_unsigned_v2_needs_the_same_owner_approval(tmp_path):
    reg, mgr = setup(tmp_path)
    rep = mgr.propose(v2_md(), V1_PY, by="voice")
    assert rep["ok"], rep["errors"]
    assert rep["manifest2"]["trust"] == "unsigned" and not rep["manifest2"]["legacy"]
    assert rep["capabilities"] == ["storage"]                    # a v2 `permissions` storage entry is requestable
    assert "unknown frontmatter key" not in " ".join(rep["warnings"])
    with pytest.raises(PermissionError):
        mgr.approve("shopping", rep["sha256"], by="voice")
    with pytest.raises(gen.SkillError):
        mgr.approve("shopping", "0" * 64, by="dashboard")
    v = mgr.approve("shopping", rep["sha256"], by="dashboard")
    assert v["enabled"] is False and v["grants"] == {"capabilities": [], "actions": False}
    gen.sync(mgr, reg)
    assert "shopping" in reg.disabled                            # never auto-enabled


async def test_owner_signed_still_needs_approval_and_installs_disabled(tmp_path):
    reg, mgr = setup(tmp_path)
    put_key(tmp_path)
    md = signed(v2_md(), V1_PY)
    rep = mgr.propose(md, V1_PY, by="voice")
    assert rep["ok"] and rep["manifest2"]["trust"] == "owner_signed", rep["errors"]
    assert "חתום במפתח שלכם" in rep["summary_he"]
    with pytest.raises(PermissionError):
        mgr.approve("shopping", rep["sha256"], by="model")
    v = mgr.approve("shopping", rep["sha256"], by="cli")
    assert v["enabled"] is False and v["grants"]["actions"] is False and v["manifest2"]["trust"] == "owner_signed"


@pytest.mark.parametrize("case", ["wrong_key", "no_key", "body_changed"])
def test_tampered_manifest_is_never_installed(tmp_path, case):
    _, mgr = setup(tmp_path)
    md = signed(v2_md(), V1_PY, key=b"another-pc-key-another-pc-key")
    if case != "no_key":
        put_key(tmp_path)
    if case == "body_changed":
        md = signed(v2_md(), V1_PY).replace(SECRET_BODY, SECRET_BODY + " and also something else")
    rep = mgr.propose(md, V1_PY, by="voice")
    assert not rep["ok"] and rep["manifest2"]["trust"] == "tampered"
    assert any("signature does not match" in e for e in rep["errors"])
    with pytest.raises(gen.SkillError, match="signature does not match"):
        mgr.approve("shopping", rep["sha256"], by="dashboard")
    assert mgr.installed_ids() == []


def test_invalid_v2_manifest_is_refused(tmp_path):
    _, mgr = setup(tmp_path)
    rep = mgr.propose(v2_md(perms="[read, storage]"), V1_PY)          # skill.py has action tools, undeclared
    assert not rep["ok"] and any("does not declare 'action'" in e for e in rep["errors"])
    rep = mgr.propose(v2_md(extra="min_core: 99.0.0\n"), V1_PY)
    assert not rep["ok"] and any("needs Core 99.0.0" in e for e in rep["errors"])
    rep = mgr.propose(v2_md(extra="dependencies: [notes_pro]\n"), V1_PY)
    assert not rep["ok"] and any("notes_pro" in e for e in rep["errors"])
    with pytest.raises(gen.SkillError):
        mgr.approve("shopping", rep["sha256"], by="dashboard")


def test_switch_off_keeps_todays_behaviour_for_a_signed_file(tmp_path):
    _, mgr = setup(tmp_path, v2=False)
    md = signed(v2_md(extra="capabilities: [storage]\n"), V1_PY, key=b"another-pc-key-another-pc-key")
    rep = mgr.propose(md, V1_PY)
    assert rep["ok"] and "manifest2" not in rep, rep["errors"]   # trust is not consulted while the switch is off
    assert any("unknown frontmatter key" in w for w in rep["warnings"])      # v2 keys are only warnings, as today


# ---------------------------------------------------------------------- update plan
def test_update_plan_lists_added_permissions_and_needs_approval(tmp_path):
    _, mgr = setup(tmp_path)
    install(mgr, v2_md("1.0.0", "[read, storage]"), V1_PY.replace('"permission": "action"', '"permission": "read"'))
    rep = mgr.propose(v2_md("1.1.0", "[read, action, storage, network:api.example.com]"), V1_PY)
    assert rep["ok"], rep["errors"]
    plan = p2.plan_update(mgr, "shopping")
    assert plan["plan"]["action"] == "upgrade" and plan["from"] == "1.0.0" and plan["to"] == "1.1.0"
    assert plan["plan"]["added_permissions"] == ["action", "network:api.example.com"]
    assert plan["needs_approval"] and plan["plan"]["needs_new_grant"] and not plan["refused"]
    assert plan["sha256"] == rep["sha256"]
    assert mgr.view("shopping")["version"] == 1                  # planning changes nothing
    assert "update_plan" in mgr.draft("shopping")
    v = mgr.approve("shopping", plan["sha256"], by="cli")
    assert v["version"] == 2 and not v["enabled"] and v["grants"] == {"capabilities": [], "actions": False}


def test_update_refuses_a_tampered_new_version(tmp_path):
    _, mgr = setup(tmp_path)
    put_key(tmp_path)
    install(mgr, v2_md("1.0.0"))
    md = signed(v2_md("1.1.0"), V1_PY).replace(SECRET_BODY, "changed after signing")
    mgr.propose(md, V1_PY)
    plan = p2.plan_update(mgr, "shopping")
    assert plan["refused"] and plan["trust"] == "tampered"
    with pytest.raises(gen.SkillError):
        mgr.approve("shopping", plan["sha256"], by="cli")
    assert mgr.view("shopping")["version"] == 1


# ---------------------------------------------------------------------- rollback
async def test_rollback_clears_grants_and_checks_the_stored_copy(tmp_path):
    reg, mgr = setup(tmp_path)
    install(mgr, v2_md("1.0.0"))
    rep2 = mgr.propose(v2_md("1.1.0"), V1_PY.replace('or "empty"', 'or "nothing"'))
    mgr.approve("shopping", rep2["sha256"], by="dashboard")
    mgr.grant("shopping", ["storage"], True, by="dashboard")
    mgr.set_enabled("shopping", True, by="dashboard")
    with pytest.raises(PermissionError):
        mgr.rollback("shopping", by="voice")
    v = mgr.rollback("shopping", by="cli")
    assert v["version"] == 1 and not v["enabled"] and v["grants"] == {"capabilities": [], "actions": False}
    gen.sync(mgr, reg)
    assert reg.skills["shopping"].version == 1 and "shopping" in reg.disabled
    # a new update after a rollback gets a fresh version folder (v2 is kept, never overwritten)
    rep3 = mgr.propose(v2_md("1.2.0"), V1_PY)
    assert mgr.approve("shopping", rep3["sha256"], by="cli")["version"] == 3


def test_rollback_never_returns_to_a_version_that_was_rolled_away_from(tmp_path):
    """#314 item 5: v1, v2, rollback to v1, new v3: a second rollback goes to v1, not back to the bad v2."""
    _, mgr = setup(tmp_path)
    install(mgr, v2_md("1.0.0"))
    mgr.approve("shopping", mgr.propose(v2_md("1.1.0"), V1_PY)["sha256"], by="dashboard")
    assert mgr.rollback("shopping", by="cli")["version"] == 1
    assert mgr.approve("shopping", mgr.propose(v2_md("1.2.0"), V1_PY)["sha256"], by="cli")["version"] == 3
    assert mgr.rollback("shopping", by="cli")["version"] == 1
    with pytest.raises(gen.SkillError, match="no earlier version"):
        mgr.rollback("shopping", by="cli")


def test_viewing_a_draft_writes_no_log_line(tmp_path, captured):
    """#314 item 6: draft() is a read; only an explicit plan is logged."""
    _, mgr = setup(tmp_path)
    install(mgr, v2_md("1.0.0"))
    mgr.propose(v2_md("1.1.0"), V1_PY)
    captured.clear()
    mgr.draft("shopping")
    assert not [x for x in captured if "SKILL_UPDATE_PLANNED" in x]
    p2.plan_update(mgr, "shopping")
    assert [x for x in captured if "SKILL_UPDATE_PLANNED" in x]


def test_rollback_refuses_a_changed_stored_copy(tmp_path):
    _, mgr = setup(tmp_path)
    install(mgr, v2_md("1.0.0"))
    rep2 = mgr.propose(v2_md("1.1.0"), V1_PY)
    mgr.approve("shopping", rep2["sha256"], by="dashboard")
    old = tmp_path / "skills" / "installed" / "shopping" / "versions" / "1" / "SKILL.md"
    old.write_text(old.read_text(encoding="utf-8") + "extra line\n", encoding="utf-8")
    with pytest.raises(gen.SkillError, match="does not match the sha256"):
        mgr.rollback("shopping", by="cli")
    assert mgr.view("shopping")["version"] == 2


# ---------------------------------------------------------------------- load / run fail closed
@pytest.mark.parametrize("v2", [False, True], ids=["switch_off", "switch_on"])
async def test_changed_installed_copy_is_not_loaded_when_on(tmp_path, v2):
    reg, mgr = setup(tmp_path, v2)
    install(mgr, V1_MD)
    mgr.grant("shopping", ["storage"], False, by="dashboard")
    mgr.set_enabled("shopping", True, by="dashboard")
    p = tmp_path / "skills" / "installed" / "shopping" / "versions" / "1" / "skill.py"
    p.write_text(p.read_text(encoding="utf-8").replace('or "empty"', 'or "hacked"'), encoding="utf-8")
    gen.sync(mgr, reg)
    r = await mgr.run("shopping", "shopping_list", {})
    if v2:
        assert "shopping" not in reg.skills and r.code == "SKILL_TAMPERED"
        assert mgr.view("shopping")["manifest2"]["integrity"].startswith("the stored copy")
    else:                                                        # today's behaviour: the sandbox is the defence
        assert "shopping" in reg.skills and r.ok and r.message == "hacked"


async def test_signed_install_whose_signature_no_longer_verifies_is_not_loaded(tmp_path):
    """The stored copy still matches its approved sha256, but its signature no longer verifies (the owner key was
    removed or replaced): with the switch on it is neither registered nor run (fail closed)."""
    reg, mgr = setup(tmp_path)
    put_key(tmp_path)
    install(mgr, signed(v2_md(), V1_PY))
    mgr.grant("shopping", ["storage"], False, by="dashboard")
    mgr.set_enabled("shopping", True, by="dashboard")
    gen.sync(mgr, reg)
    assert "shopping" in reg.skills
    put_key(tmp_path, b"a-different-owner-key-0123456789")
    gen.sync(mgr, reg)
    r = await mgr.run("shopping", "shopping_list", {})
    assert "shopping" not in reg.skills and r.code == "SKILL_TAMPERED" and "tampered signature" in r.message


def test_cli_output_escapes_model_written_dependency_names(tmp_path):
    _, mgr = setup(tmp_path)
    mgr.propose(v2_md(extra="dependencies: [notes\x1b[2Kpro]\n"), V1_PY)
    plan = p2.plan_update(mgr, "shopping")
    assert plan["refused"] and not any("\x1b" in e for e in plan["errors"])


# ---------------------------------------------------------------------- owner only, logs
async def test_model_cannot_update_or_rollback(tmp_path):
    reg, mgr = setup(tmp_path)
    reg.allowed_actions |= {"skill_propose"}
    reg.register(gen.make_builder_skill(mgr))
    names = {t.name for t in reg.tools()}
    assert names == {"skill_propose", "skills_generated_list"}
    assert not [n for n in names if any(w in n for w in ("update", "rollback", "approve", "grant", "enable"))]
    install(mgr, v2_md("1.0.0"))
    r = await reg.call("skill_propose", {"skill_md": v2_md("1.1.0"), "skill_py": V1_PY}, origin="model")
    assert r.ok and mgr.view("shopping")["version"] == 1         # the model only drafts
    sha = mgr.draft("shopping")["report"]["sha256"]
    for by in ("voice", "model", "telegram", ""):
        with pytest.raises(PermissionError):
            mgr.approve("shopping", sha, by=by)
        with pytest.raises(PermissionError):
            mgr.rollback("shopping", by=by)


async def test_logs_hold_no_skill_source_text(tmp_path, captured):
    reg, mgr = setup(tmp_path)
    put_key(tmp_path)
    install(mgr, signed(v2_md("1.0.0"), V1_PY))
    mgr.propose(signed(v2_md("1.1.0"), V1_PY).replace("Keeps a list", "Keeps a list!"), V1_PY)    # tampered
    p2.plan_update(mgr, "shopping")
    rep = mgr.propose(v2_md("1.2.0"), V1_PY)
    mgr.approve("shopping", rep["sha256"], by="cli")
    mgr.rollback("shopping", by="cli")
    f = tmp_path / "skills" / "installed" / "shopping" / "versions" / "1" / "skill.py"
    f.write_text(f.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    gen.sync(mgr, reg)
    await mgr.run("shopping", "shopping_list", {})
    blob = "\n".join(captured)
    assert "SKILL_V2_REFUSED" in blob and "SKILL_UPDATE_PLANNED" in blob
    assert SECRET_BODY not in blob and SECRET_CODE not in blob and "def shopping_add" not in blob


# ---------------------------------------------------------------------- CLI
def _cli(monkeypatch, tmp_path, on, *argv):
    from jarves import cli
    monkeypatch.setattr(p2, "manager", lambda cfg, home: (gen.GeneratedSkills(tmp_path, v2=lambda: on), on))
    import argparse
    sub = argparse.ArgumentParser().add_subparsers(dest="cmd")
    p2.add_cli(sub)
    a = sub.choices["skills"].parse_args(list(argv))
    assert cli._handler(("late", p2, ("skills",), "COMMANDS"), "skills") is p2.cmd_skills
    return p2.cmd_skills(a, None, tmp_path)


def test_cli_update_and_rollback(monkeypatch, tmp_path, capsys):
    mgr = gen.GeneratedSkills(tmp_path, v2=lambda: True)
    install(mgr, v2_md("1.0.0", "[read, action]"), V1_PY.replace("ctx.storage_get(\"items\", [])", "[]")
            .replace("    ctx.storage_set(\"items\", items)\n", ""))
    assert _cli(monkeypatch, tmp_path, False, "update", "shopping") == 2      # switch off
    assert "is off" in capsys.readouterr().out
    rep = mgr.propose(v2_md("1.1.0"), V1_PY)
    assert _cli(monkeypatch, tmp_path, True, "update", "shopping") == 0
    out = capsys.readouterr().out
    assert "added permissions: storage" in out and rep["sha256"] in out and SECRET_CODE not in out
    assert mgr.view("shopping")["version"] == 1
    assert _cli(monkeypatch, tmp_path, True, "update", "shopping", "--approve", "0" * 64) == 1
    assert _cli(monkeypatch, tmp_path, True, "update", "shopping", "--approve", rep["sha256"]) == 0
    assert mgr.view("shopping")["version"] == 2
    assert _cli(monkeypatch, tmp_path, True, "rollback", "shopping") == 0     # dry run without --yes
    assert mgr.view("shopping")["version"] == 2
    assert _cli(monkeypatch, tmp_path, True, "rollback", "shopping", "--yes") == 0
    assert mgr.view("shopping")["version"] == 1
    assert _cli(monkeypatch, tmp_path, True, "list", "--json") == 0
    rows = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rows[0]["id"] == "shopping" and rows[0]["semver"] == "1.0.0" and rows[0]["trust"] == "unsigned"


def test_dashboard_without_config_counts_the_switch_as_off(tmp_path):
    mgr = gen._mgr(SimpleNamespace(home=tmp_path))
    assert mgr.v2() is False
