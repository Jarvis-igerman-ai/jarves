"""Barge-in while the half-duplex echo gate is closed (issue #17).

Without a headset the speakers feed the microphone, so while JARVES speaks the mic is silenced towards the model
(`LiveVoice._gated`). That kept JARVES from hearing itself, but it also meant a user talking OVER it was not heard
at all: the model "kept answering while the user was talking". `EchoBargeIn` listens to the LOCAL mic level during
the gated time: the echo level is tracked, and a level well above it for a moment is the user, not the echo.
Pure logic (no audio I/O, no clock): the caller passes the chunk level and its duration.
"""
from __future__ import annotations


class EchoBargeIn:
    def __init__(self, *, hold_s: float = 0.3, ratio: float = 3.0, abs_min: float = 0.05, warm_s: float = 0.5,
                 cold_abs: float = 0.12) -> None:
        self.hold_s, self.ratio, self.abs_min, self.warm_s, self.cold_abs = hold_s, ratio, abs_min, warm_s, cold_abs
        self.reset()

    def reset(self) -> None:
        self.echo = 0.0          # running level of the room echo of JARVES's own voice
        self._warm = 0.0
        self._over = 0.0

    def threshold(self) -> float:
        if self._warm < self.warm_s:
            return self.cold_abs                    # echo level not known yet: only a clearly loud voice counts
        return max(self.abs_min, self.ratio * self.echo)

    def observe(self, level: float, dt: float, gated: bool) -> bool:
        """One mic chunk (`level` = RMS 0..1, `dt` seconds). True = the user is talking over JARVES."""
        if not gated:
            self.reset()
            return False
        loud = level > self.threshold()
        if loud:
            self._over += dt
        else:
            self._over = max(0.0, self._over - dt)  # a short dip inside a word does not reset the whole hold
            self.echo = level if self._warm == 0.0 else 0.9 * self.echo + 0.1 * level
        self._warm += dt
        if self._over >= self.hold_s:
            self._over = 0.0
            return True
        return False
