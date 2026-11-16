# Security hygiene audit, round 2: subprocess, path traversal, log redaction (JAR-125)

Status: PROPOSAL (current), read-only audit of `core/src/jarves` at `f4390920` (16 Nov 2026). No code changed.
`workflow/**` and `orchestrator/**` were left out. This follows [round 1](2026-11-09-security-hygiene-audit.md)
(JAR-153) and does not repeat it: only new findings and the current state of the round 1 items are listed.
Evidence is grep plus reading the code at file:line. Nothing was run against a real espeak or cmd.exe (see UNVERIFIED).

## 1. New findings

| ID | Sev. (proposed) | Evidence | Finding | Proposed fix | Tier |
|---|---|---|---|---|---|
| N1 | P3 | `voice/tts.py:137` | `EspeakTTS.synth` passes the text to speak as a bare argv item: `[exe, "-v", lang, "--stdout", text]`. A reply that starts with `-` is read as an espeak option. Example: a reply `-5 degrees outside` fails (unknown option, TTS error), and a reply such as `-wname.wav` makes espeak write a WAV file in the Core's working folder instead of speaking. The reply can quote web or e-mail text, so the text is not fully trusted. The text is also visible in the process list (`/proc/<pid>/cmdline`) while it is spoken, even in privacy mode. Only the Pi/Linux fallback engine is affected. | Send the text on stdin (`--stdin`, `input=text.encode()`), which fixes both the option injection and the process-list exposure. A hermetic test with a fake `subprocess.run` asserts that the text is not in argv. | N |
| N2 | P3 | `skills/helper.py:407-414`, `:487-488`, `isolation.py:432,474` | On Windows `npm`/`npx` resolve to `npm.cmd`, so the helper runs the line through `cmd.exe /c` with `cwd` set to the folder that was proposed (this is correct against BatBadBut). But cmd.exe looks for a program in the **current folder first**. A folder that holds a planted `npm.cmd` (for example a cloned repo) would run that file instead of the real npm. `npm ls` and `npm --version` are on the read-only list (`:123-131`), so with `helper_readonly_auto` ON this runs **without a popup**. `helper` and `helper_readonly_auto` are both OFF by default, which keeps the impact low. Related: `shutil.which` (`:411`) also checks the Core's own working folder first on Windows. | Run the absolute path that `shutil.which` resolved inside the cmd line (`"C:\...\npm.cmd" ls`, quoted), and set `NoDefaultCurrentDirectoryInExePath=1` in `child_env()` and for the `which` lookup. Or: never auto-run a `.cmd`/`.bat` command from the read-only list when the folder is outside the helper sandbox. | **R** (Gate/approvals path) |
| N3 | P3 | `skills/helper.py:506-515`; no prune anywhere in `helper.py` | The full output of each helper run is written **unredacted** to `<home>/helper/runs/<id>.log` and never deleted. Only the summary that is shown or spoken is redacted (`:540`, `logs.redact`). A command such as `set` or `git config -l` could store tokens there in plain text. These logs are not in the support bundle (`diagbundle.py:128-138` lists only file names and sizes), so they stay on the local disk. | Pass each chunk through `logs.redact` before it is written (or redact on read and keep a short retention, for example the last 200 runs or 30 days). | N |

## 2. Checked and found sound (no action)

- **No `shell=True`** literal outside the helper path. The only `os.system` call is `os.system("")` (`logs.py:786`, a constant). There is no `create_subprocess_shell` and no `os.popen`. Every other launch uses an argv list.
- `install_latest.py:425-468` builds a PowerShell script from paths. `_ps` doubles all four PowerShell quote characters (`:422`) and removes CR/LF, so a path cannot leave the string. The script is started with `-File`, not `-Command`.
- `runtime/viewer.py:52-60` removes `OPENAI_API_KEY` from the Godot child's environment. `skills/helper.py:439-450` `child_env` removes every variable with a credential-like name.
- **Path traversal, dashboard HTTP:** `_static` (`dashboard.py:836-838`) resolves the path and checks that it stays under `STATIC`. Workspace routes use an allow-list of known file names (`workspace.py:148-154`). `api_workspace_note` (`dashboard.py:686`) and `version` (`workspace.py:322`) check the value with a strict `fullmatch`. Session ids are only used as SQL parameters.
- **Path traversal, agent file tools:** `sandbox.py:174-201` refuses links/junctions on every path part, resolves the path and then checks it stays inside the root. Grant roots refuse system, program, `.ssh` and JARVES data folders (`:95-139`).
- **Archives:** backup restore refuses absolute, `..` and drive paths (`backup.py:431-432`). The hotfix tar uses `extractall(filter="data")` (`hotfix.py:227`). The wheel install checks `_safe_member` plus the RECORD sha256 (`hotfix_slice2.py:439-490`). Hotfix recover checks that each manifest path stays inside the install folder (`hotfix_recover.py:60-63`).
- **Log fields:** every `logs.*` keyword field is classified (`logs.py:196-251`, checked by a lint test). Secrets are masked in every mode, and content/PII are masked in privacy mode (on by default). Every string goes through `redact()`.
- JAR-123 follow-up "`STT_DONE` logs the transcript, UNVERIFIED" (`voice/stt.py:80-81`): it is logged under `text`, which is a CONTENT field, so it is **masked in privacy mode**. It is logged in full only when the owner turns privacy off. Closed as no action.

## 3. Round 1 items: current state at `f4390920`

| Round 1 item | State |
|---|---|
| §3(b) test that fails on any new `shell=True` literal | **Still open**: no such guard exists in `core/tests`. |
| R1 parallel weaker redactors | **Still open**: `github_client.py:32-37` still has its own `_TOKEN` and does not call `logs.redact`. |
| R5 `redact_deep` returns the value unredacted past depth 12 and does not redact dict keys | **Still open** (`logs.py:105-115`). Callers include the approvals audit (`approvals.py:688`) and `github_write.py:182`. |
| S1-S7 owner-only files on Windows, R2-R4 | Not re-checked in this round (out of scope: file permissions and token shapes). |

## 4. Suggested order (one PR per tier)

1. **Tier N, one PR:** N1 (espeak stdin), N3 (redact helper run logs and add retention), round 1 R5 (`redact_deep` masks past the depth limit and redacts keys), the round 1 `shell=True` guard test (allow-list `skills/helper.py` and `isolation.py`), and R1 (`github_client.redact` also calls `logs.redact`). Each with a hermetic test.
2. **Tier R, alone, Opus review:** N2 (helper cmd.exe current-folder lookup). It changes how approved commands are launched.

Proposed GitHub issues (not opened by this read-only audit): N1, N2, N3, each P3 with area `security`.

UNVERIFIED: N1 was not run against a real espeak-ng build (the conclusion comes from the argv shape and normal getopt
parsing). N2 depends on the documented cmd.exe behaviour of searching the current folder first. A local
reproduction with a planted `npm.cmd` in a temp folder was not run in this session.
