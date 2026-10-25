# V6 security and privacy review: drive mode, phone Drive tab, journey, briefing, Skills manifest v2, audio route (issue #223)

Scope: `jarves/main` c1bcb81d (Jarvis-igerman-ai/jarves), the V6 surface merged on 25 Oct 2026: `drive_mode.py` (policy, queue, the
SkillRegistry `Gate` and the `PHONE_SCREEN` route), `skills/__init__.py` (`_authorize`, `_drive_check`), `journey.py`, `drive_briefing.py`
(on `briefing.py`), `skills/manifest2.py` and `skills/platform2.py` (with their hooks in `skills/generated.py`), the audio route handling
(`voice/audio.py`, `voice/live.py`, `app.py`, #213), the phone Drive tab (`mobile_drive.py`, `drive_actions.py`, the `/api/m/drive*` routes
and the bridge in `mobile.py`, `dashboard_static/mobile/drive.js`), the V6 guides and the feature rows. Baseline: the V5 pillars review
([V5_SECURITY_REVIEW_PILLARS_2026-10-25](V5_SECURITY_REVIEW_PILLARS_2026-10-25.md)) and the reviews of #298, #309 and #317 (their findings
are the open issues #300, #301, #302, #314 and #318; not repeated here).
Method: a threat model per surface (owner by voice, owner typing on the PC or the phone, a paired phone with or without the `drive`
scope, a LAN-paired dashboard device, a prompt-injected model, third-party text in mail/calendar/messages, a planted generated Skill),
then reading the code for trust boundaries, fail-closed behaviour, leak paths (location, contacts, mail counts, event titles),
untrusted-text handling, rate limits, audit and log content, default-off switches and EN/HE parity.
Evidence class: code reading plus the targeted unit tests for the two fixes (no real phone, no car, no live Google account). No
secrets were read or printed.

## Summary

