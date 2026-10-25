"""Phone microphone / cloud-audio indicator data (V5 gap G5, issue #185).

What the phone shows is TRUE state, never a request:
- the PHONE's own microphone is known only to the phone page (it opens the mic on a press and closes it on release);
  the page shows it at once, in words and with a Stop button.
- the COMPUTER's state comes from `runtime.privacy_state.derive` (the same source as the face and the dashboard strip):
  microphone state and whether microphone audio is being streamed to the cloud voice model right now.
- audio recorded on the phone goes to the computer only and is transcribed there by the local speech model (push to
  talk); it is never sent to the cloud by the phone path and never stored. This is a fixed fact of the V5 phone path,
  stated so the owner can see it (`phone.cloud` is always False).
When the computer's state cannot be read the answer is `unknown`, and the page says so (never a calm "off"). The reply carries `ts` (server time); the page also shows `unknown` after ~8 s without a fresh reading.
"""
from __future__ import annotations

import time

from . import logs

PHONE = {"route": "computer_local", "cloud": False, "stored": False}


def view(mob) -> dict:
    fn = getattr(mob.bridge, "audio", None) if mob.bridge is not None else None
    comp: dict | None = None
    if fn is not None:
        try:
            comp = fn() or None
        except Exception as e:
            logs.warn("mobile", "MOBILE_AUDIO_STATE_FAILED", "the audio state could not be read", error_type=type(e).__name__)
            comp = None
    if comp is None:
        comp = {"known": False, "mic": "unknown", "cloud": "unknown", "cloud_on": None}
    return {"phone": dict(PHONE, voice=bool(mob.bridge and mob.bridge.voice_available() and mob.scheme == "https")),
            "computer": comp, "ts": time.time()}


def from_privacy_state(st: dict) -> dict:
    """Reduce the privacy.state message to what the phone page needs (no camera, no wake detail)."""
    mic = (st.get("mic") or {}).get("state", "unknown")
    cloud = st.get("cloud_audio") or {}
    return {"known": True, "mic": mic, "cloud": cloud.get("state", "unknown"), "cloud_on": bool(cloud.get("on"))}
