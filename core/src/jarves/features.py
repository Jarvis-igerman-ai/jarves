"""Feature switches: ONE source of truth for what JARVES may do (music, web search, camera, Gmail...).

Every feature has two independent states:
- ENABLED   the user's switch. Persisted in <state_dir>/features.json (atomic write); unset = the legacy config.toml
            value where one exists (voice.greeting, ai.web_search...), else the feature's default. Privacy-sensitive
            features (camera, face recognition, camera when away, sending e-mail) default OFF.
- AVAILABLE a probe: is the hardware / credential / service actually there (no camera, no Google token, Pear not
            running, no API key...). Never stored; re-checked at most every PROBE_TTL_S seconds.
`active(id)` = enabled and available. The Skill registry hides the tools of inactive features (`gate`) and refuses
their calls (FEATURE_OFF); TOOLS.md and the voice instructions list what is off and why, so JARVES can say so
truthfully ("אין לי מצלמה מחוברת").

Who may switch what (enforced here, never by prompt text):
- the user (CLI `jarvis features`, the dashboard Features page): everything.
- the model (voice tool `feature_set`): OFF always; ON for low-risk features; medium/high-risk ON only after the user
  approves on the screen (registry.confirm); without a screen it stays off and a request shows on the dashboard.

When a change takes effect (`applies`): "live" = the next tool list / turn; "next_session" = the next conversation
(GPT-Live fixes tools/hosted search per session); "restart" = the next Core start.

Interface for other packages (stable):
    from jarves import features
    features.register(id, title_he, title_en, default_enabled, probe=fn, needs=[...], risk="low", skills=[...],
                      tools=[...], config="voice.greeting", applies="live", about="...")
    features.current().active("music")         # the Core's registry (or a default one for tools/tests)
    f = features.Features(cfg, home, components=lambda: {...})   # own instance (dashboard, CLI, tests)
    f.enabled(id) / f.available(id) -> (bool, reason) / f.active(id) / f.set(id, on, by="user") / f.list()
    f.gate(skill_name, tool_name) -> None | reason      # SkillRegistry.gate
    f.capabilities_markdown()                           # the "Features" section of TOOLS.md
    dashboard_routes(register)                          # GET /api/features, POST /api/features/<id>
"""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal
from urllib.parse import urlsplit

from . import logs

Risk = Literal["low", "medium", "high"]
Applies = Literal["live", "next_session", "restart"]
RISKS = ("low", "medium", "high")
PROBE_TTL_S = 15.0
STATE_FILE = "features.json"


@dataclass
class ProbeCtx:
    """What a probe may look at: the config, the data dir and the Core's live health components (the dashboard and
    CLI read them from health.json). Probes must be cheap and never ask the network beyond localhost."""
    cfg: Any
    home: Path | None
    components: Callable[[], dict]
    skills: Callable[[], set[str] | None]      # names of registered Skills (None = unknown, e.g. dashboard)

    def component(self, name: str) -> dict:
        try:
            return (self.components() or {}).get(name) or {}
        except Exception:
            return {}


Probe = Callable[[ProbeCtx], tuple[bool, str]]


def _always(_: ProbeCtx) -> tuple[bool, str]:
    return True, ""


@dataclass
class Feature:
    id: str
    title_he: str
    title_en: str
    default_enabled: bool
    probe: Probe = _always
    needs: list[str] = field(default_factory=list)     # other feature ids (must be active) or resources (info)
    risk: Risk = "low"
    skills: list[str] = field(default_factory=list)    # whole Skills owned by this feature
    tools: list[str] = field(default_factory=list)     # single tools owned by this feature
    config: str | None = None                          # legacy config.toml key this switch replaces
    applies: Applies = "live"
    about: str = ""


CATALOG: dict[str, Feature] = {}


def register(id: str, title_he: str, title_en: str, default_enabled: bool, *, probe: Probe | None = None,
             needs: list[str] | None = None, risk: Risk = "low", skills: list[str] | None = None,
             tools: list[str] | None = None, config: str | None = None, applies: Applies = "live",
             about: str = "") -> Feature:
    """Add (or replace) a feature in the catalog every Features instance uses."""
    if risk not in RISKS:
        raise ValueError(f"risk must be one of {RISKS}")
    f = Feature(id, title_he, title_en, bool(default_enabled), probe or _always, list(needs or []), risk,
                list(skills or []), list(tools or []), config, applies, about)
    CATALOG[id] = f
    return f


