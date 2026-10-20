"""Profiles (Issue #17): several users on one computer, and a clearly labelled TEST profile that never touches real data.

A profile is one isolated state dir. Everything JARVES keeps lives in the state dir already (memory.db, store.db
conversations and costs, workspace SOUL/AGENTS/USER/MEMORY files, config.toml, features.json, identity.json = name /
wake phrase / voice, secrets.env, google/ and chatgpt/ tokens, privacy flag, telegram/ and calendar channels, logs,
backups), so a profile is simply "which state dir":

- ``default``      today's location, unchanged (JARVES_HOME, else %LOCALAPPDATA%\\jarves / ~/.local/state/jarves).
- any other name   <base>/profiles/<name>/ with <name>/profile.json = {name, kind, created, port, from}.
- ``test``         kind "test": fake/offline defaults (ai.provider offline, voice off, music none), its own budget,
                   SAFE_OFF switches off, a red "TEST PROFILE" banner in the dashboard and on the face.

Choosing the profile (first match wins): ``jarvis --profile <name> ...`` (sets JARVES_PROFILE for this process and
its children) > the JARVES_PROFILE environment variable > ``jarvis profile use <name>`` (<base>/profiles.json
"active") > default. One Core per profile: the instance lock is <profile home>/core.lock and every profile gets its
own link port (allocated at create time without collision, re-checked at `jarvis run`); the dashboard always takes a
free port and its login cookie is named per profile, so two dashboards on 127.0.0.1 never log each other out.

Security (application level; all profiles belong to the same OS user, so this is NOT an OS security boundary between
people sharing one Windows account; separate Windows accounts remain the strong boundary):
- a Core / dashboard / CLI only ever opens its own profile dir; names are validated (no paths, no Windows device names);
- a non-default profile drops secret environment variables inherited from the parent process (OPENAI_API_KEY ...) and
  loads only its own secrets.env, so starting it from the default profile never hands it the default's keys
  (JARVES_PROFILE_SECRETS_FROM_ENV=1 is the explicit opt-in);
- `create --from` never copies secrets.env (only with the explicit --copy-secrets), Google / ChatGPT tokens, Telegram
  pairing, the link token, paired phones, face data or conversations; memories only with --copy-memory;
- delete needs the profile name typed back, refuses the default profile and a profile whose Core is running;
- export is a verified backup archive (backup.py): credentials are never in it.
- the default profile's backup and diagnostics skip <base>/profiles/ (other people's data is not in your backup).

Interface:
    profiles.current_name() / home_for(name) / info(home) / list_profiles() / create(...) / delete(name, confirm)
    profiles.use(name) / export(name, out) / prepare_env(name) / port_conflicts(name, port) / make_skill(core)
    dashboard_routes(register)   GET /api/profiles, POST /api/profiles, POST /api/profiles/<name>/(delete|use|open)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from . import features as feat
from . import logs
from .channels_common import targets

DEFAULT = "default"
TEST = "test"
ENV = "JARVES_PROFILE"
SECRETS_FROM_ENV = "JARVES_PROFILE_SECRETS_FROM_ENV"
DIR = "profiles"                 # <base>/profiles/<name>/
REGISTRY = "profiles.json"       # <base>/profiles.json {"active": name}
PROFILE_FILE = "profile.json"    # <profile home>/profile.json
PORT_FIRST, PORT_LAST = 8766, 8865
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,23}$")
_WINDOWS_DEVICES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10))}
# copied by `create --from` (settings, not data); everything else stays behind
COPY_FILES = ("config.toml", "features.json", "identity.json", "setup_done", "setup_state.json")
PERSONA = ("AGENTS.md", "SOUL.md", "IDENTITY.md")            # persona files: always copied with --from
PERSONAL = ("USER.md", "MEMORY.md")                          # about the user: only with --copy-memory
NEVER_COPIED = ("secrets.env (unless --copy-secrets)", "google/ and chatgpt/ sign-in tokens", "telegram/ pairing",
                "link_token", "paired phones", "Pear pairing", "faces.db", "store.db conversations", "logs", "backups")

TEST_CONFIG = """# JARVES TEST PROFILE (jarvis profile create test): fake / offline defaults, never your real data.
# Its own memory, conversations, costs, budget, keys and sign-ins. Switch a part on for a test on purpose only.
assistant_name = "JARVES TEST"

[link]
port = {port}

[ai]
provider = "offline"          # no AI requests (and no cost) until you change this here on purpose
daily_request_limit = 50
daily_realtime_minutes = 10

