"""Skills manifest v2 (V6 P6, issue #212): a parser, validator and planner. PURE: it reads text and local files only.

What it is not: it never installs, enables, grants, runs or fetches anything. `skills/generated.py` keeps its sha256
approval, grants, sandbox and rollback; `skills/platform2.py` wires this module into them (issue #220). No network
code exists here, and a manifest can never raise a Skill's own permissions: `plan_update` only reports them so the
owner approves the difference (a new permission always needs a new approval).

Format: the same flat SKILL.md frontmatter as v1 (`generated.parse_skill_md`) plus optional v2 keys:
    manifest: 2                      # absent or 1 = a legacy v1 Skill, read with defaults (backward compatible)
    version: 1.2.3                   # semver; a v1 label that is not semver becomes 0.0.0 with legacy=true
    min_core: 4.1.0                  # refuse on an older Core
    permissions: [read, action, storage, network:api.example.com]
    dependencies: [notes, lists]     # Skill ids that must already exist
    surfaces: [voice, dashboard, cli, phone]
    signed_by: owner                 # key label; signature = HMAC-SHA256 hex over id, version and content hash
    signature: <hex>
Trust is local and never automatic: `unsigned` (default, needs the owner review as today), `owner_signed` (valid
signature from the key of THIS PC owner; still needs approval to install), `tampered` (signature present but wrong:
refuse). No third-party signer, no certificate store, no key exchange.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import features, hebrew
from ..channels_common import targets
from .generated import HOST_RX, ID_RX, MAX_MD, MAX_PY, SkillError, _sha, parse_skill_md, validate

SURFACES = ("voice", "dashboard", "cli", "phone")
BASE_PERMS = ("read", "action", "storage")
SEMVER_RX = re.compile(r"^(\d{1,4})\.(\d{1,5})\.(\d{1,6})$")
TRUST = ("unsigned", "owner_signed", "tampered")
SIG_LINE_RX = re.compile(r"^\s*(signature|signed_by)\s*:", re.I)


def parse_version(v: str) -> tuple[int, int, int] | None:
    m = SEMVER_RX.match(str(v or "").strip())
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


@dataclass
class Manifest:
    id: str
    title: str = ""
    description: str = ""
    version: str = "0.0.0"
    manifest: int = 1
    legacy: bool = True
    min_core: str = ""
    permissions: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    surfaces: list[str] = field(default_factory=lambda: ["voice"])
    signed_by: str = ""
    signature: str = ""
    sha256: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _list(v) -> list[str]:
    v = v if isinstance(v, list) else [v] if v else []
    return [str(x).strip().lower() for x in v if str(x).strip()]


def _perm_ok(p: str) -> bool:
    if p in BASE_PERMS:
        return True
    host = p[8:]
    return (p.startswith("network:") and bool(HOST_RX.match(host)) and not re.match(r"^[\d.]+$", host)
            and host != "localhost" and not host.endswith((".local", ".lan", ".internal")))


def parse_manifest(md: str, py: str | None = None) -> Manifest:
    """Parse + validate one SKILL.md (v1 or v2). Never raises for bad content: errors are listed on the result."""
    md = md if isinstance(md, str) else ""
    if len(md) > MAX_MD or (py is not None and len(py) > MAX_PY):       # same caps as generated.validate
        return Manifest(id="", errors=[f"SKILL.md larger than {MAX_MD} or skill.py larger than {MAX_PY} characters"])
    try:
        meta, _ = parse_skill_md(md)
    except SkillError as e:
        return Manifest(id="", errors=[str(e)])
    m = Manifest(id=str(meta.get("name", "")), title=str(meta.get("title") or meta.get("name") or "")[:120],
                 description=str(meta.get("description", ""))[:500], sha256=_sha(md, py))
    if not ID_RX.match(m.id):
        m.errors.append("name must be 3-32 chars: lower-case letters, digits, '_'")
    if not m.description.strip():
        m.errors.append("description is required")
    try:
        m.manifest = int(str(meta.get("manifest", "1")))
    except ValueError:
        m.manifest = 0
    if m.manifest not in (1, 2):
        m.errors.append("manifest must be 1 or 2")
    m.legacy = m.manifest != 2
    raw_v = str(meta.get("version", "")).strip()
    if parse_version(raw_v):
        m.version = raw_v
    elif raw_v and m.legacy:
        m.warnings.append(f"version label {raw_v!r} is not semver; read as 0.0.0")
    elif m.manifest == 2:
        m.errors.append("manifest 2 needs version as major.minor.patch")
    m.min_core = str(meta.get("min_core", "")).strip()
    if m.min_core and not parse_version(m.min_core):
        m.errors.append("min_core must be major.minor.patch")
    # v1 used `capabilities` for storage / network:host; v2 uses `permissions`. Both are counted: generated.py grants
    # from `capabilities`, so a manifest may never hide one behind a smaller `permissions` list.
    perms = _list(meta.get("permissions", [])) + _list(meta.get("capabilities", []))
    if py is not None:
        # the real read/action level comes from skill.py's TOOLS (generated.validate); never trust the label alone
        code_perms = set(validate(md, py)["permissions"]) - {"read"}
        missing = sorted(code_perms - set(perms))
        if m.manifest == 2:
            m.errors += [f"skill.py has {p} tools but the manifest does not declare {p!r}" for p in missing]
        perms += missing
    for p in perms:
        if not _perm_ok(p):
            m.errors.append(f"unknown or unsafe permission {p!r} (allowed: read, action, storage, network:<public host>)")
    m.permissions = sorted({p for p in perms if _perm_ok(p)})
    m.dependencies = _list(meta.get("dependencies", []))
    for d in m.dependencies:
        if not ID_RX.match(d) or d == m.id:
            m.errors.append(f"bad dependency {d!r}")
    if "surfaces" in meta:
        m.surfaces = _list(meta["surfaces"])
        for s in m.surfaces:
            if s not in SURFACES:
                m.errors.append(f"unknown surface {s!r} (allowed: {', '.join(SURFACES)})")
    m.signed_by = str(meta.get("signed_by", "")).strip()
    m.signature = str(meta.get("signature", "")).strip().lower()
    if m.signature and not re.fullmatch(r"[0-9a-f]{64}", m.signature):
        m.errors.append("signature must be 64 hex characters")
    return m


# ---------------------------------------------------------------------- trust (local owner key only)
def strip_signature(md: str) -> str:
    """Drop the signature lines of the FRONTMATTER only: a body line that starts with 'Signature:' stays covered."""
    end = md.find("\n---", 3) if md.startswith("---") else -1
    if end < 0:
        return md
    head = "\n".join(line for line in md[:end].split("\n") if not SIG_LINE_RX.match(line))
    return head + md[end:]


def _payload(md: str, py: str | None) -> bytes:
    m = parse_manifest(strip_signature(md), py)         # the hash covers everything except the signature lines
    return f"{m.id}\0{m.version}\0{m.sha256}".encode()


def sign(md: str, py: str | None, key: bytes) -> str:
    """HMAC-SHA256 hex (owner tooling only, never a model tool). The key stays on this PC."""
    if len(key) < 16:
        raise ValueError("owner key too short")
    return hmac.new(key, _payload(md, py), hashlib.sha256).hexdigest()


def trust_state(md: str, py: str | None, key: bytes | None) -> str:
    """unsigned | owner_signed | tampered. Informational: it never installs or enables anything."""
    m = parse_manifest(md, py)
    if not m.signature:
        return "unsigned"
    if not key or len(key) < 16 or not m.ok:
        return "tampered"
    return "owner_signed" if hmac.compare_digest(sign(md, py, key), m.signature) else "tampered"


# ---------------------------------------------------------------------- planning (pure; the owner decides)
def check_install(m: Manifest, *, core_version: str, present: set[str]) -> list[str]:
    """Reasons this manifest cannot be proposed here (empty = it may be shown to the owner for approval)."""
    out = list(m.errors)
    need, have = parse_version(m.min_core), parse_version(core_version)
    if need and have and have < need:
        out.append(f"needs Core {m.min_core} or newer (this is {core_version})")
    out += [f"needs the Skill {d}, which is not installed" for d in m.dependencies if d not in present]
    return out


def plan_update(old: Manifest | None, new: Manifest) -> dict:
    """What an update would change. `needs_approval` is always True: no auto-update, no auto-grant. A permission the
    old version did not have is listed in `added_permissions` and needs its own owner grant."""
    if old is None:
        return {"action": "install", "needs_approval": True, "added_permissions": list(new.permissions),
                "removed_permissions": [], "rollback_to": None, "needs_new_grant": bool(new.permissions)}
    a, b = parse_version(old.version) or (0, 0, 0), parse_version(new.version) or (0, 0, 0)
    action = "same" if a == b else "upgrade" if b > a else "downgrade"
    added = sorted(set(new.permissions) - set(old.permissions))
    return {"action": action, "needs_approval": True, "added_permissions": added,
            "removed_permissions": sorted(set(old.permissions) - set(new.permissions)),
            "rollback_to": old.version, "needs_new_grant": bool(added)}


def plan_rollback(history: list[str], current: str) -> str | None:
    """The newest semver in `history` older than `current`, or None. generated.rollback() clears grants."""
    cur = parse_version(current)
    older = sorted((v for v in history if cur and (p := parse_version(v)) and p < cur), key=parse_version)
    return older[-1] if older else None


def discover(root: Path, limit: int = 200) -> list[Manifest]:
    """Manifests of Skill folders directly under a LOCAL directory (<root>/<id>/SKILL.md). No network, nothing is run,
    unreadable folders are skipped. Symbolic links (folders or files) are never followed, and an over-size file
    (or a device) is never read, so a planted link cannot point the scan outside `root`."""
    out: list[Manifest] = []

    def _read(f: Path, cap: int) -> str | None:
        if f.is_symlink() or (f.exists() and not f.is_file()):
            raise OSError("not a plain file")                # the whole Skill folder is skipped, never half-read
        if not f.exists():
            return None
        if f.stat().st_size > cap * 4:                       # utf-8: at most 4 bytes per character
            raise OSError("too large")
        with f.open("rb") as fh:
            return fh.read(cap * 4 + 1).decode("utf-8")

    try:
        dirs = sorted(p for p in Path(root).iterdir() if not p.is_symlink() and p.is_dir())[:limit]
    except OSError:
        return out
    for d in dirs:
        try:
            md = _read(d / "SKILL.md", MAX_MD)
            if md is None:
                continue
            out.append(parse_manifest(md, _read(d / "skill.py", MAX_PY)))
        except (OSError, UnicodeDecodeError):
            continue
    return out


ABOUT = ("reads and checks Skill manifests (version, permissions, dependencies, local trust); refuses a tampered or "
         "invalid manifest on install and update, and adds jarvis skills update / rollback")
EVIDENCE = "UNIT test_v6_manifest2.py + test_v6_skills_platform2.py (local files only, no network)"
BLOCKER = "V6 beta: wired into generated-Skill approve, load, update and rollback; not owner-tested yet"
hebrew.TEXT.update({
    ABOUT: "קורא ובודק מניפסטים של כישורים (גרסה, הרשאות, תלויות, אמון מקומי); דוחה מניפסט פגום או לא תקין בהתקנה "
           "ובעדכון, ומוסיף jarvis skills update / rollback",
    EVIDENCE: "בדיקות יחידה test_v6_manifest2.py ו-test_v6_skills_platform2.py (קבצים מקומיים בלבד, בלי רשת)",
    BLOCKER: "בטא V6: מחובר לאישור, טעינה, עדכון וחזרה לאחור של כישורים שנוצרו; עוד לא נבדק על ידי הבעלים",
})

features.register("skills_manifest2", "מניפסט כישורים גרסה 2", "Skills manifest v2", False,
                  about=ABOUT, targets=targets(EVIDENCE, BLOCKER), permissions=["read"], settings="")
