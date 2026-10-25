            # jarves.features already registers camera / face_recognition / camera_when_away (probe reads the
            # `camera` health component CameraService sets), so nothing is registered there a second time.

"""Camera feature switches (dashboard-switchable, ALL default OFF):

- `camera`            - make requested captures and explicit local previews available
- `face_recognition`  - recognise enrolled people (each person still opts in by voice)
- `camera_when_away`  - legacy compatibility flag; never starts background capture
- `camera_preview`    - "What does he see?" local preview while the user keeps it open (default ON, needs `camera`)

They are registered with WP-I's `jarves.features` (interface: register(id, title_he, title_en, default_enabled,
probe, risk) and active(id)) when that module exists, so the dashboard lists them and shows "unavailable" while
the probe finds no camera. Until it is merged, `LocalSwitches` keeps the same interface on memory.db settings
(`feature:<id>` = on/off), switchable with `python -m jarves.camera on|off <id>`.
"""
from __future__ import annotations

from typing import Callable

ProbeFn = Callable[[], tuple[bool, str]]

SWITCHES = (
    # id, title_he, title_en, risk
    ("camera", "מצלמה", "Camera", "medium"),
    ("face_recognition", "זיהוי פנים (באישור כל אדם)", "Face recognition (each person opts in)", "high"),
    ("camera_when_away", "מצלמה כשאין איש בבית", "Camera while nobody is home", "high"),
    ("camera_preview", "מה הוא רואה? (תצוגת מצלמה)", "What does he see? (camera preview)", "low"),
)
DEFAULT_ON = frozenset({"camera_preview"})      # harmless alone: it needs the `camera` switch, which is OFF


class LocalSwitches:
    """Fallback with the jarves.features interface, persisted in memory.db settings."""

    def __init__(self, memory=None) -> None:
        self.memory = memory
        self._specs: dict[str, dict] = {}

    def register(self, id: str, title_he: str, title_en: str, default_enabled: bool = False,
                 probe: ProbeFn | None = None, risk: str = "low") -> None:
        self._specs[id] = {"title_he": title_he, "title_en": title_en, "default": default_enabled, "probe": probe,
                           "risk": risk}

    def enabled(self, id: str) -> bool:
        spec = self._specs.get(id, {"default": False})
        v = self.memory.get_setting(f"feature:{id}") if self.memory is not None else None
        return spec["default"] if v is None else v == "on"

    def set(self, id: str, on: bool) -> None:
        if self.memory is None:
            raise RuntimeError("no settings store")
        self.memory.set_setting(f"feature:{id}", "on" if on else "off")

    def available(self, id: str) -> tuple[bool, str]:
        probe = self._specs.get(id, {}).get("probe")
        return probe() if probe is not None else (True, "")

    def active(self, id: str) -> bool:
        return self.enabled(id) and self.available(id)[0]

    def view(self) -> list[dict]:
        out = []
        for id, s in self._specs.items():
            ok, detail = self.available(id)
            out.append({"id": id, "title_he": s["title_he"], "title_en": s["title_en"], "risk": s["risk"],
                        "enabled": self.enabled(id), "available": ok, "detail": detail})
        return out


def _features_module():
    try:
        from .. import features      # WP-I (dashboard feature switches); absent until merged
    except ImportError:
        return None
    return features if callable(getattr(features, "register", None)) and callable(
        getattr(features, "active", None)) else None


class Switches:
    """What the camera code calls. Delegates to jarves.features when present, else LocalSwitches."""

    def __init__(self, memory=None, features=None) -> None:
        self._f = features if features is not None else _features_module()
        self.local = LocalSwitches(memory)
        self.backend = "features" if self._f is not None else "local"

    def register_all(self, probes: dict[str, ProbeFn]) -> None:
        for id, he, en, risk in SWITCHES:
            p = probes.get(id)
            self.local.register(id, he, en, id in DEFAULT_ON, p, risk)
            # jarves.features already registers camera / face_recognition / camera_when_away (its probe reads the
            # `camera` health component CameraService sets), so they are not registered there a second time.

    def enabled(self, id: str) -> bool:
        """The saved switch alone (on even when the device is missing). Failure counts as OFF."""
        try:
            if self._f is not None:
                return bool(self._f.current().enabled(id))
            return self.local.enabled(id)
        except Exception:
            return False

    def active(self, id: str) -> bool:
        """Switched on AND available. Any failure of the switch backend counts as OFF (privacy default)."""
        try:
            return bool(self._f.active(id)) if self._f is not None else self.local.active(id)
        except Exception:
            return False