[voice]
mode = "off"                  # no microphone / speaker / cloud voice by default
greeting = false

[music]
provider = "none"

[history]
llm_review = false
"""
TEST_USER_MD = ("# על המשתמש / About the user\n"
                "פרופיל בדיקה: אין כאן נתונים אמיתיים. / TEST PROFILE: there is no real user data here.\n")
TEST_FEATURES_OFF = ("camera", "face_recognition", "camera_when_away", "camera_preview", "gmail_send", "gmail_read",
                     "google_calendar", "calendar_write", "google_contacts", "dashboard_lan", "telegram",
                     "home_assistant", "autostart", "tray_icon", "update_check", "music", "web_search",
                     "background_tasks", "memory_llm_pass", "diagnosis_llm_pass")
TEST_MONTHLY_BUDGET_USD = 10.0     # owner answer 20 Oct (FULL_TEST_PLAN): about $10 for a whole test campaign


class ProfileError(ValueError):
    """A message that says what to do; never contains another profile's data."""


# ---------------------------------------------------------------------- names and places
def valid_name(name: str) -> str:
    n = str(name or "").strip().lower()
    if not _NAME.match(n) or n in _WINDOWS_DEVICES or n == DIR:
        raise ProfileError(f"bad profile name {name!r}: 1-24 characters a-z, 0-9, '-' or '_', starting with a letter "
                           "or digit (e.g. dana, kids, test)")
    return n


def base_dir() -> Path:
    from .config import base_dir as _base
    return _base()


def home_for(name: str, base: Path | None = None) -> Path:
    """The state dir of a profile (it is not created here, except the default one, which always exists)."""
    n = valid_name(name)
    b = base or base_dir()
    return b if n == DEFAULT else b / DIR / n


def exists(name: str, base: Path | None = None) -> bool:
    n = valid_name(name)
    return n == DEFAULT or (home_for(n, base) / PROFILE_FILE).is_file()


def data_roots(home: Path) -> list[Path]:
    """The JARVES data folders a profile's tools must never reach into: its own home and, for a named profile
    (<base>/profiles/<name>), the base folder, which IS the default profile's home and holds every other profile."""
    home = Path(home)
    roots = [home]
    try:
        if home.parent.name == DIR and (home / PROFILE_FILE).is_file() and info(home).get("name") != DEFAULT:
            roots.append(home.parent.parent)
    except OSError:
        pass
    return roots


def _read_json(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, d: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def active(base: Path | None = None) -> str:
    """The profile chosen with `jarvis profile use` (default when none, or when it was deleted)."""
    b = base or base_dir()
    n = str(_read_json(b / REGISTRY).get("active") or DEFAULT)
    try:
        return n if exists(n, b) else DEFAULT
    except ProfileError:
        return DEFAULT


def current_name(base: Path | None = None) -> str:
    """--profile / JARVES_PROFILE, else the active profile, else default."""
    env = os.environ.get(ENV, "").strip()
    return valid_name(env) if env else active(base)


def info(home: Path) -> dict:
    """What a profile says about itself: {name, kind, test, created, port, from}. The base dir is "default"."""
    d = _read_json(Path(home) / PROFILE_FILE)
    name = d.get("name") or DEFAULT
    kind = d.get("kind") if d.get("kind") in ("normal", "test") else "normal"
    return {"name": name, "kind": kind, "test": kind == "test", "created": d.get("created"), "port": d.get("port"),
            "from": d.get("from")}


def select(name: str) -> str:
    """`--profile`: this process and every child it starts (background Core, dashboard) use that profile."""
    n = valid_name(name)
    if not exists(n):
        raise ProfileError(f"there is no profile {n!r}. Create it: jarvis profile create {n}   (list: jarvis profile list)")
    os.environ[ENV] = n
    return n


def prepare_env(name: str) -> list[str]:
    """A non-default profile never inherits the parent's secret environment variables (it loads its own
    secrets.env next). Returns the names removed. JARVES_PROFILE_SECRETS_FROM_ENV=1 keeps them (explicit opt-in)."""
    from .config import SECRET_ENV
    if valid_name(name) == DEFAULT or os.environ.get(SECRETS_FROM_ENV) == "1":
        return []
    gone = [k for k in SECRET_ENV if os.environ.pop(k, None) is not None]
    return gone


# ---------------------------------------------------------------------- ports
def _config_port(home: Path) -> int | None:
    import tomllib
    try:
        raw = tomllib.loads((Path(home) / "config.toml").read_text(encoding="utf-8"))
        p = (raw.get("link") or {}).get("port")
        return int(p) if p else None
    except (OSError, ValueError, TypeError):
        return None


def link_port(home: Path) -> int:
    from .config import LinkConfig
    return _config_port(home) or info(home).get("port") or LinkConfig().port


def _profile_dirs(base: Path) -> list[Path]:
    root = base / DIR
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.iterdir()):
        if p.is_dir() and not p.is_symlink() and (p / PROFILE_FILE).is_file() and _NAME.match(p.name):
            out.append(p)
    return out


