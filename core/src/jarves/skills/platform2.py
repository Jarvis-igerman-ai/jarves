"""Skills Platform 2.0 wiring (V6 item 13, issue #220): manifest v2 trust in the generated-Skill flow, plus the owner's
`jarvis skills list | update | rollback` commands. OFF by default behind the `skills_manifest2` switch.

What changes when the switch is ON (generated.py calls `check` and `approved_sha`):
  * propose / approve: the trust state (unsigned / owner_signed / tampered) is computed and shown in the summary. A
    TAMPERED signature, or an invalid manifest 2, makes the draft fail validation: it can never be approved, installed
    or updated. Unsigned and owner-signed Skills need exactly the same owner approval (sha256) as before, and still
    install DISABLED with no grants. A signature never skips a step.
  * load / run: an installed version whose files no longer match the sha256 the owner approved, or whose signature
    is tampered, is not registered and does not run (SKILL_TAMPERED).
  * rollback: the earlier version's stored copy must match the sha256 approved for it; grants are cleared as before.
With the switch OFF everything behaves as before (v1 Skills read as legacy 0.0.0 either way).

Update and rollback are owner actions only (CLI / dashboard, `generated.USER_ROUTES`): there is no model tool for
them. Nothing here downloads, imports third-party code or creates a key: the optional owner key is a file the owner
puts at <home>/skills/owner.key (16 bytes or more); without it a signed Skill reads as tampered.
Logs carry the Skill id, version numbers and short reason codes, never the SKILL.md or skill.py text.
"""
from __future__ import annotations

import json
from pathlib import Path

from .. import __version__, clilang, logs
from . import manifest2 as m2

FEATURE = "skills_manifest2"
KEY_FILE = "owner.key"
MAX_KEY = 4096


def owner_key(home: Path) -> bytes | None:
    """The local owner key if the owner created <home>/skills/owner.key; never created or exported here."""
    p = Path(home) / "skills" / KEY_FILE
    try:
        if p.is_symlink() or not p.is_file() or p.stat().st_size > MAX_KEY:
            return None
        key = p.read_bytes().strip()
    except OSError:
        return None
    return key if len(key) >= 16 else None


def check(md: str, py: str | None, *, key: bytes | None, present: set[str] = frozenset(),
          upgrading: bool = False) -> dict:
    """Trust + manifest facts of one Skill. `errors` non-empty = refuse (never load, approve or update)."""
    m = m2.parse_manifest(md, py)
    trust = m2.trust_state(md, py, key)
    errors: list[str] = []
    if trust == "tampered":
        errors.append("manifest v2: the signature does not match (changed after signing, or signed with another "
                      "key); refused")
    if not m.legacy:
        errors += [f"manifest v2: {e}" for e in m2.check_install(m, core_version=__version__,
                                                                  present=set(present) | ({m.id} if upgrading else set()))]
    return {"trust": trust, "manifest": m.manifest, "legacy": m.legacy, "version": m.version,
            "permissions": m.permissions, "dependencies": m.dependencies, "surfaces": m.surfaces,
            "signed_by": m.signed_by[:40], "errors": errors, "warnings": list(m.warnings)}


def approved_sha(state: dict, version: int) -> str:
    """The sha256 the owner approved for one installed version ('' when unknown)."""
    for h in reversed(state.get("history") or []):
        if isinstance(h, dict) and h.get("version") == version and isinstance(h.get("sha256"), str):
            return h["sha256"]
    return ""


def plan_update(mgr, sid: str, *, quiet: bool = False) -> dict:
    """What `jarvis skills update <sid>` would do with the waiting draft: never changes anything."""
    from .generated import SkillError, _sha
    sid = mgr._id(sid)
    if not (mgr.drafts / sid).is_dir():
        raise SkillError(f"no new version of {sid} is waiting; propose one first (New Skills page)")
    md, py = mgr.files(sid, draft=True)
    key = owner_key(mgr.home)
    installed = (mgr.installed / sid).is_dir()
    present = set(mgr.installed_ids()) | set(mgr.taken()[0])
    new_info = check(md, py, key=key, present=present, upgrading=installed)
    old = None
    old_version = None
    if installed:
        omd, opy = mgr.files(sid)
        old = m2.parse_manifest(omd, opy)
        old_version = int(mgr.state(sid).get("version", 0))
    plan = m2.plan_update(old, m2.parse_manifest(md, py))
    rep = json.loads((mgr.drafts / sid / "report.json").read_text(encoding="utf-8")) \
        if (mgr.drafts / sid / "report.json").is_file() else {}
    errors = list(new_info["errors"]) + [e for e in rep.get("errors", []) if not str(e).startswith("manifest v2:")]
    out = {"id": sid, "installed_version": old_version, "from": old.version if old else None,
           "to": new_info["version"], "trust": new_info["trust"], "plan": plan, "sha256": _sha(md, py),
           "errors": errors, "refused": bool(errors), "needs_approval": True}
    if not quiet:                       # viewing a draft is not an event (#314): only an explicit plan is logged
        logs.info("skills", "SKILL_UPDATE_PLANNED", "Skill update planned", skill=sid, action=plan["action"],
                  trust=new_info["trust"], added=len(plan["added_permissions"]), refused=bool(errors))
    return out


