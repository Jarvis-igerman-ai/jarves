"""Safe editing of an ALLOWLIST of non-secret config.toml values (dashboard Settings page).

Rules: only keys in EDITABLE; every value is coerced to the field's type and the WHOLE file is validated by the
pydantic Config (bounds, choices, no unknown keys, no secrets) before anything is written. The file is edited
line by line, so the user's comments and layout stay; the previous file is backed up (config.toml.bak-<stamp>,
BACKUPS kept) and the new one written atomically (temp file + os.replace). Values apply at the Core's next start.
"""
from __future__ import annotations

import os
import re
import tomllib
import typing
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from .config import Config, ConfigError, _find_secrets

BACKUPS = 10
# key -> (group, plain-language label). Never: keys, links, devices, model routing that changes billing silently.
# On/off switches that are features (greeting, chimes, background tasks, AI review, wake word...) live on the
# dashboard Features page (features.py), not here, so there is one switch per thing.
EDITABLE: dict[str, tuple[str, str]] = {
    "voice.backend_deadline_s": ("Conversation", "Give up on a stuck lookup after (s), then retry once"),
    "voice.idle_disconnect_s": ("Conversation", "End a quiet conversation after (s)"),
    "voice.greeting_delay_s": ("Conversation", "Pause before the hello (s)"),
    "voice.progress_after_s": ("Conversation", "Say \"still checking\" after (s) of a silent lookup (0 = never)"),
    "voice.session_max_s": ("Conversation", "Longest single conversation (s)"),
    "voice.half_duplex": ("Conversation", "Mute the mic while JARVES speaks (laptop speakers)"),
    "voice.wake_threshold": ("Wake word", "Wake-word sensitivity threshold (lower = more sensitive)"),
    "voice.wake_no_speech_s": ("Wake word", "Close a wake with no speech after (s)"),
    "ai.daily_request_limit": ("Cost limits", "AI requests per day (hard ceiling)"),
    "ai.daily_realtime_minutes": ("Cost limits", "Voice minutes per day (hard ceiling)"),
    "ai.voice_service_tier": ("AI", "Backend speed tier for spoken lookups (priority = Fast, 2x price)"),
    "ai.voice_reasoning_effort": ("AI", "Backend reasoning effort for spoken lookups"),
    "ai.task_timeout_s": ("AI", "Background task time limit (s)"),
    "music.max_volume": ("Music", "Maximum music volume (%)"),
    "music.volume_step": ("Music", "Louder / quieter step (%)"),
    "log.privacy": ("Privacy & logs", "Privacy mode: never keep what was said (logs, history)"),
    "log.level": ("Privacy & logs", "Log file level"),
    "history.keep_days": ("Privacy & logs", "Keep conversation history for (days)"),
    "history.llm_review_daily_max": ("Self-diagnosis", "AI reviews per day at most"),
    "history.dead_air_s": ("Self-diagnosis", "Flag silence longer than (s)"),
    "history.stall_s": ("Self-diagnosis", "Flag a request without an answer after (s)"),
    "history.slow_tool_s": ("Self-diagnosis", "Flag a tool slower than (s)"),
    "history.slow_backend_s": ("Self-diagnosis", "Flag a backend answer slower than (s)"),
    "pricing.web_search_per_call": ("Pricing", "Web search price per call (USD)"),
    "pricing.session_alert_usd": ("Pricing", "Flag a conversation costing more than (USD)"),
    "pricing.daily_alert_usd": ("Pricing", "Flag a day costing more than (USD)"),
}


def _field(key: str):
    sec, name = key.split(".", 1)
    model = Config.model_fields[sec].annotation
    return model, model.model_fields[name]


def describe(key: str) -> dict:
    model, f = _field(key)
    ann = f.annotation
    out: dict = {"default": f.default}
    if typing.get_origin(ann) is typing.Literal:
        out.update(type="choice", choices=list(typing.get_args(ann)))
    else:
        out["type"] = {bool: "bool", int: "int", float: "float"}.get(ann, "str")
    for m in f.metadata:
        for attr, k in (("ge", "min"), ("gt", "min"), ("le", "max"), ("lt", "max")):
            if getattr(m, attr, None) is not None:
                out[k] = getattr(m, attr)
    return out


def _read(path: Path) -> tuple[str, dict]:
    if not path.exists():
        return "", {}
    text = path.read_text(encoding="utf-8")
    try:
        return text, tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path} is not valid TOML ({e}); fix it by hand first") from e