def used_ports(base: Path | None = None) -> dict[int, str]:
    b = base or base_dir()
    out = {link_port(b): DEFAULT}
    for p in _profile_dirs(b):
        out.setdefault(link_port(p), p.name)
    return out


def _bindable(port: int) -> bool:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def allocate_port(base: Path | None = None, bindable=_bindable) -> int:
    used = used_ports(base)
    for p in range(PORT_FIRST, PORT_LAST + 1):
        if p not in used and bindable(p):
            return p
    raise ProfileError(f"no free link port between {PORT_FIRST} and {PORT_LAST}; delete an unused profile")


def port_conflicts(name: str, port: int, base: Path | None = None) -> list[str]:
    """Other profiles configured on the same link port (two Cores would fight over it)."""
    b = base or base_dir()
    n = valid_name(name)
    others = [(DEFAULT, b)] + [(p.name, p) for p in _profile_dirs(b)]
    return [o for o, h in others if o != n and link_port(h) == port]


# ---------------------------------------------------------------------- list / create / use / delete / export
def _running(home: Path) -> bool:
    from .runtime.lock import held
    return held(home)


def base_of(home: Path) -> Path:
    """The base dir a profile home belongs to (the default profile's home IS the base)."""
    home = Path(home)
    return home.parent.parent if (home / PROFILE_FILE).is_file() and home.parent.name == DIR else home


def list_profiles(base: Path | None = None, current: str | None = None) -> list[dict]:
    """Names, kind, port and whether the Core runs: metadata only, never another profile's content."""
    b = base or base_dir()
    cur, act = current or current_name(b), active(b)
    rows = []
    for name, home in [(DEFAULT, b)] + [(p.name, p) for p in _profile_dirs(b)]:
        i = info(home)
        rows.append({"name": name, "kind": i["kind"], "test": i["test"], "created": i["created"],
                     "port": link_port(home), "running": _running(home), "active": name == act, "current": name == cur})
    return rows


def _copy_sqlite(src: Path, dst: Path) -> None:
    s = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True, timeout=30)
    d = sqlite3.connect(str(dst))
    try:
        s.backup(d)
        d.execute("PRAGMA journal_mode=DELETE")
        d.commit()
    finally:
        s.close()
        d.close()


def _set_port(home: Path, port: int) -> None:
    from .cli import set_toml_value
    set_toml_value(home / "config.toml", "link", "port", str(port))


