# SW-V1 review — JARVES assistant software (18 Oct 2026)

**Verdict: the software runs end to end on this PC offline. The best-quality cloud voice and AI are implemented but NOT yet proven live** because no `OPENAI_API_KEY` is available. Real YouTube Music playback is one user click away. This is **not "V1 done"**: three user actions are pending (below).

## What actually runs (evidence)

| Capability | Evidence | Status |
|---|---|---|
| Core start / ready / stop, truthful health, stale-snapshot status | `jarves run` / `status` / `stop` on this PC; log `CORE_READY` … `CORE_STOPPED` | **Runs** |
| Godot face ↔ Core (real processes) | `face_app.tscn -- --e2e`: **PASS**, reply "השעה 08:00…", face idle→thinking→speaking→happy→idle | **Runs** |
| Animated face (6 states), in the 3D twin and as a standalone display | [face states](../../godot/previews/face/), [twin front](../../godot/previews/face/twin_front_live_face.png); `check_viewer` PASS; face self-test PASS | **Runs** |
| Hebrew commands offline (music, time, memory, status) | 15 intent tests; `jarves ask` on this PC | **Runs** |
| Speech out (local fallback voice "Asaf") | Spoke the E2E reply on the PC speakers | **Runs** (robotic; fallback) |
| Speech in (offline Whisper) | TTS→STT round trip: ivrit-ai 3/3 correct (~19 s); `small` 2/3 (~4 s, now default) | **Runs** (slow fallback) |
| Real mic | Stream opens on the Realtek array; level ≈ 0 (quiet room or Windows mic privacy) | **UNVERIFIED** with real speech |
| Persistent memory, user delete/clear with confirmation | Restart test; confirmation approve/decline test | **Runs** |
| Diagnostics: JSON logs, rotation, redaction, correlation IDs, support bundle | Tests + `jarves diag` | **Runs** |
| GPT-Live voice (`gpt-live-1` → `gpt-6-luna` + web search + Skills) | Session config and the delegated function-call round trip tested offline against the official SDK types | **BLOCKED: needs API key** |
| Realtime alternative (`gpt-realtime-2.1`), cloud speech (`gpt-realtime-2.1-mini`), typed AI chat (`gpt-6-luna`) | Implemented on the Agents SDK 0.24 | **BLOCKED: needs API key** |
| YouTube Music via Pear Desktop | Real Pear 3.12.0 (SHA-512 verified) runs; API on 127.0.0.1 only; unauthenticated → 401; JARVES reports "not paired" truthfully | **BLOCKED: one "Allow" click** (+ YouTube sign-in for Premium) |
| CI | Workflow ready (`ci/cad-smoke.yml`: Core pytest on Ubuntu + Windows, CAD, Blender, Godot); clean-clone `uv sync --frozen` + 46 tests PASS locally | **BLOCKED: token lacks `workflow` scope**; never run on GitHub |

**Tests:** 46 offline pytest tests PASS. They cover:
- config errors;
- redaction and rotation;
- memory;
- Skill validation, timeout, crash and confirmation;
- second-Skill registration;
- intents;
- the music skill against a fake player and a mock Pear protocol (pairing, re-pair, offline, unconfirmed playback);
- the Core over a real WebSocket: bad token, no hello, typed and push-to-talk paths, mute, missing audio device, disconnect/reconnect, malformed messages, restart persistence, correlation IDs, live-mode toggle and budget, shutdown;
- the GPT-Live delegation round trip.

No test calls a paid API.

## Your actions to finish V1 (about 5 minutes)

1. **API key:** run `setx OPENAI_API_KEY "sk-..."` and restart the Core. This unlocks GPT-Live voice, the GPT-6 agent and web search. Then try Hebrew: if GPT-Live sounds wrong, set `voice.mode = "realtime"`.
2. **Music:** start Pear Desktop (`%LOCALAPPDATA%\Programs\PearDesktop-3.12.0\app\YouTube Music.exe`), sign in to YouTube, run `uv --directory core run jarves music pair`, then click **Allow**.
3. **CI:** run `! gh auth refresh -h github.com -s workflow`. The next agent then copies `ci/cad-smoke.yml` to `.github/workflows/` and reports the first real run.

## Cost (your OpenAI account)

| Item | Cost |
|---|---|
| Voice | $0.05/min (GPT-Live) + backend tokens |
| Defaults | ≤ 30 voice min/day, ≤ 200 AI requests/day, sessions ≤ 10 min |
| Offline commands | Free |
| Tests / CI | Never spend |

## Walkthrough (Hebrew, for the user)

1. מפעילים את `core\run_core.cmd` ואז את `godot\run_face.cmd`. מופיעות העיניים של ג'ארבס.
2. מחזיקים **רווח** (או לוחצים "Talk") ומדברים. עם מפתח OpenAI, לחיצה אחת פותחת שיחה חיה, ולחיצה נוספת סוגרת אותה.
3. אפשר לבקש: "נגן את שלמה ארצי", "עצור", "השיר הבא", "עוצמה 40", "מה השעה", "תזכור ש...", "מה אתה זוכר", "סטטוס", או כל שאלה (עם מפתח ובחיפוש ברשת).
4. "השתק מיקרופון" הוא השתקה בתוכנה בלבד, לא מתג פרטיות פיזי.
5. אם משהו לא עובד: `jarves status` מראה מה שבור ומה לנסות.

## Hardware items carried to #8 (not touched)

- **R5b CAD:** camera bosses, door hooks and speaker strap are not modelled.
- **Printing and strength:** print-bed size, nut strength.
- **Physical:** heat, USB power budget, acoustics.
- **Audio on the device:** speaker→mic echo (needs the ReSpeaker AEC), real mic far-field pickup.
- **Pi 5 load:** Godot + Pear (Electron) + Core together.