# ---------------------------------------------------------------------- probes
def _api_billing(c: ProbeCtx) -> tuple[bool, str]:
    from .config import api_billing_allowed
    ai = getattr(c.cfg, "ai", None)
    if ai is None:
        return False, "no configuration"
    if ai.provider != "openai":
        return False, "AI is offline (ai.provider = offline)"
    if ai.connection == "chatgpt":
        return False, "needs the OpenAI API key (connection = ChatGPT plan only)"
    return (True, "") if api_billing_allowed(ai) else (False, "no OpenAI API key (run `jarvis key`)")


def _ai_online(c: ProbeCtx) -> tuple[bool, str]:
    from .config import openai_key_present
    ai = getattr(c.cfg, "ai", None)
    if ai is None or ai.provider != "openai":
        return False, "AI is offline (ai.provider = offline)"
    if ai.connection == "api_key" and not openai_key_present():
        return False, "no OpenAI API key (run `jarvis key`)"
    return True, ""


def _tcp_open(url: str, timeout: float = 0.3) -> bool:
    u = urlsplit(url)
    try:
        with socket.create_connection((u.hostname or "127.0.0.1", u.port or 80), timeout=timeout):
            return True
    except OSError:
        return False


def _probe_music(c: ProbeCtx) -> tuple[bool, str]:
    m = getattr(c.cfg, "music", None)
    if m is None:
        return False, "no configuration"
    comp = c.component("music")
    if comp.get("status") in ("ok", "degraded"):
        return True, ""
    # music.provider = "none" in config.toml only sets the switch's default; Pear is the only player.
    if _tcp_open(m.pear_url):
        return True, ""
    return False, "Pear Desktop is not running (start it with the API Server plugin)"


def _probe_wake(c: ProbeCtx) -> tuple[bool, str]:
    v = getattr(c.cfg, "voice", None)
    if v is None or v.mode == "off":
        return False, "voice is off (voice.mode = off)"
    comp = c.component("wake_word")
    if comp.get("status") == "unavailable":
        return False, comp.get("detail") or "wake word detector unavailable"
    if comp.get("status") == "ok":
        return True, ""
    if importlib.util.find_spec("openwakeword") is None and importlib.util.find_spec("pyopen_wakeword") is None:
        return False, "wake-word package not installed (uv sync --extra voice)"
    return _probe_mic(c)


def _probe_mic(c: ProbeCtx) -> tuple[bool, str]:
    for name in ("audio", "mic"):
        comp = c.component(name)
        if comp.get("status") == "unavailable":
            return False, comp.get("detail") or "no microphone"
    return True, ""


def _probe_speaker(c: ProbeCtx) -> tuple[bool, str]:
    comp = c.component("audio")
    if comp.get("status") == "unavailable":
        return False, comp.get("detail") or "no speaker"
    return True, ""


def _probe_live_voice(c: ProbeCtx) -> tuple[bool, str]:
    v = getattr(c.cfg, "voice", None)
    if v is None or v.mode not in ("live", "realtime"):
        return False, "voices can only be changed with live cloud voice (voice.mode = live)"
    return _api_billing(c)


def _probe_camera(c: ProbeCtx) -> tuple[bool, str]:
    comp = c.component("camera")                 # a future camera Skill reports here
    if comp.get("status") in ("ok", "degraded"):
        return True, ""
    if comp.get("status") == "unavailable":
        return False, comp.get("detail") or "no camera connected"
    if sys.platform.startswith("linux") and any(Path("/dev").glob("video*")):
        return True, ""
    return False, "no camera connected"


def _skill_installed(*names: str) -> Probe:
    def probe(c: ProbeCtx) -> tuple[bool, str]:
        have = c.skills()
        if have is not None and not any(n in have for n in names):
            return False, "not installed yet (no connector in this version)"
        return True, ""
    return probe


def _google(scope: str, *skill_names: str) -> Probe:
    installed = _skill_installed(*skill_names)

    def probe(c: ProbeCtx) -> tuple[bool, str]:
        if c.home is None or not any((c.home / n).is_file() for n in ("google_token.json", "google_oauth.json")):
            return False, f"Google account not connected ({scope})"
        return installed(c)
    return probe


