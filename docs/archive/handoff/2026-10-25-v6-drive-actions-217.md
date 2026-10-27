# V6 item 10: Drive Actions via phone deep links (#217) - local branch `v6-217-drive-deeplinks`

- `core/src/jarves/drive_actions.py`: pure link builder (tel:, Maps/Waze, YouTube Music). Never dials, sends or plays; no Skill, Gate, approvals or drive mode touched (#317/#298 unchanged). Refusals (emergency/invalid number, bad place, unknown kind) return ok False and no link.
- Route `POST /api/m/drive/action` (drive scope, rate limited, audit holds kind and status only). Feature `drive_actions`, default OFF, needs `phone_drive`.
- Phone: "Quick actions" card in `drive.js` (anchors only, whitelisted prefixes, EN = HE).
- Guide `drive_actions.toml` (EN+HE, parity `phone`), tests `tests/test_drive_actions.py`.
- Not opened as a PR: 3 PRs were already open (#315-#317). Open (draft, `no-automerge`, Opus security review) when a slot is free.
- Local gate: prepush green except `test_p_perf.py::test_backend_prompt_does_not_grow` (48672 > 48400), which fails identically on unmodified main (pre-existing).
- Questions for the user: none. Not tested on a real phone (tel: handling is up to the phone).