def _copy_from(src: Path, dst: Path, *, copy_memory: bool, copy_secrets: bool) -> list[str]:
    copied = []
    for name in COPY_FILES:
        if (src / name).is_file():
            shutil.copy2(src / name, dst / name)
            copied.append(name)
    ws_src, ws_dst = src / "workspace", dst / "workspace"
    for name in PERSONA + (PERSONAL if copy_memory else ()):
        if (ws_src / name).is_file():
            ws_dst.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ws_src / name, ws_dst / name)
            copied.append(f"workspace/{name}")
    if copy_memory and (ws_src / "memory").is_dir():
        shutil.copytree(ws_src / "memory", ws_dst / "memory", dirs_exist_ok=True)
        copied.append("workspace/memory/")
    if (src / "wake").is_dir():                     # trained wake phrases (identity.json points at them)
        shutil.copytree(src / "wake", dst / "wake", dirs_exist_ok=True)
        copied.append("wake/")
        ident = _read_json(dst / "identity.json")
        w = ident.get("wake") or {}
        m = Path(str(w.get("model", "")))
        if w and m.suffix in (".npz", ".tflite") and (src / "wake").resolve() in m.resolve().parents:
            w["model"] = str((dst / "wake" / m.name).resolve())
            ident["wake"] = w
            _write_json(dst / "identity.json", ident)
    if (src / "memory.db").is_file():               # settings (voice, budget, backup...) and, if asked, memories
        _copy_sqlite(src / "memory.db", dst / "memory.db")
        db = sqlite3.connect(str(dst / "memory.db"))
        try:
            db.execute("DELETE FROM usage")             # the budget counts of the source are not this profile's
            if not copy_memory:
                db.execute("DELETE FROM memories")
            db.commit()
            db.execute("VACUUM")                        # no deleted rows left in the file
        except sqlite3.Error:
            pass
        finally:
            db.close()
        copied.append("memory.db (" + ("memories + settings" if copy_memory else "settings only") + ")")
    if copy_secrets and (src / "secrets.env").is_file():
        from .config import secrets_file
        dst_s = secrets_file(dst)
        fd = os.open(dst_s, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write((src / "secrets.env").read_text(encoding="utf-8-sig"))
        copied.append("secrets.env (explicitly asked: --copy-secrets)")
    return copied


def _write_test_defaults(home: Path, port: int) -> None:
    (home / "config.toml").write_text(TEST_CONFIG.format(port=port), encoding="utf-8")
    _write_json(home / feat.STATE_FILE, {"version": 1, "enabled": {f: False for f in TEST_FEATURES_OFF},
                                          "changed": {f: {"by": "profile-test", "ts": time.time()}
                                                      for f in TEST_FEATURES_OFF}, "requests": {}})
    (home / "workspace").mkdir(exist_ok=True)
    (home / "workspace" / "USER.md").write_text(TEST_USER_MD, encoding="utf-8")
    (home / "setup_done").write_text("test profile\n", encoding="utf-8")
    from . import usage
    usage.set_settings(home, {"api_monthly_budget_usd": TEST_MONTHLY_BUDGET_USD, "costs_api": "off"})


def create(name: str, *, from_: str | None = None, copy_memory: bool = False, copy_secrets: bool = False,
           test: bool | None = None, base: Path | None = None, by: str = "cli") -> dict:
    """Make a new profile dir. `test` defaults to True for the name "test"."""
    b = base or base_dir()
    n = valid_name(name)
    if n == DEFAULT:
        raise ProfileError("the default profile always exists")
    home = home_for(n, b)
    if home.exists():
        raise ProfileError(f"profile {n!r} already exists (jarvis profile list)")
    src = None
    if from_:
        s = valid_name(from_)
        if not exists(s, b):
            raise ProfileError(f"there is no profile {s!r} to copy from")
        src = home_for(s, b)
    elif copy_memory or copy_secrets:
        raise ProfileError("--copy-memory / --copy-secrets need --from <profile>")
    is_test = (n == TEST) if test is None else bool(test)
    port = allocate_port(b)
    home.mkdir(parents=True)
    try:
        if sys.platform != "win32":
            os.chmod(home, 0o700)                 # POSIX: the profile dir is private to the user
        copied: list[str] = []
        if src is not None:
            copied = _copy_from(src, home, copy_memory=copy_memory, copy_secrets=copy_secrets)
        if is_test and src is None:
            _write_test_defaults(home, port)
        _set_port(home, port)
        meta = {"name": n, "kind": "test" if is_test else "normal", "created": time.time(), "port": port,
                "from": from_ and valid_name(from_), "copied": copied}
        _write_json(home / PROFILE_FILE, meta)
    except Exception:
        shutil.rmtree(home, ignore_errors=True)
        raise
    logs.info("profiles", "PROFILE_CREATED", "profile created", profile=n, kind=meta["kind"], port=port, by=by,
              copied_from=meta["from"], memory=bool(copy_memory), secrets=bool(copy_secrets))
    return {**info(home), "home": str(home), "copied": copied, "never_copied": list(NEVER_COPIED)}


def use(name: str, base: Path | None = None) -> str:
    b = base or base_dir()
    n = valid_name(name)
    if not exists(n, b):
        raise ProfileError(f"there is no profile {n!r} (jarvis profile list)")
    _write_json(b / REGISTRY, {"active": n, "changed": time.time()})
    logs.info("profiles", "PROFILE_ACTIVE", "active profile set", profile=n)
    return n


def delete(name: str, confirm: str, base: Path | None = None, by: str = "cli") -> dict:
    """Remove a profile and ALL its data. `confirm` must be the profile name typed back."""
    b = base or base_dir()
    n = valid_name(name)
    if n == DEFAULT:
        raise ProfileError("the default profile cannot be deleted")
    if not exists(n, b):
        raise ProfileError(f"there is no profile {n!r}")
    if str(confirm or "").strip().lower() != n:
        raise ProfileError(f"not deleted: type the profile name ({n}) to confirm")
    home = home_for(n, b)
    if _running(home):
        raise ProfileError(f"not deleted: JARVES is running in profile {n}. Stop it first: jarvis --profile {n} stop")
    root = (b / DIR).resolve()
    real = home.resolve()
    if home.is_symlink() or real.parent != root:
        raise ProfileError("not deleted: the profile folder is not inside the profiles folder")
    shutil.rmtree(real)
    if _read_json(b / REGISTRY).get("active") == n:
        _write_json(b / REGISTRY, {"active": DEFAULT, "changed": time.time()})
    logs.info("profiles", "PROFILE_DELETED", "profile deleted", profile=n, by=by)
    return {"deleted": n}


def export(name: str, out: Path | None = None, base: Path | None = None) -> Path:
    """A verified backup archive of one profile (restore with `jarvis --profile <name> restore <zip>`). Credentials
    are never in it (backup.py rules)."""
    from . import backup
    b = base or base_dir()
    n = valid_name(name)
    if not exists(n, b):
        raise ProfileError(f"there is no profile {n!r}")
    made = backup.create(home_for(n, b), "manual", note=f"profile export: {n}")
    if out is None:
        return made
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(made, out)
    return out


# ---------------------------------------------------------------------- starting another profile's process
def _child_env(name: str) -> dict:
    from .config import SECRET_ENV
    env = {k: v for k, v in os.environ.items() if k not in SECRET_ENV}
    env[ENV] = name
    return env


def spawn(name: str, args: list[str]) -> None:
    """`python -m jarves --profile <name> <args>` as its own detached process, without this profile's secrets."""
    cmd = [sys.executable, "-m", "jarves", "--profile", name, *args]
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
              start_new_session=sys.platform != "win32", env=_child_env(name), cwd=str(home_for(name)))
    try:
        subprocess.Popen(cmd, creationflags=flags | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0), **kw)
    except OSError:
        subprocess.Popen(cmd, creationflags=flags, **kw)


