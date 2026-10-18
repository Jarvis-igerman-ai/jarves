"""Configuration: one small TOML file + environment secrets. Unknown keys and secrets in the file are rejected."""
from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class ConfigError(Exception):
    """Raised for an unreadable or invalid config; message says what to fix."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LinkConfig(_Strict):
    host: str = "127.0.0.1"
    port: int = Field(8765, ge=1024, le=65535)

    @field_validator("host")
    @classmethod
    def _loopback_only(cls, v: str) -> str:
        # SW-V1 policy: no LAN/remote listener until a secured admin surface is approved.
        if v not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("link.host must be a loopback address (127.0.0.1); LAN access is not approved yet")
        return v


class AIConfig(_Strict):
    # Model IDs checked 2026-10-18 at developers.openai.com/api/docs/models/all (GPT-6 = current flagship family)
    provider: Literal["openai", "offline"] = "openai"
    text_model: str = "gpt-6-luna"          # backend for typed chat and GPT-Live delegation; "gpt-6.1-sol" = smarter
    web_search: bool = True
    max_turns: int = Field(6, ge=1, le=20)
    timeout_s: float = Field(30, gt=0, le=120)
    daily_request_limit: int = Field(200, ge=0)   # hard cost ceiling: model requests per local day (0 = AI off)
    daily_realtime_minutes: int = Field(30, ge=0)  # hard cost ceiling for live voice minutes per local day


class VoiceConfig(_Strict):
    # live = OpenAI GPT-Live (best, full duplex); realtime = gpt-realtime-2.1 via Agents SDK (alternative);
    # push_to_talk = offline fallback (local STT). Without OPENAI_API_KEY the Core uses push_to_talk.
    mode: Literal["live", "realtime", "push_to_talk", "off"] = "live"
    language: str = "he"
    live_model: str = "gpt-live-1"
    realtime_model: str = "gpt-realtime-2.1"
    speech_model: str = "gpt-realtime-2.1-mini"   # speaks typed replies (OpenAI's TTS successor)
    voice: str = "marin"
    half_duplex: bool = True               # mic gated while JARVES speaks (laptop speakers); False with headphones/AEC mic
    session_max_s: int = Field(600, ge=30, le=3600)
    tts_engine: Literal["auto", "openai", "local", "none"] = "auto"
    # offline fallback STT (PC: ivrit-ai Hebrew Whisper; Pi: choose a smaller model, e.g. "small")
    stt_model: str = "ivrit-ai/whisper-large-v3-turbo-ct2"
    stt_fallback_model: str = "small"
    stt_compute: str = "int8"
    tts_voice_hint: str = "he-IL"
    record_max_s: float = Field(15, gt=0, le=60)
    input_device: int | str | None = None
    output_device: int | str | None = None


class MusicConfig(_Strict):
    provider: Literal["pear", "none"] = "pear"
    pear_url: str = "http://127.0.0.1:26538"
    timeout_s: float = Field(5, gt=0, le=30)
    max_volume: int = Field(70, ge=0, le=100)


class LogConfig(_Strict):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    max_bytes: int = Field(1_000_000, ge=10_000, le=50_000_000)
    backups: int = Field(5, ge=1, le=50)
    debug_minutes: int = Field(30, ge=1, le=240)


class Config(_Strict):
    assistant_name: str = "JARVES"
    link: LinkConfig = LinkConfig()
    ai: AIConfig = AIConfig()
    voice: VoiceConfig = VoiceConfig()
    music: MusicConfig = MusicConfig()
    log: LogConfig = LogConfig()


SECRET_KEYS = ("api_key", "apikey", "token", "secret", "password", "cookie")


def state_dir() -> Path:
    """Per-user data dir (logs, memory db, link token). Override with JARVES_HOME."""
    if os.environ.get("JARVES_HOME"):
        p = Path(os.environ["JARVES_HOME"])
    elif sys.platform == "win32":
        p = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "jarves"
    else:
        p = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "jarves"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _find_secrets(d: dict, prefix: str = "") -> list[str]:
    out = []
    for k, v in d.items():
        if isinstance(v, dict):
            out += _find_secrets(v, f"{prefix}{k}.")
        elif any(s in k.lower() for s in SECRET_KEYS):
            out.append(prefix + k)
    return out


def load_config(path: Path | None = None) -> tuple[Config, Path]:
    path = path or state_dir() / "config.toml"
    if not path.exists():
        return Config(), path
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"cannot read {path}: {e}. Fix the TOML syntax or delete the file to use defaults.") from e
    bad = _find_secrets(raw)
    if bad:
        raise ConfigError(f"{path}: secrets are not allowed in the config file ({', '.join(bad)}). "
                          "Put OPENAI_API_KEY in the environment instead.")
    try:
        return Config.model_validate(raw), path
    except ValidationError as e:
        msgs = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())
        raise ConfigError(f"{path}: invalid settings: {msgs}") from e


def openai_key_present() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())
