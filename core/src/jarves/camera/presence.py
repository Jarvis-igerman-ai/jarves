"""Presence + gaze logic (pure, clock-injected, no camera): detector boxes in, events out.

- arrive: a face in `arrive_hits` consecutive frames (one false detection never wakes the face)
- leave: no face for `leave_s` (the face's gaze returns to its idle look)
- away: nobody seen AND no conversation for `away_after_s` (the service then switches the camera OFF unless
  the `camera_when_away` switch allows it)
- gaze: the largest face's centre -> face.set_gaze {x, y, confidence} in -1..1, mirrored (the camera looks at the
  room, the face looks back), rate-limited to `gaze_hz` and only when it moved by more than `gaze_step`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .detect import Box


@dataclass
class Gaze:
    x: float
    y: float
    confidence: float

    def payload(self) -> dict:
        return {"x": round(self.x, 3), "y": round(self.y, 3), "confidence": round(self.confidence, 2)}


@dataclass
class Update:
    arrived: bool = False
    left: bool = False
    gaze: Gaze | None = None
    faces: int = 0


@dataclass
class PresenceTracker:
    arrive_hits: int = 2
    leave_s: float = 2.0
    min_score: float = 0.6
    min_area: float = 0.004         # ignore tiny far-away faces (fraction of the frame)
    gaze_hz: float = 5.0
    gaze_step: float = 0.04
    mirror: bool = True
    present: bool = False
    last_seen: float = -1e9
    _hits: int = 0
    _last_gaze_t: float = -1e9
    _last_gaze: Gaze | None = field(default=None, repr=False)

    def target(self, b: Box) -> Gaze:
        x = (b.cx - 0.5) * 2.0
        y = (b.cy - 0.5) * 2.0
        if self.mirror:
            x = -x
        clamp = lambda v: max(-1.0, min(1.0, v))  # noqa: E731
        return Gaze(clamp(x), clamp(y), max(0.0, min(1.0, b.score)))

    def feed(self, boxes: list[Box], now: float) -> Update:
        faces = [b for b in boxes if b.score >= self.min_score and b.area >= self.min_area]
        up = Update(faces=len(faces))
        if faces:
            self._hits += 1
            self.last_seen = now
            if not self.present and self._hits >= self.arrive_hits:
                self.present, up.arrived = True, True
            if self.present:
                g = self.target(max(faces, key=lambda b: b.area))
                moved = (self._last_gaze is None or abs(g.x - self._last_gaze.x) > self.gaze_step
                         or abs(g.y - self._last_gaze.y) > self.gaze_step)
                if moved and now - self._last_gaze_t >= 1.0 / self.gaze_hz:
                    up.gaze, self._last_gaze, self._last_gaze_t = g, g, now
        else:
            self._hits = 0
            if self.present and now - self.last_seen >= self.leave_s:
                self.present, up.left = False, True
                self._last_gaze = None
        return up

    def reset(self) -> None:
        self.present, self._hits, self._last_gaze = False, 0, None