def view(path: Path, running: Config) -> list[dict]:
    _, raw = _read(path)
    out = []
    for key, (group, label) in EDITABLE.items():
        sec, name = key.split(".", 1)
        out.append({"key": key, "group": group, "label": label, **describe(key),
                    "file": (raw.get(sec) or {}).get(name), "running": getattr(getattr(running, sec), name),
                    "applies": "next start"})
    return out


def coerce(key: str, value):
    d = describe(key)
    t = d["type"]
    if t == "bool":
        if isinstance(value, bool):
            return value
        if str(value).strip().lower() in ("true", "on", "1", "yes"):
            return True
        if str(value).strip().lower() in ("false", "off", "0", "no"):
            return False
        raise ValueError(f"{key}: expected on/off")
    if t == "int":
        if isinstance(value, float) and not value.is_integer():
            raise ValueError(f"{key}: expected a whole number")
        return int(value)
    if t == "float":
        return float(value)
    return str(value)


def _toml(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


_HEADER = re.compile(r"^\s*\[([A-Za-z0-9_.\-]+)\]\s*(?:#.*)?$")


def edit_text(text: str, updates: dict[str, object]) -> str:
    """Set section.key = value in TOML text, keeping comments; missing keys/sections are added."""
    lines = text.splitlines()
    for key, value in updates.items():
        sec, name = key.split(".", 1)
        new = f"{name} = {_toml(value)}"
        cur, sec_end, sec_found, done = None, None, False, False
        for i, line in enumerate(lines):
            m = _HEADER.match(line)
            if m:
                if cur == sec:
                    sec_end = i
                cur = m.group(1)
                if cur == sec:
                    sec_found, sec_end = True, None
                continue
            if cur == sec and re.match(rf"^\s*{re.escape(name)}\s*=", line):
                old = line.split("=", 1)[1]
                comment = re.search(r"\s+#[^\"']*$", old) if '"' not in old and "'" not in old else None
                lines[i] = new + (comment.group(0) if comment else "")
                done = True
                break
        if done:
            continue
        if sec_found:
            at = sec_end if sec_end is not None else len(lines)
            while at > 0 and not lines[at - 1].strip():
                at -= 1
            lines.insert(at, new)
        else:
            lines += (["", f"[{sec}]"] if lines else [f"[{sec}]"]) + [new]
    return "\n".join(lines) + "\n"


def backups(path: Path) -> list[Path]:
    return sorted(path.parent.glob(path.name + ".bak-*"), reverse=True)


def _write(path: Path, text: str) -> Path | None:
    bak = None
    if path.exists():
        bak = path.with_name(f"{path.name}.bak-{datetime.now():%Y%m%d-%H%M%S-%f}")
        bak.write_bytes(path.read_bytes())
        for old in backups(path)[BACKUPS:]:
            old.unlink(missing_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return bak


def validate_text(path: Path, text: str) -> Config:
    raw = tomllib.loads(text)
    bad = _find_secrets(raw)
    if bad:
        raise ConfigError(f"secrets are not allowed in the config file ({', '.join(bad)})")
    try:
        return Config.model_validate(raw)
    except ValidationError as e:
        raise ConfigError("; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())) from e


def apply(path: Path, updates: dict) -> dict:
    """Validate and save; returns {changed: {key: value}, backup}. Raises ValueError / ConfigError (nothing written)."""
    unknown = [k for k in updates if k not in EDITABLE]
    if unknown:
        raise ValueError(f"not editable here: {', '.join(unknown)}")
    values = {k: coerce(k, v) for k, v in updates.items()}
    text, _ = _read(path)
    new_text = edit_text(text, values)
    cfg = validate_text(path, new_text)
    for k, v in values.items():                     # the edit landed where pydantic reads it
        sec, name = k.split(".", 1)
        if getattr(getattr(cfg, sec), name) != v:
            raise ConfigError(f"{k} could not be written safely; edit config.toml by hand")
    path.parent.mkdir(parents=True, exist_ok=True)
    bak = _write(path, new_text)
    return {"changed": values, "backup": bak.name if bak else None}


def undo(path: Path) -> dict:
    """Bring back the newest backup (validated first); that backup is used up, so repeated undo steps back."""
    baks = backups(path)
    if not baks:
        raise ValueError("no earlier config.toml to go back to")
    text = baks[0].read_text(encoding="utf-8")
    validate_text(path, text)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    baks[0].unlink(missing_ok=True)
    return {"restored": baks[0].name}