def _all(*probes: Probe) -> Probe:
    def probe(c: ProbeCtx) -> tuple[bool, str]:
        for p in probes:
            ok, why = p(c)
            if not ok:
                return ok, why
        return True, ""
    return probe


# ---------------------------------------------------------------------- built-in catalog
register("music", "מוזיקה", "Music", True, probe=_probe_music, needs=["pear_desktop"], skills=["music"],
         config="music.provider", about="play / pause / volume in Pear Desktop (YouTube Music)")
register("web_search", "חיפוש באינטרנט", "Web search", True, probe=_ai_online, needs=["openai"],
         config="ai.web_search", applies="next_session", about="answers with fresh facts from the web")
register("background_tasks", "משימות ברקע", "Background tasks", True, probe=_api_billing, needs=["openai_api"],
         skills=["tasks"], config="ai.tasks_enabled", about="longer research runs while the conversation goes on")
register("memory_curator", "למידה מהשיחות", "Learn from conversations", True,
         config="settings:curator.auto", about="keeps durable facts after a conversation (local rules, no network)")
register("memory_llm_pass", "למידה בעזרת המודל", "Memory LLM pass", False, probe=_api_billing,
         needs=["memory_curator", "openai_api"], risk="medium", config="settings:curator.llm",
         about="also asks the backend model to extract facts after a conversation (one paid request)")
register("diagnosis_llm_pass", "אבחון עצמי בעזרת המודל", "Self-diagnosis LLM pass", False, probe=_api_billing,
         needs=["openai_api"], risk="medium", about="lets self-diagnosis ask the model about odd conversations")
register("google_calendar", "יומן גוגל", "Google Calendar", True, probe=_google("calendar", "calendar"),
         needs=["google_account"], risk="medium", skills=["calendar"], about="read and add calendar events")
register("gmail_read", "קריאת מיילים", "Read Gmail", True, probe=_google("gmail", "gmail"),
         needs=["google_account"], risk="medium", tools=["gmail_search", "gmail_read"], about="read your e-mail")
register("gmail_send", "שליחת מיילים", "Send Gmail", False, probe=_google("gmail", "gmail"),
         needs=["google_account", "gmail_read"], risk="high", tools=["gmail_send", "gmail_draft"],
         about="send e-mail in your name")
register("camera", "מצלמה", "Camera", False, probe=_probe_camera, needs=["camera_device"], risk="high",
         skills=["camera"], about="look through the camera when asked")
register("face_recognition", "זיהוי פנים", "Face recognition", False, probe=_all(_probe_camera, _skill_installed("faces")),
         needs=["camera"], risk="high", skills=["faces"], about="recognise who is in the room")
register("camera_when_away", "מצלמה כשאין אף אחד בבית", "Camera when away", False,
         probe=_all(_probe_camera, _skill_installed("camera")), needs=["camera"], risk="high",
         about="watch the room while nobody is home")
register("wake_word", "מילת הפעלה", "Wake word \"Hey Jarvis\"", True, probe=_probe_wake, needs=["microphone"],
         config="voice.wake_enabled", about="listens on this device for \"Hey Jarvis\"; no audio leaves before it")
register("greeting", "ברכת פתיחה", "Greeting", True, config="voice.greeting", about="says hello when the face starts")
register("chimes", "צלילי התראה", "Chimes", True, probe=_probe_speaker, needs=["speaker"], config="voice.chime",
         about="soft sound when \"Hey Jarvis\" is heard and when the conversation is ready")
register("dashboard", "לוח בקרה", "Dashboard", True, applies="restart",
         about="local web dashboard (`jarvis dashboard`)")
register("workspace_agent_edit", "עריכת האופי והקבצים שלי", "Edit my own persona files", True, risk="medium",
         tools=["workspace_edit", "workspace_undo"],
         about="change SOUL / IDENTITY / USER / MEMORY when you ask (always asks you first)")
register("voice_change", "החלפת קול", "Voice change", True, probe=_probe_live_voice, tools=["set_voice"],
         about="change the speaking voice when you ask (\"תחליף קול\")")

# The voice/CLI legacy keys that a Features instance mirrors back onto the loaded Config (read live by the Core).
CONFIG_MIRROR = {"voice.wake_enabled": "wake_word", "voice.greeting": "greeting", "voice.chime": "chimes",
                 "ai.web_search": "web_search", "ai.tasks_enabled": "background_tasks"}
