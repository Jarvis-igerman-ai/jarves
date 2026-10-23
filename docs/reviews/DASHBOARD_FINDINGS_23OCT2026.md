# Dashboard findings review — 23 October 2026

Scope: the owner's local JARVES dashboard, not the GitHub backlog. Private conversations, names, credentials and raw diagnostic evidence stay private. IDs below identify local dashboard records; they are not GitHub issue numbers. Source-level fixes are tracked by [public maintenance issue #77](https://github.com/igalgerman/jarves/issues/77), which carries final commit/CI and delivery evidence.

Reviewed: 25 conversation issues, 13 Bug reports, six monitor findings (four active), 15 latest self-check findings and seven AI-review notes. A repeated snapshot is not a new occurrence. Status changes preserve records/history and require an appropriate recheck; unit tests do not close microphone, account, player or hardware acceptance.

## Confirmed source defects in this change

- Voice microphone privacy had no action tool. Add immediate input gating and independent transport shutdown; unmute remains an explicit window button, tray or `jarvis mic unmute` action. The visible window control stays outside collapsed Options. `jarvis mic status|mute|unmute` reports live state and fails if the requested change is unconfirmed.
- Room rendering received appearance messages without an attached presentation renderer. Reuse the standalone renderer on the room glass; add `jarvis appearance list|status|set` using the same persisted choice. No physical geometry changes.
- Startup prerequisite checks ran before all skills were attached, producing false missing-connector findings. Run the checks after attachment, before the link binds.
- Refused/invalid/replayed tools had no structured result event, producing false missing-result findings. Emit exactly one safe result per completed call, without tool arguments/results; preserve all permission/taint guards.
- Expected permission/approval/privacy refusals were aggregated as runtime failures. Exclude only exact expected outcomes while retaining raw history/logs; model-invalid arguments, genuine handler failures, crashes and timeouts still produce findings.
- Re-ingesting the same cached findings repeatedly incremented occurrence counts and could reopen fixed reports after upgrades. Deduplicate by observation identity while preserving real recurrence and historical records.

## Conversation issues — all 25 accounted for

| Local IDs | Review / remaining gate |
|---|---|
| 1 | Historical face disconnect. Check current live link/recovery; do not infer a recurring failure from an old event. |
| 2, 3 | Historical backend-stall/silent-wait findings do not reproduce with the current classifier on their exact session. Backend calls completed in 0.7–1.8 seconds. Eligible for resolution after verified backup and recorded recheck. |
| 4 | 34 early-audio chunks were deliberately dropped while the user continued speaking; no mic/speaker queue drops. Keep the protection. Real conversational quality check remains. |
| 5 | Measured 9.53-second first-audio wait, without playback backlog/drops. Cause unproved; keep open for a real conversation after the control fix. |
| 6, 11 | Historical camera clarity/repeated-request findings. Build 217 fixes idle capture; requested real capture/clarity remains unverified. |
| 7, 17 | Missing result events for policy refusals; addressed by complete result logging. Recheck with guarded fake calls; preserve refusal. |
| 8 | Historical camera action claim before approval. Guard stays enforced; actual conversational grounding requires real-use confirmation. |
| 9 | Historical incorrect end-window advice. Existing shutdown/end-conversation routes are implemented; real conversational acceptance remains separate. |
| 10 | Hebrew filler/ambiguous wording concern; real speech quality check remains. |
| 12, 20 | Measured music/camera tool latency (8.5/5.5 seconds), not by itself proof of a defect. Requires actual player/camera quality check. |
| 13, 14, 18 | Expected approval/tainted-content refusals, not failed authorization logic. Eligible for reviewed benign resolution; do not relax guards. |
| 15 | Context injection interrupted at historical session close; distinguish cancellation noise from active-session failure before resolution. |
| 16 | Session estimate exceeded a configured advisory amount. Not proof that hard budget enforcement failed; no budget changes. |
| 19 | False-wake symptom requires controlled microphone/environment calibration; no silent threshold changes. |
| 21 | External connection close recovered to wake listening within 1.60 seconds; later conversations succeeded. Recovery incident can be reviewed as recovered, not a guarantee that networks never disconnect. |
| 22 | Acknowledgement started in 2.36 seconds; subsequent status/features/skills calls explain the wait. Current stall classifier finds none. Reviewed unconfirmed AI hypothesis. |
| 23, 24 | Latest action-claim/delegation concerns overlap the missing mute control and ignored room appearance; recheck after delivery. |
| 25 | Four workspace reads: efficiency concern, not proof of failed work. Updated explicit controls/guidance should avoid the dashboard detour; real acceptance remains. |

