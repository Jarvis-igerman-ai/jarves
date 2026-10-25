"""Simulated car-audio / Bluetooth fixtures (V6 #214): fake audio devices and route-change events. No hardware, no I/O.

    from fixtures.car_audio import FakeSoundDevice, SCENARIOS, run_scenario
"""
from .fake_sd import (A2DP, BUILTIN_MIC, BUILTIN_SPEAKER, CAR_HFP, USB_HEADSET, FakeDevice, FakeSoundDevice,
                      PortAudioError)
from .scenarios import SCENARIOS, run_scenario

__all__ = ["FakeSoundDevice", "FakeDevice", "PortAudioError", "SCENARIOS", "run_scenario", "BUILTIN_MIC",
           "BUILTIN_SPEAKER", "CAR_HFP", "A2DP", "USB_HEADSET"]