SPAWN = spawn          # tests replace this (no real processes from unit tests)


# ---------------------------------------------------------------------- Hebrew texts (hebrew.TEXT once it is on main)
ABOUT = ("several users on this computer, each with their own memory, conversations, costs, settings, keys and "
         "sign-ins, plus a TEST profile that never touches real data (\"switch to <name>\" asks on the screen first)")
EVIDENCE = "UNIT test_profiles.py (two real Cores side by side in temp dirs, no cross-leak)"
BLOCKER = "owner has not created or switched profiles hands-on yet"
PI_BLOCKER = "plain Python state dirs, no Windows-only code; never run on a Raspberry Pi (no device yet)"
HE = {
    ABOUT: "כמה משתמשים במחשב הזה, לכל אחד זיכרון, שיחות, עלויות, הגדרות, מפתחות והתחברויות משלו, ועוד פרופיל בדיקה "
           "שלא נוגע בנתונים אמיתיים (\"תעבור ל<שם>\" מבקש קודם אישור על המסך)",
    EVIDENCE: "בדיקות יחידה test_profiles.py (שתי ליבות אמיתיות זו לצד זו בתיקיות זמניות, בלי דליפה ביניהן)",
    BLOCKER: "הבעלים עוד לא יצר או החליף פרופילים בעצמו",
    PI_BLOCKER: "תיקיות מצב רגילות ב-Python, בלי קוד ייחודי ל-Windows; עוד לא רץ על Raspberry Pi (אין מכשיר עדיין)",
    "TEST PROFILE": "פרופיל בדיקה",
    "not your real data": "לא הנתונים האמיתיים שלך",
}
try:                                    # SW-V3 P0.2b hebrew.py (not on main yet): register our texts there too
    from . import hebrew as _hebrew     # type: ignore[attr-defined]
    _hebrew.TEXT.update(HE)
except ImportError:
    pass

feat.register("profiles", "פרופילים (משתמשים ופרופיל בדיקה)", "Profiles (users and a test profile)", True,
              risk="medium", skills=["profiles"], tools=["profile_list", "profile_switch"], about=ABOUT,
              targets=targets(EVIDENCE, BLOCKER, PI_BLOCKER),
              permissions=["read", "action (switch: on-screen approval)"],
              settings="jarvis profile list|create|use|delete|export, dashboard Profiles page (#/profiles)")


