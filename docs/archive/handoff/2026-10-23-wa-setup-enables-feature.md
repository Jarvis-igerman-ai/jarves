# 2026-10-23 WhatsApp setup also turns on the reading switch (issue #17)

Bug: after "Set up WhatsApp" the bridge was linked and in read mode, but the `whatsapp_bridge` feature switch stayed
off, so `jarvis ask` said it had no WhatsApp access.

Fix: one backend `whatsapp_admin.enable_reading(home, by, features=None, cfg=None)` = `set_mode(read)` + `features.switch(whatsapp_bridge, on)`
(same call as `jarvis features on`; a running Core follows live via the V4-WA-1 listener). Used only after an explicit yes by
`jarvis whatsapp setup`, Connect `_step_whatsapp`, and the dashboard page (`POST /api/whatsapp/mode` with `enable_reading: true`).
`whatsapp_send` is never touched. A no leaves both off and prints `jarvis features on whatsapp_bridge`.
Hints: `jarvis whatsapp status` (`reading_switch` field + line), `jarvis whatsapp mode read|send`, dashboard page (pill + Turn on button).
Guide `whatsapp_bridge.toml` (EN+HE) updated, Hebrew via catalog_add.py. Tests in `core/tests/test_whatsapp_surfaces.py`.

Not verified: a real PC / live Core picking the switch up (relies on the existing features.json poll and V4-WA-1 listener).