# Settings Skill keys (WP-D, memory.db overrides) that are feature switches now.
SETTING_FEATURES = {"curator.auto": "memory_curator", "curator.llm": "memory_llm_pass"}


def legacy_settings(memory) -> dict[str, bool]:
    """Old memory.db `override:curator.*` values, adopted once by Features.import_legacy."""
    out = {}
    for key, fid in SETTING_FEATURES.items():
        try:
            v = memory.get_setting(f"override:{key}")
        except Exception:
            v = None
        if v in ("on", "off"):
            out[fid] = v == "on"
    return out


def _cfg_get(cfg, dotted: str):
    obj = cfg
    for part in dotted.split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    return obj


# ---------------------------------------------------------------------- state
class Features:
    """Feature switches for one data dir. Thread-safe; the file is re-read when another process (CLI, dashboard)
    changed it, so a switch flipped on the dashboard reaches the running Core on its next check."""

    def __init__(self, cfg=None, home: Path | None = None, *, components: Callable[[], dict] | None = None,
                 skills: Callable[[], set[str] | None] | None = None, path: Path | None = None) -> None:
        if cfg is None:
            from .config import Config
            cfg = Config()
        self.cfg, self.home = cfg, home
        self.path = path or (home / STATE_FILE if home is not None else None)
        self.ctx = ProbeCtx(cfg, home, components or (lambda: {}), skills or (lambda: None))
        self._lock = threading.RLock()
        self._state: dict = {"version": 1, "enabled": {}, "changed": {}, "requests": {}}
        self._mtime: float | None = None
        self._checked = 0.0
        self._probe_cache: dict[str, tuple[float, bool, str]] = {}
        self._defaults: dict[str, tuple[bool, str]] = {}
        for fid in CONFIG_MIRROR.values():
            self.default(fid)                   # legacy config values, before anything mirrors onto cfg
        self.listeners: list[Callable[[str, bool], None]] = []   # (id, enabled) after a change by this process
        self._load()

    # --- persistence
    def _load(self) -> None:
        if self.path is None:
            return
        try:
            st = self.path.stat()
        except OSError:
            return
        if st.st_mtime == self._mtime:
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or not isinstance(raw.get("enabled", {}), dict):
                raise ValueError("not a features object")
        except (OSError, ValueError) as e:
            logs.warn("features", "FEATURES_FILE_BAD", f"{self.path.name} unreadable; using defaults",
                      error=type(e).__name__)
            try:
                os.replace(self.path, self.path.with_suffix(".bad.json"))
            except OSError:
                pass
            self._mtime = None
            return
        self._state = {"version": 1, "enabled": {k: bool(v) for k, v in raw.get("enabled", {}).items()},
                       "changed": dict(raw.get("changed") or {}), "requests": dict(raw.get("requests") or {})}
        self._mtime = st.st_mtime

    def _refresh(self) -> None:
        now = time.monotonic()
        if now - self._checked >= 1.0:
            self._checked = now
            before = dict(self._state["enabled"])
            self._load()
            if before != self._state["enabled"]:
                for fid in set(before) | set(self._state["enabled"]):
                    if before.get(fid) != self._state["enabled"].get(fid) and fid in CATALOG:
                        self._notify(fid)

    def poll(self) -> None:
        """Pick up a change written by another process (dashboard, CLI) and tell the listeners."""
        with self._lock:
            self._checked = 0.0
            self._refresh()

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)
        try:
            self._mtime = self.path.stat().st_mtime
        except OSError:
            self._mtime = None

    def _notify(self, fid: str) -> None:
        on = self.enabled(fid)
        for fn in list(self.listeners):
            try:
                fn(fid, on)
            except Exception:
                logs.error("features", "FEATURE_LISTENER_FAILED", f"listener failed for {fid}", exc=True)

    # --- reads
    def feature(self, fid: str) -> Feature:
        if fid not in CATALOG:
            raise KeyError(fid)
        return CATALOG[fid]

    def default(self, fid: str) -> tuple[bool, str]:
        """(value, source) when the user never switched it: the legacy config.toml value, else the default.
        Cached on first use, i.e. before apply_to_config mirrors the switches back onto the same Config."""
        if fid not in self._defaults:
            self._defaults[fid] = self._default(fid)
        return self._defaults[fid]

    def _default(self, fid: str) -> tuple[bool, str]:
        f = self.feature(fid)
        if f.config and self.cfg is not None and not f.config.startswith("settings:"):
            val = _cfg_get(self.cfg, f.config)
            section, _, key = f.config.partition(".")
            src = "config" if key in getattr(getattr(self.cfg, section, None), "model_fields_set", set()) else "default"
            if f.config == "music.provider" and isinstance(val, str):
                return val != "none", src
            if isinstance(val, bool):
                return val, src
        return f.default_enabled, "default"

    def enabled(self, fid: str) -> bool:
        with self._lock:
            self._refresh()
            v = self._state["enabled"].get(fid)
        return self.default(fid)[0] if v is None else v

    def available(self, fid: str) -> tuple[bool, str]:
        f = self.feature(fid)
        now = time.monotonic()
        hit = self._probe_cache.get(fid)
        if hit and now - hit[0] < PROBE_TTL_S:
            ok, why = hit[1], hit[2]
        else:
            try:
                ok, why = f.probe(self.ctx)
            except Exception as e:      # a broken probe means "not available", never a crash
                ok, why = False, f"check failed ({type(e).__name__})"
            self._probe_cache[fid] = (now, bool(ok), str(why or ""))
        if ok:
            for dep in f.needs:
                if dep in CATALOG and dep != fid and not self.active(dep):
                    return False, f"needs {CATALOG[dep].title_en} ({dep}) on"
        return bool(ok), str(why or "")

    def active(self, fid: str) -> bool:
        return fid in CATALOG and self.enabled(fid) and self.available(fid)[0]

    def reason(self, fid: str) -> str:
        """Why a feature is not active ("" when it is)."""
        if not self.enabled(fid):
            return "turned off"
        ok, why = self.available(fid)
        return "" if ok else why

    def invalidate(self, fid: str | None = None) -> None:
        """Forget cached probe results (e.g. after Pear started or a camera was plugged in)."""
        if fid is None:
            self._probe_cache.clear()
        else:
            self._probe_cache.pop(fid, None)

    def status(self, fid: str) -> dict:
        f = self.feature(fid)
        with self._lock:
            self._refresh()
            user = self._state["enabled"].get(fid)
            changed = self._state["changed"].get(fid) or {}
            req = self._state["requests"].get(fid)
        dflt, src = self.default(fid)
        on = dflt if user is None else user
        ok, why = self.available(fid)
        return {"id": fid, "title_he": f.title_he, "title_en": f.title_en, "about": f.about, "risk": f.risk,
                "enabled": on, "default": f.default_enabled, "source": "user" if user is not None else src,
                "available": ok, "reason": why, "active": on and ok, "needs": f.needs, "applies": f.applies,
                "config": f.config, "skills": f.skills, "tools": f.tools,
                "changed_by": changed.get("by"), "changed_at": changed.get("ts"), "requested": req}

    def list(self) -> list[dict]:
        return [self.status(fid) for fid in CATALOG]

    # --- writes
    def set(self, fid: str, on: bool, *, by: str = "user") -> dict:
        """Switch a feature (by = user | cli | dashboard | voice). Policy for the model lives in FeatureSkill."""
        self.feature(fid)
        with self._lock:
            self._load()
            before = self.enabled(fid)
            self._state["enabled"][fid] = bool(on)
            self._state["changed"][fid] = {"by": by, "ts": time.time()}
            if on:
                self._state["requests"].pop(fid, None)
            self._save()
        logs.info("features", "FEATURE_SET", f"{fid} {'on' if on else 'off'}", feature=fid, on=bool(on), by=by)
        if before != bool(on):
            self._notify(fid)
        return self.status(fid)

    def reset(self, fid: str) -> dict:
        """Back to the config.toml / built-in default."""
        self.feature(fid)
        with self._lock:
            self._load()
            before = self.enabled(fid)
            self._state["enabled"].pop(fid, None)
            self._state["changed"].pop(fid, None)
            self._save()
        if before != self.enabled(fid):
            self._notify(fid)
        return self.status(fid)

    def request(self, fid: str, by: str = "voice") -> None:
        """Remember that JARVES was asked to turn a risky feature on; the dashboard shows it for approval."""
        with self._lock:
            self._load()
            self._state["requests"][fid] = {"by": by, "ts": time.time()}
            self._save()

    def import_legacy(self, values: dict[str, bool], by: str = "migration") -> list[str]:
        """Adopt old switches (e.g. memory.db `override:curator.*`) for features the user never set here."""
        done = []
        with self._lock:
            self._load()
            for fid, on in values.items():
                if fid in CATALOG and fid not in self._state["enabled"]:
                    self._state["enabled"][fid] = bool(on)
                    self._state["changed"][fid] = {"by": by, "ts": time.time()}
                    done.append(fid)
            if done:
                self._save()
        return done

    # --- integration
    def owner(self, skill: str, tool: str) -> str | None:
        """The feature that owns a tool (tool-level ownership wins over skill-level)."""
        for f in CATALOG.values():
            if tool in f.tools:
                return f.id
        for f in CATALOG.values():
            if skill in f.skills:
                return f.id
        return None

    def gate(self, skill: str, tool: str) -> str | None:
        """SkillRegistry hook: None = offer/run the tool; else why not."""
        fid = self.owner(skill, tool)
        if fid is None or self.active(fid):
            return None
        f = CATALOG[fid]
        return f"{f.title_en} ({fid}) is {self.reason(fid) or 'off'}"

    def apply_to_config(self, cfg=None) -> None:
        """Mirror the switches onto the loaded Config, so Core code that reads cfg.voice.greeting etc. follows the
        one source of truth (and sees a live change on its next read)."""
        cfg = cfg if cfg is not None else self.cfg
        if cfg is None:
            return
        for key, fid in CONFIG_MIRROR.items():
            section, _, name = key.partition(".")
            sec = getattr(cfg, section, None)
            if sec is not None and hasattr(sec, name):
                setattr(sec, name, self.enabled(fid))

    def unavailable_summary(self) -> list[tuple[Feature, str]]:
        out = []
        for f in CATALOG.values():
            if not self.active(f.id):
                out.append((f, self.reason(f.id)))
        return out

    def capabilities_markdown(self) -> str:
        """The Features section for TOOLS.md and the voice instructions: what is off/unavailable and why."""
        off = self.unavailable_summary()
        on = [f for f in CATALOG.values() if self.active(f.id)]
        lines = ["## Features (switches)",
                 "On: " + (", ".join(f"{f.title_en} ({f.id})" for f in on) or "none") + ".",
                 "Off or not available (say so honestly, e.g. \"אין לי מצלמה מחוברת\"; never pretend):"]
        for f, why in off:
            lines.append(f"- {f.title_he} / {f.title_en} ({f.id}): {why}"
                         + (" [high risk: the user approves on the screen or dashboard]" if f.risk == "high" else ""))
        lines.append("Use features_list / feature_set when the user asks what is on or to switch something.")
        return "\n".join(lines) + "\n"

    def front_note(self, limit: int = 600) -> str:
        """Short line for GPT-Live's own instructions (the voice model answers "do you have a camera?" itself)."""
        off = [f"{f.title_he} ({why})" for f, why in self.unavailable_summary()]
        if not off:
            return ""
        text = "Features that are OFF or not available right now: " + "; ".join(off)
        return text[:limit] + ". Say so honestly if asked; the user can switch features on the dashboard."