# ---------------------------------------------------------------------- CLI: jarvis skills list|update|rollback
def add_cli(sub) -> None:
    p = sub.add_parser("skills", help="generated Skills: jarvis skills list | update NAME [--approve SHA256] | "
                                      "rollback NAME [--yes]. Owner only; update and rollback need Skills manifest v2 on")
    p.add_argument("action", nargs="?", default="list", choices=["list", "update", "rollback"])
    p.add_argument("name", nargs="?", default="")
    p.add_argument("--approve", default="", metavar="SHA256", help="update: the sha256 shown by the plan (you reviewed it)")
    p.add_argument("--yes", action="store_true", help="rollback: do it (without it, only shows what would happen)")
    p.add_argument("--json", action="store_true")


def _say(he: str, en: str) -> None:
    print(clilang.fmt(he, en))


def manager(cfg, home):
    """(GeneratedSkills for the CLI, is the switch on). The CLI is an owner route (by='cli')."""
    from ..cli import _cli_features
    from .generated import GeneratedSkills
    on = _cli_features(cfg, home).active(FEATURE)
    return GeneratedSkills(Path(home), v2=lambda: on), on


def cmd_skills(a, cfg, home) -> int:
    from .generated import SkillError
    mgr, on = manager(cfg, home)
    if a.action == "list":
        rows = []
        for v in mgr.overview()["installed"]:
            info = v.get("manifest2") or {}
            rows.append({"id": v["id"], "version": v["version"], "semver": info.get("version", "0.0.0"),
                         "enabled": v["enabled"], "trust": info.get("trust", "-"), "versions": v["versions"]})
        if a.json:
            print(json.dumps(rows, ensure_ascii=False))
            return 0
        if not rows:
            _say("אין כישורים שנוצרו.", "No generated Skills.")
        for r in rows:
            print(f"  {r['id']}  v{r['version']} ({r['semver']})  {'on' if r['enabled'] else 'off'}  trust: {r['trust']}")
        return 0
    if not on:
        _say("מניפסט כישורים גרסה 2 כבוי. הפעילו: jarvis features on skills_manifest2",
             "Skills manifest v2 is off. Turn it on: jarvis features on skills_manifest2")
        return 2
    if not a.name:
        _say("חסר שם כישור.", "Name a Skill.")
        return 2
    try:
        if a.action == "update":
            plan = plan_update(mgr, a.name)
            if a.json and not a.approve:
                print(json.dumps(plan, ensure_ascii=False))
                return 1 if plan["refused"] else 0
            p = plan["plan"]
            print(f"{plan['id']}: {p['action']} {plan['from'] or '-'} -> {plan['to']}  trust: {plan['trust']}")
            print("  added permissions: " + (", ".join(p["added_permissions"]) or "none"))
            print("  removed permissions: " + (", ".join(p["removed_permissions"]) or "none"))
            if plan["refused"]:
                _say("העדכון נדחה:", "Update refused:")
                for e in plan["errors"][:8]:
                    print("  - " + e)
                return 1
            if not a.approve:
                _say("כדי לאשר אחרי שבדקתם: jarvis skills update " + plan["id"] + " --approve " + plan["sha256"],
                     "To approve after review: jarvis skills update " + plan["id"] + " --approve " + plan["sha256"])
                return 0
            v = mgr.approve(plan["id"], a.approve, by="cli")      # re-validates, re-checks trust and the sha256
            _say(f"הותקנה גרסה {v['version']} כבויה ובלי הרשאות. תנו הרשאות והפעילו בלוח הבקרה.",
                 f"Version {v['version']} installed DISABLED with no grants. Grant and enable it on the dashboard.")
            return 0
        if not a.yes:
            v = mgr.view(mgr._id(a.name))
            _say("חזרה לגרסה הקודמת מכבה את הכישור ומוחקת את ההרשאות. להרצה: --yes",
                 "Rolling back turns the Skill off and clears its grants. To do it: --yes")
            print(f"  {v['id']}: v{v['version']}, versions {v['versions']}")
            return 0
        v = mgr.rollback(a.name, by="cli")
        _say(f"חזרתי לגרסה {v['version']}; הכישור כבוי וההרשאות נמחקו.",
             f"Rolled back to version {v['version']}; the Skill is off and its grants are cleared.")
        return 0
    except (SkillError, PermissionError, OSError) as e:
        print(str(e)[:300])
        return 1


COMMANDS = {"skills": cmd_skills}
