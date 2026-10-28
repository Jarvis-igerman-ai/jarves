"""Opt-in recording of a browser pilot session (V7 B5, #400). Default OFF and doubly opt-in: the switch `browser_recording` AND a
"record this session" choice when the owner starts the pilot.

What it records: the pilot page as a WebM video (Playwright screencast, no audio), one file per uninterrupted stretch, in
<home>/browser/recordings/<id>/ with a manifest. It never leaves the computer and is never given to a model.

Privacy rules (the point of this module):
  * Recording is PAUSED (the current file is closed) the moment the page has a visible password, card, one-time-code or similar field,
    and resumes by itself when no such field is left. The check runs several times a second; a fault while checking counts as "sensitive".
  * Pausing also happens while the pilot session itself is paused/stopped.
  * As a second line, the page gets a style that blanks the text of those fields from the first frame (`MASK_CSS`), because a check
    that runs a few times a second can miss the first few frames.
  * Honest limits (shown in the guide): text that is only SHOWN on a page (a card number printed in a receipt) is not a field and is
    recorded; use the pilot on test pages only. The first frames after a field appears are covered by the mask, not by the pause.
  * Retention: recordings older than RETENTION_DAYS are deleted, the total is capped, one recording has a time cap. The owner can
    delete any recording at any time (`jarvis browser recordings delete`).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from . import features as feat
from . import logs
from .calm import targets

FORMAT_LABEL = "WebM video (VP8/VP9), no audio, one file per uninterrupted stretch"
RETENTION_DAYS = 7
MAX_TOTAL_MB = 500
MAX_RECORDING_S = 30 * 60
MAX_SEGMENT_S = 10 * 60
FPS = 10
SIZE = {"width": 1024, "height": 768}
_ID = re.compile(r"^rec-[0-9a-f]{12}$")

# Visible fields that must never be filmed. Matches type=password, autocomplete cc-*/one-time-code/current-password/new-password, and
# name / id / placeholder / aria-label words for cards, codes and secrets (English and Hebrew).
_WORDS = (r"pass(word|code)?|pwd|card|cc-?(num|number|csc|cvc|cvv|exp)|cvv|cvc|csc|otp|one.?time|2fa|mfa|token|secret|\bpin\b|iban|"
          r"ssn|security.?code|verification.?code|סיסמ|כרטיס|אשראי|קוד|תעודת.?זהות")
SENSITIVE_JS = """() => {
  const words = new RegExp(%r, 'i');
  const visible = (e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const seen = (root) => {
    for (const e of root.querySelectorAll('input, textarea')) {
      const t = (e.type || '').toLowerCase();
      if (['hidden', 'checkbox', 'radio', 'submit', 'button', 'reset', 'image', 'file', 'range', 'color'].includes(t)) continue;
      const ac = (e.getAttribute('autocomplete') || '').toLowerCase();
      const label = [e.name, e.id, e.placeholder, e.getAttribute('aria-label')].join(' ');
      if ((t === 'password' || /cc-|one-time-code|current-password|new-password/.test(ac) || words.test(label)) && visible(e)) return true;
    }
    for (const h of root.querySelectorAll('*')) { if (h.shadowRoot && seen(h.shadowRoot)) return true; }
    return false;
  };
  if (seen(document)) return true;
  for (const f of document.querySelectorAll('iframe')) { try { if (f.contentDocument && seen(f.contentDocument)) return true; } catch (e) { return true; } }
  return false;
}""" % _WORDS

MASK_CSS = ("input[type=password], input[autocomplete^=cc-], input[autocomplete=one-time-code], input[autocomplete=current-password], "
            "input[autocomplete=new-password] { color: transparent !important; text-shadow: none !important; caret-color: transparent !important; "
            "-webkit-text-security: disc !important; background: #000 !important; }")
MASK_INIT_SCRIPT = ("(() => { const add = () => { const s = document.createElement('style'); s.textContent = %s; "
                    "(document.head || document.documentElement).appendChild(s); }; "
                    "if (document.documentElement) add(); else document.addEventListener('DOMContentLoaded', add); })();" % json.dumps(MASK_CSS))


def ffmpeg_ready() -> bool:
    """Playwright records video with its own small ffmpeg build ("playwright install ffmpeg", about 1 MB, no system change). Without it a
    recording cannot start, so the switch says so instead of failing later. PLAYWRIGHT_BROWSERS_PATH=0 (inside the package) is not looked up."""
    base = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    if base == "0":
        return True
    if base:
        d = Path(base)
    elif sys.platform == "win32":
        d = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    elif sys.platform == "darwin":
        d = Path.home() / "Library" / "Caches" / "ms-playwright"
    else:
        d = Path.home() / ".cache" / "ms-playwright"
    try:
        return any(p.is_dir() and any(p.iterdir()) for p in d.glob("ffmpeg-*"))
    except OSError:
        return False


def root(home: Path) -> Path:
    return Path(home) / "browser" / "recordings"


class Recorder:
    """One recording. Driven from the browser worker thread (the only thread that may touch Playwright): `tick(page, active)` a few times a
    second, `finish(page)` when the session ends."""

    def __init__(self, home: Path, *, clock: Callable[[], float] = time.time, rec_id: str | None = None) -> None:
        self.home, self.clock = Path(home), clock
        self.id = rec_id or "rec-" + uuid.uuid4().hex[:12]
        self.dir = root(self.home) / self.id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.started = clock()
        self.segments: list[dict] = []
        self.pauses = 0
        self.state = "waiting"                  # waiting | recording | paused | finished
        self._seg_started = 0.0
        self._rec_s = 0.0                       # seconds actually recorded so far
        self._write()

    # ------------------------------------------------------------------ the manifest (what the owner sees in the list)
    def view(self) -> dict:
        return {"id": self.id, "started": self.started, "state": self.state, "format": FORMAT_LABEL, "pauses": self.pauses,
                "recorded_s": round(self._rec_s + (self.clock() - self._seg_started if self.state == "recording" else 0.0), 1),
                "segments": [dict(s) for s in self.segments]}

    def _write(self) -> None:
        try:
            tmp = self.dir / "manifest.json.tmp"
            tmp.write_text(json.dumps(self.view(), ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.dir / "manifest.json")
        except OSError:
            logs.warn("browser", "BROWSER_RECORDING_MANIFEST_FAILED", "could not write the recording manifest")

    # ------------------------------------------------------------------ segments
    def _start(self, page: Any) -> None:
        name = f"part-{len(self.segments) + 1:03d}.webm"
        page.screencast.start(path=str(self.dir / name), fps=FPS, size=SIZE, quality=60)
        self.segments.append({"file": name, "started": self.clock(), "ended": None, "bytes": 0})
        self._seg_started, self.state = self.clock(), "recording"
        self._write()

    def _stop(self, page: Any, why: str) -> None:
        seg = self.segments[-1]
        try:
            page.screencast.stop()
        except Exception:                                       # noqa: BLE001 - a dead page still ends the stretch
            logs.warn("browser", "BROWSER_RECORDING_STOP_FAILED", "the recording stretch could not be closed cleanly")
        seg["ended"] = self.clock()
        self._rec_s += seg["ended"] - self._seg_started
        try:
            seg["bytes"] = (self.dir / seg["file"]).stat().st_size
        except OSError:
            seg["bytes"] = 0
        if why != "finished":
            self.pauses += 1
        self.state = "paused" if why != "finished" else "finished"
        self._write()

    def tick(self, page: Any, active: bool) -> None:
        """Pause on a sensitive field or an inactive session, resume when it is clear, enforce the time caps."""
        if self.state == "finished":
            return
        try:
            sensitive = bool(page.evaluate(SENSITIVE_JS))
        except Exception:                                       # noqa: BLE001 - cannot look = do not film
            sensitive = True
        want = active and not sensitive
        if self.state == "recording":
            over_total = self._rec_s + (self.clock() - self._seg_started) >= MAX_RECORDING_S
            if not want or over_total or self.clock() - self._seg_started >= MAX_SEGMENT_S:
                self._stop(page, "paused")
                if over_total:
                    self.state = "finished"
                    self._write()
            return
        if want and self._rec_s < MAX_RECORDING_S:
            try:
                self._start(page)
            except Exception:                                   # noqa: BLE001
                self.state = "finished"
                self._write()
                logs.warn("browser", "BROWSER_RECORDING_START_FAILED", "the recording could not start; it is off for this session")

    def finish(self, page: Any) -> dict:
        if self.state == "recording":
            self._stop(page, "finished")
        self.state = "finished"
        self._write()
        return self.view()


# ---------------------------------------------------------------------- the owner's view: list, delete, retention
def list_recordings(home: Path) -> list[dict]:
    out = []
    for d in sorted(root(home).glob("rec-*")) if root(home).exists() else []:
        if not _ID.match(d.name):
            continue
        try:
            m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            m = {"id": d.name, "state": "unknown", "segments": []}
        m["path"] = str(d)
        m["bytes"] = sum(p.stat().st_size for p in d.glob("*") if p.is_file())
        out.append(m)
    return sorted(out, key=lambda m: m.get("started") or 0, reverse=True)


def delete(home: Path, rec_id: str) -> bool:
    if not _ID.match(str(rec_id)):
        return False
    d = root(home) / rec_id
    if not d.is_dir():
        return False
    shutil.rmtree(d, ignore_errors=True)
    logs.info("browser", "BROWSER_RECORDING_DELETED", "a pilot recording was deleted", id=rec_id)
    return not d.exists()


def purge(home: Path, *, clock: Callable[[], float] = time.time, days: int = RETENTION_DAYS, max_mb: int = MAX_TOTAL_MB,
          keep: str = "") -> int:
    """Delete recordings older than [days], then the oldest ones until the total fits [max_mb]. [keep] is the running recording."""
    n = 0
    items = [m for m in list_recordings(home) if m.get("id") != keep]
    for m in items[:]:
        if clock() - float(m.get("started") or 0) > days * 86400:
            n += bool(delete(home, m["id"]))
            items.remove(m)
    total = sum(m["bytes"] for m in items)
    for m in reversed(items):                                  # oldest first
        if total <= max_mb * 1024 * 1024:
            break
        total -= m["bytes"]
        n += bool(delete(home, m["id"]))
    return n


FEATURE = "browser_recording"


def _probe(c: feat.ProbeCtx) -> tuple[bool, str]:
    if not ffmpeg_ready():
        return False, "video needs Playwright's small ffmpeg: python -m playwright install ffmpeg"
    return True, ""


feat.register(FEATURE, "הקלטת וידאו של הפיילוט", "Record the browser pilot (video)", False, probe=_probe,
              needs=["browser_pilot"], risk="high", stage="beta",
              about="optionally record a pilot session on a local test page as a WebM video kept on this computer; recording pauses on password, card "
                    "and code fields; old recordings are deleted after 7 days; you can delete any of them",
              targets=targets("test_browser_recording.py"),
              permissions=["screen of the pilot page is saved to a file"], settings="jarvis features on browser_recording")
