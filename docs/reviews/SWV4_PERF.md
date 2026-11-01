# SW-V4 package P: performance improvement (first pass)

Measured on the owner PC class machine (Ryzen 3 5300U, 7.5 GB, Windows 11, Python 3.12), 21 Oct 2026, with
`core/tools/perf_gate.py` (real background Core, combined scenario, 60 s idle, 3 runs, median) and a Core built by the
test fixture for prompt sizes. Other agents were running at times: noisy items are marked.

## Before / after

| Item | Before | After | How |
|---|---|---|---|
| Idle disk reads (KB/min) | 320-366 (V3: 250-390) | 16-40 | cause removed, see below |
| Backend call, tools JSON + rules (chars) | 59.4k (42.7k tools + 16.7k rules) | 52.1k (37.5k + 14.6k), -12% | slim schemas, gated rule paragraphs |
| Test `test_cost_caps_are_never_exceeded_property` | 29 s | 3.8 s | linear e-mail regex (also speeds `guard`/dashboard/log redaction) |
| Dashboard static (JS+CSS ~670 KB) | sent in full, no-store, every load | gzip (about 70% smaller) + ETag/304, `no-cache` | dashboard.py `_static` |
| Threads, private MB, core_start_ms, CPU | 11-15, 93-99, 1.7-2.0 s, 0.26-0.5 % | unchanged (within noise) | - |
| Full suite | 2220 passed, 0 skipped | 2220 passed (+5 new) in 30 min 36 s on this PC | - |

## Idle reads: the cause (acceptance d)
`Core._health_loop` asked the music player (Pear) every 20 s. The first ask opens the first async httpx connection, and
httpx/httpcore import anyio's submodules lazily at that moment: about 1-2 MB of `.pyc` read from disk, landing right
at the boundary between warm-up and the idle window (hence 250-390 KB/min, and 25-2500 when the timing moved). Found
with an audit-hook + stack probe inside the real Core (`open`/`import` events in the idle window; stack:
`_health_loop -> music.health -> now_playing -> _ensure_token -> reachable -> httpx`). Fixes: (1) the HTTP stack is
imported once in a worker thread shortly after start (`_warm_http_stack`, delayed 5 s so CORE_READY is not slowed);
(2) an unreachable player is asked again after 20, 40 ... 300 s instead of every 20 s, back to 20 s once it answers.
Also seen, not a cause: `GeneratedSkills` watch lists `skills/installed` every 2 s (a directory listing, no bytes).

## Backend tokens (CV-17): 12%, target 40% NOT met
- Tool schemas had pydantic `title` keys on every property and length bounds the Core validates itself: removed
  (`skills.slim_schema`, used by GPT-Live, the Agents SDK text brain and the ChatGPT-plan brain). Tools 42.7k -> 37.5k chars.
- Backend rule paragraphs for Google/mail/calendar and background tasks are left out when none of those tools is
  offered; the camera paragraph shrinks to one honest line when there is no camera tool (`brain.render_instructions`).
- Regression test `tests/test_p_perf.py::test_backend_prompt_does_not_grow` (fails above +10% of the new size).
- Why not 40%: the remaining bulk is 92 tool descriptions (11k chars) and parameter descriptions (5k), most of them
  Hebrew examples that change how the model fills arguments. Cutting them, or offering a tool list per turn, can change
  behaviour and needs the 47+ voice scenarios on the real GPT-Live path (billed, shared $25 cap): not done here.
  Next steps in order of safety: per-session tool groups (hide self_check/health/skill_builder/notes/lists unless asked
  for), then description rewrites, then a measured cached-prefix check (V3 84% cached; instructions already end with the
  changing memories so the prefix is stable).

## Installer
`packaging/build_windows.ps1` now removes the bundled Python's `Lib\test`, `idlelib`, `turtledemo`, `ensurepip`, `lib2to3`
(never used; tkinter/tcl stay for the approval popup). Not built here (1.3 GB pinned downloads), so the saving is
unmeasured; expected 15-25 MB before LZMA. Installer stays 198 MB until a build confirms.

## Budgets
`idle_disk_read_kb_per_min`: baseline 20, owner_max 600 -> 100, ci_max 2500 -> 500 (`[[history]]` entry). Nothing loosened.
`spawn_to_ready_s` (5.0-6.7 s here, both before and after, `core_start_ms` unchanged 1.7-2.0 s) and `threads` (11-15)
are machine/AV noise on this PC; `threads` 15 appeared once before any change.

## Not done (needs the owner PC, microphone, billed GPT-Live or a real browser)
Wake-to-first-audio, session start, barge-in, p90 reply audio (voice latency items), face show/hide, sleep/resume,
dashboard timing in a real browser at 1440/390 px, per-feature memory table, update delta size. The suite wall time
(30 min here, mostly real-browser a11y 242 s, CLI setup 131 s, wake training 3 x ~65 s) needs fixture sharing that is
safe only with full runs; the profile is in the table above for the next pass.

Note: `tests/test_swv4_idle_writes.py` could not be re-run after the last edit because another process held port 18771;
it passed in the full run.

## Tool-description trimming (branch perf/tool-descriptions, #17)
- Model-facing backend call (tools JSON + rules) 59.4k -> under 40.5k chars (>= 30% cut). Every user-triggered tool keeps one English
  and one Hebrew trigger phrase and its safety notes ('ask first', 'draft only', 'never sends').
- `tests/test_tool_descriptions.py`: ~150 EN/HE utterances routed by a fake model (longest quoted trigger phrase wins), trigger and
  safety-note checks, and a hard ceiling (BACKEND_CEILING) so the text only shrinks.
- NEEDS LIVE RE-RUN before release: the fake router proves triggers exist, not how GPT-Live fills arguments. Run the 47+ voice
  scenarios on the real path (billed, shared cap) before shipping this branch.

## 4.0.1: the -30% ceiling is deferred (owner decision of the coordinator)

The 28 V5 tools (goals 8, goal plans 3, preferences 6, proactive 2, development 4, issues 1, self-management 4) were trimmed
to one English and one Hebrew trigger each, parameter descriptions and numeric bounds were removed from their schemas
(the Core validates every call), and routing rows were added. They still add about 8k characters to the model-facing
call: each tool costs roughly 120 characters of JSON frame plus its name before any text. Measured on the test Core:
47.4k (tools 38.0k + rules 9.5k). Ceilings in `test_p_perf.py` and `test_tool_descriptions.py` are now 48.4k (measured +2%);
the 40.5k / -30% target moves to 4.0.2: lazy tool exposure by group (expose a group's tools only when it is relevant).

## #71 lazy tool exposure: -30% reached (4.0.2)
Client-delegation backend call (`voice/lazy_tools.py`, 5 groups + `tools_more`): tools + rules 37.2k chars measured on the
test Core (full list 48.3k, V4 baseline 59.4k, -37%). `test_p_perf` (41.6k) and `test_tool_descriptions` (40.5k) now assert
the lazy size; the full list keeps its 48.4k ceiling because server delegation still sends every tool. Not measured: real
GPT-Live scenarios with hidden groups (UNVERIFIED, billed path).