# ---------------------------------------------------------------------- the Core's instance
_current: Features | None = None


def attach(f: Features) -> Features:
    global _current
    _current = f
    return f


def current() -> Features:
    """The Core's Features (or a defaults-only instance when no Core attached one, e.g. in tools/tests)."""
    global _current
    if _current is None:
        _current = Features()
    return _current


def active(fid: str) -> bool:
    return current().active(fid)


def detach() -> None:
    global _current
    _current = None


def attach_live(live, features: Features) -> None:
    """Add the off/unavailable features to a LiveVoice's instructions (built at session start)."""
    orig = getattr(live, "session_config", None)
    if orig is None:
        return

    def session_config() -> dict:
        cfg = orig()
        note = features.front_note()
        if note:
            cfg["instructions"] = f"{cfg.get('instructions', '')}\n\n{note}"
        return cfg
    live.session_config = session_config


# ---------------------------------------------------------------------- the voice Skill
def make_skill(features: Features, registry=None):
    """Tools for the model: features_list (read) and feature_set (action; policy above)."""
    from pydantic import BaseModel, Field

    from .skills import Skill, Tool, ToolResult

    class SetArgs(BaseModel):
        id: str = Field(..., description="Feature id from features_list, e.g. music, web_search, camera")
        on: bool = Field(..., description="true = turn on, false = turn off")

    class FeatureSkill(Skill):
        name = "features"
        description = "Which of my features are on, off or unavailable, and switching them"

        def tools(self) -> list[Tool]:
            async def list_(_):
                items = features.list()
                text = "; ".join(f"{s['id']}: {'on' if s['active'] else ('off' if not s['enabled'] else 'unavailable')}"
                                 + (f" ({s['reason']})" if s["reason"] and not s["active"] else "") for s in items)
                return ToolResult(True, text, {"features": [{k: s[k] for k in (
                    "id", "title_he", "enabled", "available", "active", "reason", "risk")} for s in items]})

            async def set_(a: SetArgs):
                fid = a.id.strip().lower()
                if fid not in CATALOG:
                    return ToolResult(False, f"אין תכונה בשם {a.id}.", {"ids": list(CATALOG)}, "FEATURE_UNKNOWN")
                f = CATALOG[fid]
                if features.enabled(fid) == a.on:
                    st = features.status(fid)
                    note = "" if (not a.on or st["available"]) else f" אבל היא לא זמינה: {st['reason']}"
                    return ToolResult(True, f"{f.title_he} כבר {'פועלת' if a.on else 'כבויה'}.{note}", st)
                if a.on and f.risk in ("medium", "high"):
                    confirm = getattr(registry, "confirm", None)
                    ok = await confirm(f"להפעיל את {f.title_he}? / Turn on {f.title_en}?") if confirm else False
                    if not ok:
                        features.request(fid, by="voice")
                        return ToolResult(False, f"{f.title_he} דורשת אישור על המסך או בלוח הבקרה. לא הפעלתי.",
                                          {"id": fid, "risk": f.risk}, "FEATURE_NEEDS_APPROVAL")
                st = features.set(fid, a.on, by="voice")
                when = {"live": "מעכשיו", "next_session": "מהשיחה הבאה", "restart": "מההפעלה הבאה"}[f.applies]
                msg = f"{'הפעלתי' if a.on else 'כיביתי'} את {f.title_he} ({when})."
                if a.on and not st["available"]:
                    msg += f" אבל היא עדיין לא זמינה: {st['reason']}"
                return ToolResult(True, msg, st)

            return [
                Tool("features_list", "List my features (music, web search, camera, Gmail...): on, off or "
                                      "unavailable, and why.", list_, permission="read"),
                Tool("feature_set", "Turn one feature on or off when the user asks. Risky ones (camera, sending "
                                    "e-mail...) need the user's approval on the screen.", set_, SetArgs,
                     permission="action", timeout_s=30),
            ]

    return FeatureSkill()


