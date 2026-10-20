"""Shared helpers of the V3 P1 channel add-ons (Telegram, WhatsApp handoff, Home Assistant, morning briefing).

Nothing here talks to the network. The add-ons keep secrets in <state_dir>/secrets.env (config.save_secret) and
non-secret settings in <state_dir>/<addon>/*.json (private, atomic write)."""
from __future__ import annotations

import getpass
import json
import os
import secrets
from pathlib import Path

from .capabilities import PI_ROADMAP

_PI_BLOCKER = "plain Python + httpx, no Windows-only code; never run on a Raspberry Pi (no device in the test setup)"


def targets(evidence: str, windows_blocker: str, pi_blocker: str = _PI_BLOCKER) -> dict:
    """Truthful per-platform status for features.register(targets=...): unit tests with a local fake server only."""
    return {"windows": {"status": "implemented_unverified", "evidence": evidence, "blocker": windows_blocker},
            "linux_arm64_pi": {"status": "implemented_unverified", "blocker": pi_blocker, "roadmap_issue": PI_ROADMAP}}


def read_json(path: Path) -> dict:
    try:
        v = json.loads(Path(path).read_text(encoding="utf-8"))
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def write_private_json(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


class StepSkipped(BaseException):
    """The owner typed 's' / 'skip' inside a `jarvis connect` step: abandon that step, continue with the next
    (BaseException so a wizard's own `except Exception` cannot swallow it)."""


def is_skip_word(text: str | None, words: tuple[str, ...] = ("s", "skip")) -> bool:
    return (text or "").strip().lower() in words


def read_secret(io, prompt: str) -> str | None:
    """A token typed by the user: hidden on a real terminal, plain from a scripted ConsoleIO (tests).
    Inside `jarvis connect` (io.skippable) typing s / skip abandons the step before anything is saved."""
    if getattr(io, "lines", None) is None:
        try:
            v = getpass.getpass(prompt)
        except (EOFError, KeyboardInterrupt):
            return None
        if getattr(io, "skippable", False) and is_skip_word(v):
            raise StepSkipped
        return v
    return io.read(prompt)


def yes(answer: str | None, default: bool = False) -> bool:
    a = (answer or "").strip().lower()
    if not a:
        return default
    return a in ("y", "yes", "כן", "כ", "1")
