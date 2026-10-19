# DevicePort v0.1 — interface contract, not implementation request

A single logical boundary for simulated devices now and Windows-USB/Pi-Linux adapters later.

## Inbound events (device -> Core)
Envelope: `{ "type": "button.down", "ts_ms": 12345, "payload": {"id":"action"} }`.

| Type | Payload | Simulation -> later hardware |
|---|---|---|
| `button.down`, `button.up` | `id` | UI click/release -> GPIO edges / USB bridge |
| `mic.mute_changed` | `muted: boolean` | toggle -> device mute (verify physical privacy) |
| `presence.changed` | `present: boolean` | toggle -> mmWave/PIR sensor |
| `light.changed` | `lux: number` | slider -> I2C lux sensor |
| `camera.frame_available` | `frame_id: string` | fixture -> camera capture |
| `network.changed` | `connected: boolean` | toggle -> OS monitor |

## Outbound commands (Core -> device)
`speaker.play`, `speaker.stop`, `speaker.set_volume (0..1)`, `face.set_state (idle/listening/thinking/speaking/happy/error)`, `display.set_brightness (0..1)`, `camera.capture`.
Camera (SW-V2 WP-J, `core/src/jarves/camera/`): `face.set_gaze {x, y: -1..1, confidence: 0..1}` (0 = stop tracking) and `camera.indicator {on, flash}` (software indicator; see `docs/CAMERA_SETUP.md`). The camera adapter lives in `camera/backends.py`; frames stay in the Core process RAM and never cross the face link.

## Semantics
- Inputs on all platforms must produce the same named events and state transitions. Button down/up are separate; Core derives long press from times.
- Core MUST NOT import GPIO/USB/camera drivers directly; only adapter does.
- Camera frame/event carries an ID/ref, not image bytes in JSON. Real-time audio has a separate streaming path.
- "Mute" in UI is not a guarantee of electrical microphone disconnection; label truthfully.
- Device absent, permissions denied, network lost -> recoverable state; NOT a crash.
- Do not implement hardware adapter in Stage 1; this is the future testable contract.