# ---------------------------------------------------------------------- dashboard
def _ctx_features(ctx) -> Features:
    f = getattr(ctx, "_features", None)
    if f is None:
        from .health import read_snapshot

        def components() -> dict:
            snap = read_snapshot(ctx.home / "health.json") or {}
            return snap.get("components") or {} if snap.get("age_s", 1e9) < 120 else {}
        f = Features(ctx.cfg, ctx.home, components=components)
        ctx._features = f
    return f


def api_features(ctx, m, body):
    f = _ctx_features(ctx)
    return 200, {"features": f.list(), "note": "Switches apply live where possible; others at the next "
                                               "conversation or Core start (see 'applies')."}


def api_feature_set(ctx, m, body):
    f = _ctx_features(ctx)
    fid = m.group(1)
    if fid not in CATALOG:
        return 404, {"error": "no such feature"}
    body = body or {}
    if body.get("reset"):
        return 200, f.reset(fid)
    if not isinstance(body.get("enabled"), bool):
        return 400, {"error": "body needs {\"enabled\": true|false}"}
    return 200, f.set(fid, body["enabled"], by="dashboard")     # the dashboard IS the user's approval


def dashboard_routes(register_route) -> None:
    register_route("GET", r"/api/features", api_features)
    register_route("POST", r"/api/features/([a-z0-9_]+)", api_feature_set)