# ---------------------------------------------------------------------- the voice Skill
_HE_LATIN = {"ב": "b", "ג": "g", "ד": "d", "ה": "h", "ו": "v", "ז": "z", "ח": "h", "ט": "t", "י": "y", "כ": "k",
             "ך": "k", "ל": "l", "מ": "m", "ם": "m", "נ": "n", "ן": "n", "ס": "s", "פ": "p", "ף": "p", "צ": "tz",
             "ץ": "tz", "ק": "k", "ר": "r", "ש": "sh", "ת": "t", "א": "", "ע": ""}


def _skeleton(s: str) -> str:
    """Consonants only, Hebrew transliterated: 'dana' / 'דנה' -> 'dn', 'test' / 'טסט' -> 'tst'."""
    s = "".join(_HE_LATIN.get(ch, ch) for ch in s.strip().lower())
    s = s.replace("ch", "h").replace("w", "v").replace("c", "k").replace("ph", "p")
    return re.sub(r"[^a-z0-9]|[aeiouyhv]", "", s)


def spoken_match(said: str, names: list[str]) -> str | None:
    """The profile the user named: an exact (valid) name, else the ONE profile whose consonants match what was said
    (a Hebrew-spoken "דנה" -> "dana"). None when nothing or more than one matches."""
    try:
        n = valid_name(said)
        if n in names:
            return n
    except ProfileError:
        pass
    sk = _skeleton(said)
    hits = [x for x in names if sk and _skeleton(x) == sk]
    return hits[0] if len(hits) == 1 else None


def make_skill(core):
    """profile_list (read) and profile_switch (action): switching asks on the SCREEN first (registry.confirm, never a
    spoken yes alone), then starts the other profile's Core with its window and quits this one after the goodbye.
    No speaker recognition in v1: the person says which profile."""
    from pydantic import BaseModel, Field

    from .skills import NoArgs, Skill, Tool, ToolResult

    class SwitchArgs(BaseModel):
        name: str = Field(..., min_length=1, max_length=24, description="the profile name the user said, e.g. dana, test")

    class ProfileSkill(Skill):
        name = "profiles"
        description = "Which profiles (users / test) exist, and switching to another one after on-screen approval"

        def tools(self) -> list[Tool]:
            base = base_of(core.home)

            async def list_(_):
                cur = info(core.home)["name"]
                rows = list_profiles(base, cur)
                text = "; ".join(f"{r['name']}{' (test)' if r['test'] else ''}{' <- current' if r['name'] == cur else ''}"
                                 for r in rows)
                return ToolResult(True, text, {"current": cur, "profiles": [
                    {k: r[k] for k in ("name", "kind", "running")} for r in rows]})

            async def switch(a: SwitchArgs):
                cur = info(core.home)["name"]
                names = [r["name"] for r in list_profiles(base, cur)]
                n = spoken_match(a.name, names)       # campaign 20 Oct: "דנה" said aloud is the profile "dana"
                if n is None:
                    return ToolResult(False, f"אין פרופיל בשם {a.name}. יש: {', '.join(names)}.", {
                        "profiles": names, "rule": "If the user meant one of these, call profile_switch again with "
                                                   "that exact name. Never offer to create a profile that is listed."},
                        "PROFILE_UNKNOWN")
                if n == cur:
                    return ToolResult(True, f"אנחנו כבר בפרופיל {n}.", {"current": cur}, "PROFILE_ALREADY")
                confirm = getattr(core.registry, "confirm", None)
                label = f"{n} ({HE['TEST PROFILE']})" if info(home_for(n, base)).get("test") else n
                prompt = (f"לעבור לפרופיל {label}? ג'ארוויס ייסגר וייפתח בפרופיל הזה. / Switch to profile "
                          f"{n}? JARVES closes and opens in that profile.")
                # on the SCREEN only (design above; campaign 20 Oct: the plain confirm also took a spoken "כן")
                meta = {"voice": False, "risk": "medium"} if getattr(confirm, "accepts_meta", False) else {}
                ok = await confirm(prompt, **meta) if confirm else False
                if not ok:
                    logs.info("profiles", "PROFILE_SWITCH_DECLINED", "profile switch not approved on the screen",
                              profile=n)
                    return ToolResult(False, "לא עברתי. מעבר פרופיל צריך אישור על המסך.", {"profile": n},
                                      "PROFILE_SWITCH_NOT_APPROVED")
                use(n, base)
                SPAWN(n, ["run", "--background", "--show"])
                logs.info("profiles", "PROFILE_SWITCH", "switching profile", frm=cur, to=n)
                import asyncio

                from .runtime import _quit_after_goodbye
                asyncio.get_running_loop().create_task(_quit_after_goodbye(core))
                return ToolResult(True, f"עובר לפרופיל {n}. להתראות מהפרופיל הזה.", {"from": cur, "to": n},
                                  "PROFILE_SWITCHING")

            return [
                Tool("profile_list", "List JARVES's profiles (users and the test profile) and which one is current.",
                     list_, NoArgs, permission="read"),
                Tool("profile_switch", "Switch to another profile when the user explicitly asks (\"תעבור לפרופיל של "
                     "דנה\", \"switch to the test profile\"). The user approves on the screen; JARVES then restarts "
                     "in that profile. Never guess who is speaking.", switch, SwitchArgs, permission="action",
                     timeout_s=60),
            ]

    return ProfileSkill()