## Bug reports — all 13 accounted for

| Local JI suffix | Review / remaining gate |
|---|---|
| f35febe0 | One historical 9625-ms startup measurement against an 8-second threshold; repeat a controlled speed check before treating it as a regression. |
| 23d42e9e | Five dirty-shutdown records; not proof of five application crashes. Preserve history and inspect actual failure context. |
| b84bf739, 63b80a0b | Mirrors of historical backend-stall/silent-wait findings above; fresh recheck and dedup prevent stale re-ingestion. |
| 79d8ccf3 | Historical Hebrew filler mirror; speech quality gate. |
| e4b6e751, e6677206 | Historical face disconnect mirrors; actual link/recovery evidence gate. |
| c81876f1 | Camera availability snapshot; startup-order fix and fresh sensorless readiness recheck. |
| 76940db9, 7ad0ecf7 | Pear unavailable/unpaired or real playback unconfirmed. Player/sign-in and actual playback gate; no fake success. |
| f3c9f664 | Inactive build-212 correlation; preserve history, do not label an old event as a current regression. |
| 23c8e677 | False-wake count; controlled microphone calibration gate. |
| ff0c8818 | New quiet-microphone warning correlated with build 217; correlation is not proof the build caused it. Device/input check needed. |

## Monitor, self-check and AI-review notes

- Monitor: `preflight:features` is addressed by registration ordering; `anomaly:wake_false` and `anomaly:step:WAKE_MIC_QUIET` require real input calibration; active build-217 correlation remains unproved. Inactive quiet-warning/build-212 findings are historical, not new defects.
- Latest self-check: historical dirty-shutdown, stall/silent-wait/filler, face-link/disconnect, camera prerequisite and Pear/music mirrors follow the gates above. `config:deadline_late` reflects a real 25-second setting against a 20-second advisory threshold; do not silently change user configuration. The old installed/update-version result is a stale snapshot, not a current updater defect; run a fresh free self-check. Open-issue counts and unused morning-briefing/notes/scheduler are informational, not broken features.
- AI notes 1–5: connection interruption and early-response follow the evidence above; quiet input/local confirmation needs a microphone check; missing tool result and false prerequisites have source fixes. Notes 6–7 are already seen, rank-zero informational items.

## Verified private status checkpoint

A verified standard backup was created before writes. The installed classifier was rerun on the exact historical sessions and produced no stall findings. Conversation records 2/3 and recovered incident 21 are now resolved; expected refusal records 13/14/18 and unconfirmed AI hypothesis 22 are ignored. Seven statuses changed, zero records/history deleted. The private audit records the backup and each rationale. Other gates remain open; source fixes are not proof that the installed runtime has changed.

## Recheck and resolution policy

Use a verified private checkpoint before status writes. Run rules-only diagnosis/self-check with AI disabled; no paid replay or sensor capture. Conversation statuses may be resolved or ignored through the existing API, without deleting history. Bug reports are marked fixed only when the installed fix/recheck supports it. Monitor active state comes from a fresh detection pass; dismissing a note is not a successful recheck. Real microphone/camera/player/account and manual appearance acceptance stay open until actually exercised. Source-only changes do not prove the owner PC is updated.
