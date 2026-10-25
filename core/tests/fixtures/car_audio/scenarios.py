"""Named route-change scripts shared by tests and the harness: a list of steps run against a FakeSoundDevice."""
from __future__ import annotations

# step: (action, argument)   actions: connect_car, drop_bluetooth, unplug, plug, switch_route, focus_loss, focus_gain, pump
SCENARIOS: dict[str, list[tuple[str, object]]] = {
    "car_connect": [("connect_car", None), ("pump", 3)],
    "bluetooth_drop_mid_conversation": [("connect_car", None), ("pump", 3), ("drop_bluetooth", None)],
    "bluetooth_drop_and_return": [("connect_car", None), ("pump", 3), ("drop_bluetooth", None), ("pump", 2),
                                  ("connect_car", None)],
    "headset_unplug_replug": [("plug", "USB Headset"), ("switch_route", "USB Headset"), ("pump", 3),
                              ("unplug", "USB Headset"), ("plug", "USB Headset")],
    "incoming_call_focus_loss": [("connect_car", None), ("pump", 3), ("focus_loss", None), ("pump", 2),
                                 ("focus_gain", None)],
    "a2dp_only": [("plug", "Car Stereo (A2DP)"), ("switch_route", "Car Stereo (A2DP)")],
}


def run_scenario(fake, name: str) -> None:
    """Apply a scenario's steps in order (no sleeping; `pump` delivers mic chunks)."""
    for action, arg in SCENARIOS[name]:
        fn = getattr(fake, action)
        fn() if arg is None else fn(arg)