ALLOWED_ACTIONS = frozenset({"profile_switch"})


def attach(core) -> None:
    core.registry.register(make_skill(core))
    core.registry.allowed_actions |= ALLOWED_ACTIONS


# ---------------------------------------------------------------------- dashboard
def view(home: Path) -> dict:
    me = info(home)
    return {"current": me["name"], "kind": me["kind"], "test": me["test"],
            "banner": {"he": "פרופיל בדיקה · לא הנתונים האמיתיים שלך", "en": "TEST PROFILE · not your real data"}
            if me["test"] else None,
            "profiles": list_profiles(base_of(home), me["name"]), "never_copied": list(NEVER_COPIED),
            "commands": {"list": "jarvis profile list", "create": "jarvis profile create <name> [--from <name> "
                         "--copy-memory]", "use": "jarvis profile use <name>", "run": "jarvis --profile <name>",
                         "delete": "jarvis profile delete <name>", "export": "jarvis profile export <name>"}}


def _local_only(ctx):
    who = getattr(ctx, "principal", None)
    return who is None or who.get("kind") == "local"


def api_profiles(ctx, m, body):
    return 200, view(ctx.home)


def api_profile_create(ctx, m, body):
    if not _local_only(ctx):
        return 403, {"error": "profiles are managed only on this computer"}
    b = body or {}
    if b.get("copy_secrets"):
        return 400, {"error": "keys are never copied from the dashboard; use `jarvis profile create <name> --from "
                              "<name> --copy-secrets` if you really want that"}
    try:
        made = create(str(b.get("name", "")), from_=(str(b["from"]) if b.get("from") else None),
                      copy_memory=b.get("copy_memory") is True,
                      test=(b["test"] if isinstance(b.get("test"), bool) else None), base=base_of(ctx.home),
                      by="dashboard")
    except ProfileError as e:
        return 400, {"error": str(e)}
    made.pop("home", None)                  # the dashboard shows names, not another profile's folder
    return 200, {"ok": True, "profile": made, **view(ctx.home)}


def api_profile_delete(ctx, m, body):
    if not _local_only(ctx):
        return 403, {"error": "profiles are managed only on this computer"}
    name = m.group(1)
    if info(ctx.home)["name"] == name:
        return 409, {"error": "this dashboard belongs to that profile; delete it from another profile or the CLI"}
    try:
        delete(name, str((body or {}).get("confirm", "")), base=base_of(ctx.home), by="dashboard")
    except ProfileError as e:
        return 400, {"error": str(e)}
    return 200, {"ok": True, **view(ctx.home)}


def api_profile_use(ctx, m, body):
    if not _local_only(ctx):
        return 403, {"error": "profiles are managed only on this computer"}
    try:
        use(m.group(1), base_of(ctx.home))
    except ProfileError as e:
        return 400, {"error": str(e)}
    return 200, {"ok": True, **view(ctx.home)}


def api_profile_open(ctx, m, body):
    """Open another profile's own dashboard (its own process, port and login): this one never reads its files."""
    if not _local_only(ctx):
        return 403, {"error": "profiles are managed only on this computer"}
    try:
        n = valid_name(m.group(1))
    except ProfileError as e:
        return 400, {"error": str(e)}
    if not exists(n, base_of(ctx.home)):
        return 404, {"error": f"there is no profile {n!r}"}
    SPAWN(n, ["dashboard"])
    return 200, {"ok": True, "opening": n, "note": "its dashboard opens in a new browser tab"}


