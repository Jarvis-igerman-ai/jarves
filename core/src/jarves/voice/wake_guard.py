"""False-wake self-tuning (issue #17).

The owner's PC logged 39 "conversation opened but nobody spoke" (VOICE_NO_SPEECH) against a wake threshold of
0.15. Every false wake opens a paid voice session. This module keeps a small local log of wake OUTCOMES (a time and
"good" / "false", never audio, never text) and turns the measured false-wake rate into a stricter wake pipeline:

  level 0   fewer than 3 false wakes in 24 h: nothing changes
  level 1+  accept threshold +0.08 per level (cap +0.32), a shorter no-speech window, and from level 2 the local
            speech check ("hey/hi/ok Jarvis" heard by the on-device ASR) is also required for strong scores
            (before, a score >= accept woke without any check)

Good wakes (the user did speak) pull the level back down. The level only depends on the last 24 hours, so a quiet
week undoes it. `jarvis wake test --apply` (a new calibration) clears the log. Pure logic: the clock is injected.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

WINDOW_S = 24 * 3600
HALF_LIFE_S = 3 * 3600      # a false wake older than this counts half, older than 2x only a quarter (decay)
MIN_FALSE = 3               # false wakes in the window before anything changes
STEP = 0.08
MAX_LEVEL = 2               # ceiling: at most +0.16 (it was 4 = +0.32, which shut the owner's real wakes out)
ACCEPT_CEILING = 0.55       # the accept threshold is never pushed above max(its configured value, this)
MAX_ENTRIES = 200
SINGLE_STAGE_MIN = 0.25     # threshold floor when the wake model is the ONLY stage (no local speech check)
SHORT_NO_SPEECH_S = 6.0


def _path(home: Path) -> Path:
    return Path(home) / "wake" / "outcomes.json"


class WakeOutcomeLog:
    def __init__(self, home: Path | None, clock: Callable[[], float] = time.time) -> None:
        self.home, self.clock = home, clock
        self._items: list[tuple[float, str]] = []
        self._load()

    # --- storage (levels of use only: a time and a letter)
    def _load(self) -> None:
        if self.home is None:
            return
        try:
            raw = json.loads(_path(self.home).read_text(encoding="utf-8"))
            self._items = [(float(t), "f" if k == "f" else "g") for t, k in raw][-MAX_ENTRIES:]
        except (OSError, ValueError, TypeError):
            self._items = []

    def _save(self) -> None:
        if self.home is None:
            return
        try:
            p = _path(self.home)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._items[-MAX_ENTRIES:]), encoding="utf-8")
            tmp.replace(p)
        except OSError:
            pass                                    # tuning is a convenience; never break the wake loop

    def _prune(self) -> None:
        cut = self.clock() - WINDOW_S
        self._items = [(t, k) for t, k in self._items if t >= cut][-MAX_ENTRIES:]

    # --- recording
    def record(self, false_wake: bool) -> None:
        self._prune()
        self._items.append((self.clock(), "f" if false_wake else "g"))
        self._save()

    def reset(self) -> None:
        self._items = []
        self._save()

    # --- reading
    def counts(self) -> tuple[int, int]:
        """(good wakes, false wakes) in the last 24 hours."""
        self._prune()
        now = self.clock()
        good = false = 0.0
        for t, k in self._items:
            w = 0.5 ** (max(0.0, now - t) // HALF_LIFE_S)         # decay: old outcomes fade, a quiet spell relaxes it
            if k == "g":
                good += w
            else:
                false += w
        return int(round(good)), int(round(false))

    def level(self) -> int:
        good, false = self.counts()
        if false < MIN_FALSE:
            return 0
        lvl = min(MAX_LEVEL, 1 + (false - MIN_FALSE) // 3)
        if good >= 2 * false:
            lvl -= 1
        return max(0, lvl)

    def bump(self) -> float:
        return round(STEP * self.level(), 2)

    def always_confirm(self) -> bool:
        return self.level() >= 2

    def no_speech_s(self, base: float) -> float:
        """Seconds a wake-started conversation may stay silent before it closes (0 = off stays off)."""
        if base <= 0 or self.level() == 0:
            return base
        return min(base, max(SHORT_NO_SPEECH_S, base / 2))

    def summary(self, threshold: float | None = None) -> dict:
        good, false = self.counts()
        lvl = self.level()
        return {"good_wakes": good, "false_wakes": false, "level": lvl, "bump": self.bump(),
                "always_confirm": self.always_confirm(), "window_h": WINDOW_S // 3600,
                "suggested_threshold": None if threshold is None else suggest_threshold(threshold, false, good)}


def suggest_threshold(current: float, false: int, good: int) -> float | None:
    """A calmer wake_threshold for the measured false-wake count, or None when nothing is needed."""
    if false < MIN_FALSE:
        return None
    lvl = max(0, min(MAX_LEVEL, 1 + (false - MIN_FALSE) // 3) - (1 if good >= 2 * false else 0))
    new = round(min(0.8, max(current, SINGLE_STAGE_MIN) + STEP * max(1, lvl)), 2)
    return new if new > current else None


def advice_text(s: dict, threshold: float) -> str:
    """The plain sentence `jarvis wake test` prints (and the dashboard shows)."""
    if s["false_wakes"] < MIN_FALSE:
        return (f"Last {s['window_h']} h: {s['good_wakes']} wake(s) with speech, {s['false_wakes']} false. "
                "Nothing to fix.")
    out = (f"Last {s['window_h']} h: {s['false_wakes']} false wake(s) (the conversation opened and nobody spoke) "
           f"against {s['good_wakes']} real one(s). JARVES already made itself stricter by +{s['bump']} "
           "(and shortened the wait for a first word).")
    sug = s.get("suggested_threshold")
    if sug:
        out += (f" Threshold {threshold} is too sensitive for this room: run `jarvis wake test --apply` with the "
                f"mic and room as usual, or set voice.wake_threshold = {sug}.")
    return out


def apply_to_detector(det, bump: float, always_confirm: bool, single_stage_min: float = SINGLE_STAGE_MIN) -> dict:
    """Make a running wake pipeline stricter (or restore it). Works on ConfirmingDetector (accept / always_confirm),
    GainDetector and plain detectors (threshold). Returns what the detector now uses."""
    if hasattr(det, "accept"):                               # two-stage
        base = getattr(det, "_guard_base_accept", None)
        if base is None:
            base = det._guard_base_accept = float(det.accept)
        det.accept = round(min(max(base, ACCEPT_CEILING), base + bump), 3)
        det.threshold = det.accept
        det.always_confirm = bool(always_confirm)
        return {"stage": 2, "accept": det.accept, "always_confirm": det.always_confirm}
    if not hasattr(det, "threshold"):
        return {}
    inner = getattr(det, "base", None)
    base = getattr(det, "_guard_base_thr", None)
    if base is None:
        base = det._guard_base_thr = float(det.threshold)
    thr = round(min(max(base, single_stage_min, ACCEPT_CEILING), max(base, single_stage_min) + bump), 3)
    det.threshold = thr
    if inner is not None and hasattr(inner, "threshold"):
        inner.threshold = thr
    return {"stage": 1, "threshold": thr}