| Severity | Found by this review | Already open from earlier reviews |
|---|---|---|
| P0 | 0 | 0 |
| P1 | 0 | 0 |
| P2 | 0 | 0 |
| P3 | 5 (#322 to #326) | 5 (#300, #301, #302, #314, #318) |

No P0 to P2. The central guards hold: drive mode only ever adds a refusal or a deferral before the normal approval (it never approves),
trust comes from Core state (origin, the route a code path set, the per-route taint), the phone Drive tab is read-mostly and every route
needs the phone token plus the off-by-default `drive` scope, no location is read anywhere, and links are built server-side from fixed
prefixes and opened only by the owner's tap.

Fixed in PR #327 (`no-automerge`, coordinator review): #318 (the Gate fallback on the phone route) and #322 (R1, below).

## Findings

| ID | Sev | Where | Finding | Issue / fix |
|---|---|---|---|---|
| R1 | P3 | `drive_briefing.py` empty-result branch | In "present" mode, when every shown section failed, the failure text joined ALL sections, so a calendar section that succeeded but was filtered out (no `minutes_from_now`) would carry its event title (spoken, and to the phone via the bridge `text`). Not reachable with the Google tool today (it always sets minutes). | #322, fixed in #327 (join only failed sections; test) |
| R2 | P3 | `DriveBriefingSkill.mode(override)` | The model's `passenger` argument always wins over the saved setting, so a saved "present" can be relaxed to "alone" by a model call (event title and unread count read aloud). The phone path (`mobile_drive.effective_mode`) only allows more privacy. | #323, owner decision (safe default: apply `effective_mode` on the model path too) |
| R3 | P3 | `drive_mode.tool_kind` and the briefing tools | With drive mode ON, `drive_briefing_get` (read, `untrusted_output`) is a `long_read`, so a spoken "brief me before I drive" is deferred and adds a junk item to the when-stopped list (the voice twin of #311). Cross-feature, not a leak. | #324, owner decision (safe default: refuse without queueing) |
| R4 | P3 | `dashboard_auth.device_body_ok` | A LAN-paired dashboard device (dashboard_lan, off by default) may flip every low-risk switch, including `drive_mode`, `journey`, `drive_briefing` and `phone_drive`; the guides say only the owner switches drive mode and the phone cannot. | #325, owner decision (safe default: block `drive_mode` from devices) |
| R5 | P3 | `/api/m/drive/briefing` | Per-phone limit only (6/min); no shared bucket across phones like the journey route (#312). Each tap calls Google and the weather service and can hold a dashboard thread up to 60 s. | #326, fix (shared bucket) |

Already open and confirmed still valid on c1bcb81d: #318 (fixed in #327), #301 (allow-list by tool name: a generated Skill named
`music` could own `music_stop` if the built-in is missing; the impact stays small because an "allow" still goes through grants and
approvals), #302 (known-contact by name), #300 (queue file with two writers), #314 (Skills Platform 2.0 hardening list).

## What was checked and holds

Drive mode and the Gate (`drive_mode.py`, `skills/__init__.py`)
- Default OFF; with the switch off the Gate returns before any policy (byte-identical behaviour, tested). Only the phone route rule runs
  with drive mode off, and it only refuses.
- The model cannot switch drive mode: `feature_set drive_mode` from the model or from a tainted turn is refused (`DRIVE_OWNER_ONLY`);
  `self_change` / `self_undo` and every other unknown action kind are deferred while driving; there is no tool that releases the queue
  (only `jarvis drive stopped|clear` on the PC).
- Emergency: `dial_prepare` with an emergency number or word opens the dialer at once from the owner's words; from untrusted or
  scheduled sources it is refused, never queued. Hang-up / stop / cancel are always allowed (they still go through the normal flow).
- Untrusted text: `turn_source` uses the per-route taint and origin; model-written fields such as `number_source` never change trust
  (tested). Calls and messages need a known contact, the voice route and one spoken confirmation, plus the normal approval.
- Fail closed: a broken Gate refuses side effects (`DRIVE_ERROR`); after #327 the phone route allows only plain reads in that branch.
- Queue: labels are `tool -> first name` only, cleaned of control and bidi characters, capped (20 items, 200 characters, 24 h TTL);
  logs carry tool, kind, reason and item id, never message text or arguments.

Phone screen route (`PHONE_SCREEN`)
- Set only by Core code in the bridge (`mobile.py` `drive_briefing`), inside its own task with `ROUTE.reset` in `finally`; the registry
  source never names it and an extra `route` argument from the model is ignored (tested). On that route only `read` tools of kind
  answer / long_read / time / calendar_read run, nothing is queued, and the taint is cleared after the call.

Phone Drive tab (`mobile_drive.py`, `drive_actions.py`, `mobile.py`, `drive.js`)
- Every `/api/m/drive*` route checks the phone token and the `drive` scope (off until `jarvis phone scopes <id> --grant drive`), then
  the switches on the PC (`phone_drive`, `drive_briefing`, `journey`, `drive_actions`; drive_actions is medium risk and OFF by default).
  Switch reads fail closed (`_on` returns False; missing state returns 503).
- Passenger: the phone answer applies to one briefing, is never stored and can only make it more private than the saved setting.
- Queue view sends the tool name only (`tool_name` regex), never text or a contact name.
- Drive Actions only build `tel:` / Maps / Waze / YouTube Music links from typed text; emergency numbers are refused (raw and after
  normalisation); control characters refused; unknown schemes refused server-side and again in `drive.js`.
- Audit: `request` lines carry route, status, op and kind; typed numbers, places and search text are never audited or logged. Rate
  limits: briefing 6/min per phone, journey 6/min per phone plus 10/min shared, actions 10/min per phone; all 429 with an audit line.
- `drive.js` renders everything with `textContent`, links only as anchors with an allow-listed prefix, `rel="noopener noreferrer"`.

Journey (`journey.py`)
- Places are typed text only; "here" / "my location" / "כאן" are refused; bidi and invisible characters stripped; 120 characters.
- Network only on request, to Nominatim and the OSRM demo server (disclosed in the journey guide: "only the two places you typed go to
  the map service"); 1 request/s spacing and a 10-minute in-memory cache; provider errors never echo a place or URL to the phone.
- "no live traffic" label and data age on every estimate.

Pre-drive briefing (`drive_briefing.py`)
- OFF by default; "ask" by default reads nothing; "present" keeps only minutes to the next event and the weather; mail is a count only
  (senders and subjects dropped); the weather place is the saved city, never a device position; the tool is `untrusted_output`.
- Gaps R1 to R3 above.

Skills manifest v2 (`manifest2.py`, `platform2.py`, `generated.py`)
- OFF by default. Trust is informational: a signature never skips the sha256 approval, install stays DISABLED with no grants.
- Tampered or invalid manifests fail validation (never approved, installed, updated or loaded); with the switch on, a stored copy that
  no longer matches the approved sha256 is not registered and does not run (`SKILL_TAMPERED`, fail closed).
- `capabilities` and `permissions` are both counted, and skill.py's real tool levels are added, so a manifest cannot hide a permission.
- `discover` never follows symbolic links and caps file sizes; the owner key is never created, exported or read through a symlink.
- Update and rollback are CLI / dashboard owner routes only; no model tool. Remaining gaps are #314.

Audio route (`voice/audio.py`, `voice/live.py`, `app.py`, #213)
- Mic stall (5 s, double grace before the first frame) and speaker loss end the conversation with a truthful notice and "degraded"
  health; the microphone is never reopened by itself; the stall window applies to the conversation mic only (the wake listener keeps
  its own guard). No audio is recorded or stored. No security finding.

EN/HE
- All new user-facing codes have Hebrew and English (`drive_mode.T`, `drive_actions.T`, `journey.T`, `RATE_LIMITED`, `JOURNEY_BUSY`),
  the phone tab catalog is complete in both languages, and the six V6 guides carry `privacy_he` / `privacy_en`. The Gate's
  instruction appended for the model ("Queued until stopped: ...") is English by design (model-facing). `/api/m/drive/action`'s 429
  body has no `error_he` (the other two drive routes do); cosmetic, folded into #326.

## Recommendation

V6 has no open P0 to P2 from this review. Merge #327 after the coordinator's review. The five new P3 items and the five older ones
are listed with an owner and a safe default in [the 6.0 bug table](BUG_TABLE_V60_2026-10-25.md); none blocks a 6.0 beta.