def dashboard_routes(register) -> None:
    register("GET", r"/api/profiles", api_profiles)
    register("POST", r"/api/profiles", api_profile_create)
    register("POST", r"/api/profiles/([a-z0-9_-]{1,24})/delete", api_profile_delete)
    register("POST", r"/api/profiles/([a-z0-9_-]{1,24})/use", api_profile_use)
    register("POST", r"/api/profiles/([a-z0-9_-]{1,24})/open", api_profile_open)


# ---------------------------------------------------------------------- CLI
def add_cli(sub) -> None:
    p = sub.add_parser("profile", help="profiles: several users + a test profile (list / create / use / delete / "
                                       "export / show); run one with  jarvis --profile <name>")
    ps = p.add_subparsers(dest="action", required=True)
    ps.add_parser("list", help="every profile, its kind, link port and whether it runs")
    ps.add_parser("show", help="the profile this command uses")
    c = ps.add_parser("create", help="new profile (the name 'test' makes the offline test profile)")
    c.add_argument("name")
    c.add_argument("--from", dest="from_", metavar="NAME", help="copy settings, persona and switches from a profile")
    c.add_argument("--copy-memory", action="store_true", help="with --from: also copy memories, USER.md and MEMORY.md")
    c.add_argument("--copy-secrets", action="store_true", help="with --from: also copy secrets.env (API keys); "
                                                              "Google / ChatGPT sign-ins are never copied")
    c.add_argument("--test", action="store_true", help="make it a test profile (offline defaults, TEST banner)")
    u = ps.add_parser("use", help="make a profile the one `jarvis` uses from now on")
    u.add_argument("name")
    d = ps.add_parser("delete", help="delete a profile and all its data (asks you to type its name)")
    d.add_argument("name")
    d.add_argument("--confirm", metavar="NAME", help="non-interactive: the profile name typed back")
    e = ps.add_parser("export", help="a verified backup zip of one profile (no keys or sign-ins in it)")
    e.add_argument("name")
    e.add_argument("--out", type=Path, help="where to write the zip (default: that profile's backups folder)")


def cmd_profile(a, cfg, home) -> int:
    try:
        if a.action == "list":
            for r in list_profiles():
                print(f"{'*' if r['current'] else ' '} {r['name']:<24} {'TEST' if r['test'] else 'normal':<7} "
                      f"port {r['port']:<6} {'running' if r['running'] else 'stopped':<8}"
                      + ("  (active: `jarvis` uses it)" if r["active"] else ""))
            print("* = this command's profile.  Run another:  jarvis --profile <name>   Make it the default:  "
                  "jarvis profile use <name>")
            return 0
        if a.action == "show":
            i = info(home)
            print(f"profile {i['name']} ({'TEST profile' if i['test'] else i['kind']}), port {link_port(home)}, "
                  f"data in {home}")
            return 0
        if a.action == "create":
            r = create(a.name, from_=a.from_, copy_memory=a.copy_memory, copy_secrets=a.copy_secrets,
                       test=True if a.test else None)
            print(f"created profile {r['name']} ({'TEST: offline defaults, separate budget' if r['test'] else 'normal'})"
                  f", link port {r['port']}\n  data: {r['home']}")
            if r["copied"]:
                print("  copied: " + ", ".join(r["copied"]))
            print("  never copied: " + ", ".join(NEVER_COPIED))
            print(f"  start it:  jarvis --profile {r['name']}     set it up:  jarvis --profile {r['name']} setup")
            return 0
        if a.action == "use":
            n = use(a.name)
            print(f"`jarvis` now uses profile {n}. (Back: jarvis profile use default)")
            return 0
        if a.action == "delete":
            typed = a.confirm
            if typed is None:
                print(f"This deletes profile {a.name} with ALL its memory, conversations, settings, keys and sign-ins. "
                      "It cannot be undone (export it first: jarvis profile export " + a.name + ").")
                try:
                    typed = input(f"Type the profile name ({a.name}) to delete it: ")
                except EOFError:
                    typed = ""
            delete(a.name, typed)
            print(f"deleted profile {a.name}")
            return 0
        if a.action == "export":
            out = export(a.name, a.out)
            print(f"wrote {out} (verified backup archive; no keys or sign-ins). Restore: "
                  f"jarvis --profile {valid_name(a.name)} restore \"{out}\"")
            return 0
    except ProfileError as e:
        print(str(e), file=sys.stderr)
        return 2
    return 2
