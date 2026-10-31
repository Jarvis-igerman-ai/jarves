"""Opt-in recording of a browser pilot session (V7 B5, #400). Default OFF and doubly opt-in: the switch `browser_recording` AND a
"record this session" choice when the owner starts the pilot.

What it records: the pilot page as a WebM video (Playwright screencast, no audio), one file per uninterrupted stretch, in
<home>/browser/recordings/<id>/ with a manifest. It never leaves the computer and is never given to a model.

Privacy rules (the point of this module):
  * Recording is PAUSED (the current file is closed) when the page has a visible password, card, one-time-code or similar field, and
    resumes by itself when no such field is left. The check runs about 3 times a second while the browser is idle AND right before every
    browser action; during one long action it does not run, so there can be a gap of up to that action's length between a field appearing
    and the file closing. A fault while checking, or a frame that cannot be read (cross-origin, sandboxed), counts as "sensitive".
  * Recording is only on while the ASSISTANT is driving (session state AGENT). It is paused for the whole time the owner has taken over
    (HUMAN): what the owner types goes to ordinary fields too, and a video would show it.
  * To cover the gap, the page gets an init script that tags matching fields (also ones added later, open shadow roots included) and a
    style that blanks them in the very frame they appear (`MASK_INIT_SCRIPT`). The check and the mask use the same rule.
  * Honest limits (shown in the guide): text that is only SHOWN on a page (a card number in a receipt) is not a field and is recorded;
    closed shadow roots and contenteditable areas are not recognised; the video shows what the page shows, at 1024x768 and 10 frames
    a second, and is not tamper-proof. Text typed during a takeover can still be filmed after control returns to the assistant if the field
    is not matched as sensitive. Use the pilot on test pages only.
  * Retention: recordings older than RETENTION_DAYS are deleted, the total is capped, one recording has a time cap. The owner can
    delete any recording at any time (`jarvis browser recording-delete ID`).
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

FORMAT_LABEL = "WebM video (VP8/VP9), no audio, 1024x768 at 10 frames a second, one file per uninterrupted stretch; shows page content, not tamper-proof"
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
_SENSITIVE_FN = """
  const words = new RegExp(%r, 'i');
  const skip = ['hidden', 'checkbox', 'radio', 'submit', 'button', 'reset', 'image', 'file', 'range', 'color'];
  const isSensitive = (e) => {
    const t = (e.type || '').toLowerCase();
    if (skip.includes(t)) return false;
    const ac = (e.getAttribute('autocomplete') || '').toLowerCase();
    const label = [e.name, e.id, e.placeholder, e.getAttribute('aria-label')].join(' ');
    return t === 'password' || /cc-|one-time-code|current-password|new-password/.test(ac) || words.test(label);
  };
  const visible = (e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
""" % _WORDS

# Checked from Python a few times a second and before every job: is a sensitive field visible anywhere (open shadow roots and same-origin
# frames included)? A frame that cannot be read (cross-origin, sandboxed) counts as sensitive: nothing is filmed while one is on the page.
SENSITIVE_JS = "() => {" + _SENSITIVE_FN + """
  const seen = (root) => {
    for (const e of root.querySelectorAll('input, textarea')) { if (isSensitive(e) && visible(e)) return true; }
    for (const h of root.querySelectorAll('*')) { if (h.shadowRoot && seen(h.shadowRoot)) return true; }
    for (const f of root.querySelectorAll('iframe')) { let d = null; try { d = f.contentDocument; } catch (e) { return true; } if (!d || seen(d)) return true; }
    return false;
  };
  return seen(document);
}"""

# Runs in every page from the first moment: tags matching fields (also later ones, open shadow roots) so the style blanks them at once, in
# the very frame they appear, not only when the next check runs. Closed shadow roots and contenteditable are NOT covered (see the guide).
MASK_INIT_SCRIPT = "(() => {" + _SENSITIVE_FN + """
  const css = %s;
  const tag = (root) => {
    for (const e of root.querySelectorAll('input, textarea')) { if (isSensitive(e)) e.setAttribute('data-jarves-hide', '1'); }
    for (const h of root.querySelectorAll('*')) { if (h.shadowRoot) watch(h.shadowRoot); }
  };
  const watch = (root) => {
    if (root.__jarvesWatched) return; root.__jarvesWatched = true;
    if (root.nodeType === 11 || root !== document) { const st = document.createElement('style'); st.textContent = css; root.appendChild(st); }
    tag(root);
    new MutationObserver(() => tag(root)).observe(root, { childList: true, subtree: true, attributes: true,
      attributeFilter: ['type', 'name', 'id', 'placeholder', 'autocomplete', 'aria-label'] });
  };
  const start = () => { const st = document.createElement('style'); st.textContent = css; (document.head || document.documentElement).appendChild(st); watch(document); };
  if (document.documentElement) start(); else document.addEventListener('DOMContentLoaded', start);
})();""" % json.dumps("[data-jarves-hide], input[type=password], input[autocomplete^=cc-], input[autocomplete=one-time-code], "
                      "input[autocomplete=current-password], input[autocomplete=new-password] { color: transparent !important; "
                      "text-shadow: none !important; caret-color: transparent !important; -webkit-text-security: disc !important; background: #000 !important; }")
MASK_CSS = "[data-jarves-hide], input[type=password] { color: transparent !important; }"          # kept for the tests: the rule is in the init script


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


RUNNING: set = set()          # ids of recordings being written in this process: retention never deletes them


def _private(d: Path) -> None:
    """Owner-only folder where the OS supports it (Pi/Linux/macOS); on Windows the profile folder is already private to the account."""
    try:
        if sys.platform != "win32":
            d.chmod(0o700)
    except OSError:
        pass


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
        _private(root(self.home))
        _private(self.dir)
        self.started = clock()
        RUNNING.add(self.id)
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
        if self.state == "finished":
            RUNNING.discard(self.id)                              # however it ended, retention may treat it as an ordinary recording again
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

    def close_now(self, page: Any) -> None:
        """Fail closed: whatever went wrong, close the open file at once (nothing more is filmed until a check succeeds)."""
        if self.state == "recording":
            self._stop(page, "paused")

    def finish(self, page: Any) -> dict:
        if self.state == "recording":
            self._stop(page, "finished")
        self.state = "finished"
        RUNNING.discard(self.id)
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
        m["bytes"] = sum(p.stat().st_size for p in d.glob("*") if p.is_file())
        out.append(m)
    return sorted(out, key=lambda m: m.get("started") or 0, reverse=True)


def delete(home: Path, rec_id: str, *, running: str = "") -> bool:
    """Delete one recording. [running] = the id of the recording that is being written right now: it cannot be deleted until it ends."""
    if not _ID.match(str(rec_id)) or rec_id == running or rec_id in RUNNING:
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
    items = [m for m in list_recordings(home) if m.get("id") != keep and m.get("id") not in RUNNING]
    for m in items[:]:
        if clock() - float(m.get("started") or 0) > days * 86400:
            n += bool(delete(home, m["id"], running=keep))
            items.remove(m)
    total = sum(m["bytes"] for m in items)
    for m in reversed(items):                                  # oldest first
        if total <= max_mb * 1024 * 1024:
            break
        total -= m["bytes"]
        n += bool(delete(home, m["id"], running=keep))
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
