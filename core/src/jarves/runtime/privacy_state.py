"""`privacy.state`: one consolidated, truthful "what is on right now" message for the face, the room window and the
dashboard Home strip (docs/FACE_RUNTIME.md "Privacy strip", docs/DEVICE_CONTRACT.md).

Every field is DERIVED from the real state each time (never from what was requested, never optimistic):
- mic          muted (software mute) | unavailable (no audio device) | listening (a conversation or a push-to-talk
               capture has the mic) | gated (conversation open, the mic is replaced by silence while JARVES speaks)
               | wake_only (only the local "Hey Jarvis" detector hears it; nothing leaves the device) | idle (closed)
- camera       unavailable | disabled (switch off) | off (switch on, sensor closed) | presence (sensor open for local
               presence detection) | preview ("What does he see?" open) | photo (a still is being taken)
- speaker      speaking | quiet | unavailable
- cloud_audio  on = microphone audio is being streamed to the cloud voice model RIGHT NOW;
               state sending | connected (session open, only silence is sent) | connecting | off
When a field cannot be read, the conservative answer wins: a voice session whose gate cannot be read counts as
"listening" + "sending", never as closed.

The publisher polls the state 4x a second and sends `privacy.state` to the face on any change, at once to a newly
connected face, and as a heartbeat every HEARTBEAT_S (the face shows "unknown" when it goes stale). The same dict is in
every `status` snapshot under "privacy" (dashboard /api/runtime).
"""
from __future__ import annotations

import asyncio
import json
import time

from .. import logs

POLL_S = 0.25
HEARTBEAT_S = 5.0
WAKE_NOTE_S = 120.0
VERSION = 1
MIC = ("listening", "gated", "wake_only", "muted", "idle", "unavailable")
CAMERA = ("photo", "preview", "presence", "off", "disabled", "unavailable")
SPEAKER = ("speaking", "quiet", "unavailable")
CLOUD = ("sending", "connected", "connecting", "off")


def _component(core, name: str) -> str:
    try:
        c = core.health.components.get(name)
        return c.status if c is not None else ""
    except Exception:
        return ""


def _gated(live) -> bool:
    """True only when the voice session provably sends silence instead of the microphone."""
    try:
        g = getattr(live, "_gated", None)
        if callable(g):
            return bool(g())
        return bool(getattr(live, "half_duplex", False)) and time.monotonic() < float(
            getattr(live, "_speaking_until", 0.0)) + 0.4
    except Exception:
        return False


def _mic_opened(live) -> bool:
    """GPT-Live sends silence (the physical mic stays closed) until the room is ready (`_presented`)."""
    ev = getattr(live, "_presented", None)
    try:
        return ev is None or bool(ev.is_set())
    except Exception:
        return True


def derive(core) -> dict:
    dev = getattr(core, "dev", None)
    live = getattr(core, "live", None)
    muted = bool(getattr(dev, "mic_muted", False))
    audio = _component(core, "audio")
    connecting = bool(getattr(core, "_voice_connecting", False))
    # --- microphone
    if muted:
        mic = "muted"
    elif audio == "unavailable":
        mic = "unavailable"
    elif live is not None and _mic_opened(live):
        mic = "gated" if _gated(live) else "listening"
    elif getattr(core, "listening", False):
        mic = "listening"
    elif getattr(core, "_wake_mic_active", False):
        mic = "wake_only"
    else:
        mic = "idle"
    # --- cloud audio: only a live/realtime session streams microphone audio (push-to-talk STT is local)
    if live is not None:
        cloud = "sending" if mic == "listening" else "connected"
    elif connecting:
        cloud = "connecting"
    else:
        cloud = "off"
    # --- speaker
    speaking = getattr(dev, "face_state", "") == "speaking"
    try:
        speaking = speaking or (live is not None and time.monotonic() < float(getattr(live, "_speaking_until", 0.0)))
    except Exception:
        pass
    if audio in ("unavailable", "disabled"):          # no device / voice switched off in config
        speaker = "unavailable"
    else:
        speaker = "speaking" if speaking else "quiet"
    # --- camera
    cam = getattr(core, "camera", None)
    cam_state, can_preview, last = "unavailable", False, None
    if cam is not None:
        try:
            cam_state = cam.privacy_state()
            can_preview = cam_state != "unavailable" and not cam.preview_refusal()
            last = cam.last_seen
        except Exception:
            logs.debug("runtime", "PRIVACY_CAMERA_FAILED", "camera state unreadable: shown as unavailable")
            cam_state = "unavailable"
    # --- wake word: is the local detector listening, and the last thing it noticed (shown for 2 minutes):
    # "heard speech, no wake match", "mic too quiet", a near miss, or a wake.
    ws = getattr(core, "_wake_status", None)
    recent = isinstance(ws, dict) and time.time() - float(ws.get("ts", 0)) < WAKE_NOTE_S
    wake = {"state": "listening" if mic == "wake_only" else "off",
            "note": ws.get("kind") if recent else None, "hint": (ws.get("hint") or None) if recent else None,
            "at": ws.get("ts") if recent else None}
    lang = str(getattr(getattr(getattr(core, "cfg", None), "voice", None), "language", "he") or "he")
    return {
        "v": VERSION, "locale": "he" if lang.startswith("he") else "en",
        "mic": {"state": mic, "source": "software", "can_toggle": dev is not None},
        "camera": {"state": cam_state, "can_toggle": cam is not None and cam_state != "unavailable",
                   "preview_available": can_preview, "last_seen": last},
        "speaker": {"state": speaker},
        "cloud_audio": {"on": cloud == "sending", "state": cloud},
        "wake": wake,
        "ts": round(time.time(), 2),
    }


def _key(st: dict) -> str:
    return json.dumps({k: v for k, v in st.items() if k != "ts"}, sort_keys=True, ensure_ascii=False, default=str)


class PrivacyPublisher:
    def __init__(self, core) -> None:
        self.core = core
        self._last = ""
        self._sent_t = -1e9
        self._face = None
        self.sent = 0                       # diagnostics / tests

    def start(self) -> None:
        self.core._bg.append(asyncio.create_task(self._loop()))

    def state(self) -> dict:
        return derive(self.core)

    async def push(self, force: bool = False) -> bool:
        link = getattr(self.core, "link", None)
        if link is None:
            return False
        face = link.face_client_id()
        if face is None:
            self._face = None
            return False
        st = derive(self.core)
        key = _key(st)
        now = time.monotonic()
        if not force and face == self._face and key == self._last and now - self._sent_t < HEARTBEAT_S:
            return False
        if key != self._last:
            logs.verbose("runtime", "PRIVACY_STATE", "privacy state changed", mic_state=st["mic"]["state"],
                         camera=st["camera"]["state"], cloud_audio=st["cloud_audio"]["state"])
        self._face, self._last, self._sent_t = face, key, now
        self.sent += 1
        return await link.send_face("privacy.state", st)

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(POLL_S)
            try:
                await self.push()
            except asyncio.CancelledError:
                raise
            except Exception:
                logs.debug("runtime", "PRIVACY_PUSH_FAILED", "privacy.state not sent")
