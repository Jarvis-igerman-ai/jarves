# SW-V4 package C: independent code re-review and fixes

- **Reviewer:** Opus 5.5 in a fresh context. It did not write the reviewed code.
- **Date:** 20 Oct 2026
- **Branch:** `v4-c-review`, off `origin/main` `5a98f13`.
- **Issues:** #16 and #17.
- **Scope:** the changes since 18 Oct 2026. The #16 baseline is `38948e2`. That diff touches 185 files under `core/src`, about +45.8k lines.
- **Method:** a static, adversarial read of the security boundaries:
  - the helper / approvals / popup
  - the taint guard
  - the browser policy
  - Google, Telegram, Instagram and WhatsApp
  - profiles
  - dashboard auth / CSRF / XSS / static paths
  - update signature verification
  - the installer
  - secrets handling and dependency pins
- **Voice:** the voice lifecycle (`voice/live.py`, `app.py`) was read against the #16 items.
- **Tests:** every confirmed weakness got an exploit-style test first. The test was run against the reviewed code and failed (`git show 5a98f13:<file>`). Then the smallest safe fix was added. All new tests are in `core/tests/test_review_c.py`. They run on Windows and Linux with 0 skips. Platform spellings are chosen per OS.
- **Test runs:** only focused pytest runs, one at a time. The full suite was not run.

## Findings

Severity follows `JARVES_SW_V4_WORK_PACKAGE.md` 9.4:

- **P0:** data loss or a security hole with no approval.
- **P1:** an approval or taint boundary can be bypassed, or there is a wrong security level.
- **P2:** defence in depth broken, or a cross-boundary leak that still needs one owner approval.
- **P3:** robustness, honesty or cosmetics.

| ID | Sev | Area | Finding (evidence) | Reproduction (test) | Status |
|---|---|---|---|---|---|
| C-01 | P1 | helper classifier | Destructive commands were classified `normal` instead of `dangerous`, so a spoken "yes" or the face could approve them without the typed code. Causes: `_RECURSIVE_DELETE` and `format/diskpart` matched only at the start of the line, the recursive flag had to come first, and the encoded-PowerShell check took only 5 exact spellings (`helper.py` 91 and 108 at `5a98f13`). Bypasses: `cmd /c rd /s /q C:\Data`, `powershell -c "Remove-Item -Recurse ..."`, `powershell -encodedc …`, `powershell –enc …` (en dash), `rmdir /q /s X`, `rm -f -r X`, `env rm -rf X`, `python -c "shutil.rmtree(...)"`, `find X -delete`. | `test_nested_or_late_flag_destructive_commands_need_the_typed_code` (6 cases per OS) | **Fixed.** Nested shells (`cmd /c`, `powershell`/`pwsh`, `sh -c`, `wsl`...) count as dangerous. A recursive flag anywhere among the options counts. PowerShell `-r…` and dash variants count. A recursive delete inside a nested command line is dangerous with no sandbox exemption. Inline-code deletes and spawns count (`shutil.rmtree`, `os.system`, `subprocess`, `find -delete`, `xargs`). Harmless `python -c "print(1)"` and deletes inside the sandbox stay `normal` (`test_harmless_inline_code_and_sandbox_deletes_stay_normal`). |
| C-02 | P1 (chain) / P2 | helper: JARVES data folder | The refusal "touches JARVES's own data folder" compared only literal text and top-level tokens. These spellings got past it: a wildcard (`…\test_x*\secrets.env`), a relative path inside a quoted inline program (`python -c "open('.jarves/secrets.env')"` with cwd = the user folder), `../../secrets.env` from the sandbox inside quotes, a drive-less `/Users/...` with forward slashes, the `\\localhost\C$\…` admin share, and `~/` inside quotes on Linux. Why P1 as a chain: one voice-approved `normal` command can also *write* `<home>/approvals/answers/<id>.json`. The Core's file poller accepts that file as a dashboard answer, so the model can answer its own later typed-code requests. | `test_data_folder_by_wildcard_is_refused`, `..._inside_a_nested_command_line_...`, `..._by_traversal_out_of_the_sandbox_...`, `..._platform_spellings_...` | **Fixed.** `_mentions_home()` now checks all of these: `~` and `%VAR%`, forward slashes, drive-less, `\\?\` and `\\.\`, `\\host\X$`, the 8.3 short name, wildcards matched component-wise (fnmatch), and every word of nested command lines resolved against the cwd. Paths inside the sandbox stay allowed (`test_sandbox_paths_are_still_allowed`). The residual risk is R-01. |
| C-03 | P2 | registry taint | The turn taint (`TOOL_UNTRUSTED_TURN`) was set only when an `untrusted_output` tool returned `ok=True` (`skills/__init__.py:333`). A helper command that exits 1 returns its output, which an attacker can control, in `output_tail` with `ok=False`. The next model action in that turn was then allowed. | `test_failed_untrusted_tool_output_still_taints_the_turn` | **Fixed.** A failed result that carries `untrusted_*` keys or `output_tail` also taints the turn. A failure with no third-party text does not (`test_failure_without_third_party_text_does_not_taint`). |
| C-04 | P3 | helper on Windows | `shutil.which("npm")` finds `npm.cmd`. A `.bat` or `.cmd` run from a list argv goes through cmd.exe. cmd.exe parses the Python-quoted line a second time (the "BatBadBut" class, CVE-2024-24576), so what runs can differ from the line the owner approved. | `test_batch_file_programs_run_through_the_shell_as_shown` | **Fixed.** A resolved `.bat` or `.cmd` runs as the shown line through cmd itself. |
| C-06 | P1 | Instagram | `instagram_inbox` returns DM text from strangers but was not `untrusted_output` (`instagram_channel.py:398`). An injected DM could drive model actions in the same turn, for example `schedule_approve`, `note_add`, `browser_open` or `telegram_draft`. Gmail, Calendar and contacts were marked untrusted. | `test_instagram_inbox_text_taints_the_turn` | **Fixed** (`untrusted_output=True`). 4 Instagram tests and 1 helper test now model the owner speaking between the read and the draft. |
| C-07 | P3 | Instagram webhook (public, behind a tunnel) | The handler read `min(int(Content-Length), 1e6)`, so `Content-Length: -1` led to `rfile.read(-1)`. The handler thread then blocked until the client hung up, which is a cheap thread exhaustion. A non-numeric length raised an error. The dashboard already had this fix. | `test_instagram_webhook_negative_content_length_does_not_hang` | **Fixed:** 400 for a negative or bad length, 413 for more than 1 MB. |
| C-08 | P2 | profiles (cross-profile) | A named profile lives at `<base>/profiles/<name>`, and `<base>` *is* the default profile's home (its `secrets.env`, `google/` tokens, `approvals/`). The helper protected only the current profile's home. Folder grants refused only the current home and its parents, not sibling folders of `<base>` (`sandbox.py:94`). | `test_helper_in_a_named_profile_never_reads_the_default_profile_data`, `test_folder_grant_in_a_named_profile_cannot_cover_the_default_profile_data` | **Fixed:** `profiles.data_roots(home)` gives `[home, base]` for a named profile. The helper (classify, npm install cwd) and `_forbidden_grant_roots` use it. |
| C-09 | P3 | secrets | `save_secret` wrote `NAME=value` as given. A value with a line break could add or override other `secrets.env` entries. Callers include a token refreshed from a remote answer (`instagram refresh`) and pasted tokens. | `test_c09_a_secret_with_a_line_break_cannot_inject_another_entry` | **Fixed:** CR, LF and NUL are refused. |

### Reviewed, no defect found

- **Approvals (`approvals.py`):**
  - Default deny on timeout, shutdown, cancel and no channel.
  - `answer()` refuses inside a tool (IN_TOOL). The poller resets the marker.
  - Typed codes cannot be answered by voice or the face.
  - Deny cooldown, rate limit and dedupe are in place. The audit log is bounded.
  - The popup result is read only after the child exits.
  - The ids are 16 hex characters and the route regex is anchored.
- **Dashboard (`dashboard.py`, `dashboard_auth.py`):**
  - Loopback only, with Host pinning against DNS rebinding.
  - HttpOnly SameSite=Strict per-profile cookie, compared in constant time.
  - Writes need `X-Jarves` plus Origin / `Sec-Fetch-Site`. There is no OPTIONS handler, so a cross-site custom header fails at preflight.
  - Paired devices have an allow-list with body checks and CSRF tokens. Pairing codes are limited per IP and in total.
  - Content-Length bounds are checked.
  - Static files are served by `resolve()` plus a parent check.
  - The CSP is strict (`script-src 'self'`, so `javascript:` hrefs are blocked). All dashboard JS renders with `textContent` (no `innerHTML` anywhere).
  - Routes are anchored with `^…$`.
- **Browser (`browser.py`):**
  - Only http/https. `javascript:`, `data:` and `file:` are refused before a host is "completed".
  - Every top-level navigation goes through the request route: a route-level abort catches `http://evil\@allowed`, because Python and Chromium parse that differently. A redirect is checked on landing.
  - Downloads are off. Service workers are blocked. No permissions are granted. The context is fresh and in memory.
- **Google:**
  - `calendar_guard` refuses DELETE, `/move`, `/clear` and `status=cancelled`. Writes are allow-listed by path and fields.
  - There is no Gmail delete, trash or modify route.
  - A send is always confirmed (impact None), only for a draft made in this session.
  - The `EmailMessage` default policy rejects CR/LF in headers.
- **Telegram:**
  - The token is only in `secrets.env`. httpx errors are not passed on, because their text contains the URL with the token.
  - Pairing is limited to private chats. Sends use the draft / confirm pattern.
- **WhatsApp:** handoff only (wa.me pre-fill). The open is confirmed.
- **Updates:**
  - The Ed25519 manifest uses a domain-separated canonical form. HTTPS only. SHA256 and size are checked.
  - An installed build installs only from a signed manifest that it verifies again right before launch. Downgrade is blocked by `is_newer`.
  - `ASSET_RE` forbids path parts in the file name.
- **Link:** a per-install token compared in constant time, roles with message allow-lists.
- **Generated Skills:**
  - Static AST rules: forbidden names, attributes, dunders and `__` strings; only plain functions.
  - The child runs with `-I -S`, an empty environment and restricted builtins with a guarded import.
  - POSIX rlimits.
  - Network and storage only through checked `ctx` calls.
  - (Windows: see R-05.)
- **Installer (`jarves.iss`):**
  - `PrivilegesRequired=lowest`.
  - The purge guard checks a folder before DelTree.
  - `{param:SETUPANSWERS}` is passed into a `cmd /c` line. Whoever runs the installer already runs code, so this is not a boundary (R-08).
- **Dependencies:** `uv.lock` pins the exact versions. The ranges in `pyproject.toml` are bounded where an API is fragile (openai-agents <0.25, websockets <17, playwright <2). `cryptography`, used for update verification, comes in only through `pyjwt[crypto]`, so declare it explicitly when that list is next touched (P3, not changed).

## Residual risks (not fixed, with reasons)

| ID | Sev | Risk | Why not fixed here / proposed fix |
|---|---|---|---|
| R-01 | P2 | An owner-approved helper command runs with the owner's full rights. A program can build JARVES's data path at run time (`os.path.join(os.environ['LOCALAPPDATA'], 'JAR'+'VES')`), which no text check can see. It can then read secrets or write `approvals/answers/<id>.json`. That file would need the typed code, which `pending.json` exposes, so the program can read it the same way. | Text heuristics are best effort by nature. The real fix is OS level: run helper commands under a restricted token or AppContainer, or put a deny ACE for the helper's child on `<home>`; on Linux, a separate uid or a mount namespace. A second option is an HMAC on answer files, with a key that only the Core and the CLI hold in memory. The popup already shows the exact command and folder. This needs a design decision, so it is left for package L/O. |
| R-02 | P3 | Browser: only top-level navigations are policed. Scripts on an allow-listed or approved page can fetch or iframe any host, including loopback and LAN services. A redirect target is requested before `_check_landed` sends the page back. | Same as any normal browser, with no JARVES cookies in the fresh context. Possible hardening: abort sub-resource requests to private or loopback addresses unless the page itself is on one. |
| R-03 | P3 | Browser tools are exempt from the page-text taint guard. Text injected into a page can drive clicks and typing on allow-listed sites. Purchases, passwords, card fields and codes still always ask. | A design choice (the owner allow-listed the site). Document it in the browser guide. |
| R-04 | P3 | Voice approvals: any voice in the room that says a pure "כן" / "yes" 1.5 s or more after a non-typed request approves it. That includes a TV or the music. | An owner decision: voice approval is wanted, and dangerous requests need the typed code. A possible extra: refuse a spoken yes while music is playing. |
| R-05 | P3 | On Windows, generated Skills have no OS-level isolation. They rely on AST rules and restricted builtins only. | Every Skill is approved by the owner. An AppContainer or Job object with no network is the proper fix. |
| R-06 | P3 | In the manual (non-`auto_pair`) Telegram pairing, the newest private chat that wrote to the bot is proposed for a y/N confirmation by name. A stranger with the same first name could be confirmed by mistake. | Use the one-time `/start <code>` deep link (as `jarvis connect` does) for `jarvis telegram start` too. |
| R-07 | P3 | A GET of `/api/approvals` by a paired phone marks "dashboard watching". A request that no real channel can answer then waits for the timeout instead of being denied at once. | Count only local principals in `dashboard_seen`. |
| R-08 | P3 | The installer puts `{param:SETUPANSWERS}` into a `cmd /c` line. | Whoever runs the installer already runs code. Quote or validate it when package L touches the installer. |
| R-09 | P3 | `task_status` (results of web research) and `workspace_read` (files the owner granted) are not `untrusted_output`. | That text is derived from the web or the owner's files. Mark both untrusted after the voice campaign checks the behaviour change. |

## #16: triage and tracking text

Paste this into #16. Keep #16 **open** until the real-Pear validation and the voice regression run are recorded.

> **Package C review (SW-V4, branch `v4-c-review`, report docs/reviews/SWV4_C_REVIEW.md)**
>
> | #16 item | Triage | Evidence | Status |
> |---|---|---|---|
> | P1 playback "confirmed" while Pear is stalled | **design limit + honest wording** | Pear has no live progress signal: `elapsedSeconds` stayed at 27 while the song really played from 27 s to 36 s (real Pear, 19 Oct). Requiring progress gave 0/10 false "not confirmed". The result now says `playback: player_reports_playing` and never "audible". An ignored pause gets an actionable `MUSIC_NOT_CONFIRMED` (`music.py` `_verify_playing` and the pause loop). | done in code; tests `test_play_result_says_the_player_reports_it_not_that_it_is_audible`, `test_stalled_player_that_ignores_pause_gets_an_actionable_message`. **Open: one bounded real-Pear check (stalled video).** |
> | P2 permissive track match / relevance | **confirmed bug, fixed** | `_same_track` needs title + artist when both sides name one in the same script. There is a narrow exception for the same recording under another video id. `track_is_relevant` checks each candidate (Hebrew prefixes, transliteration gives None). | tests `test_same_track_needs_the_artist_too_*`, `test_relevance_is_per_candidate_*`, `test_unrelated_top_result_is_skipped_*` |
> | P2 automatic queue trimming changes Pear history | **design choice, decided (OD-14)** | `music.trim_queue` is now **off by default**, with a switch in dashboard Settings (`config_edit.EDITABLE`, Hebrew label). The queue lock and cancel-on-shutdown are kept. | commit `414c182`; tests `test_config_has_trim_and_step_switches`, `test_trim_can_be_switched_off`, `test_close_cancels_a_running_trim_*` |
> | P2 25 s budget during the candidate loop | **confirmed bug, fixed** | One end-to-end deadline (`PLAY_BUDGET_S` 21 s, below the registry's 25 s). Each candidate's polling is bounded by it, and a truthful `MUSIC_NOT_CONFIRMED` comes before the tool timeout. | tests `test_stuck_play_returns_its_own_truthful_result_*`, `test_candidates_never_starting_stay_inside_the_deadline`, `test_play_budget_always_ends_before_the_registry_timeout` |
> | Risk: false-wake cutoff during a long utterance with late transcripts | **unreproduced risk, guarded** | The no-speech watchdog also looks at mic voice (`NO_SPEECH_TALKING_S`), capped at `ENERGY_EXTEND_S`, so room noise still cannot hold a session open. | tests `test_long_first_utterance_with_late_transcript_*` (20 s and 35 s), `test_room_noise_still_cannot_hold_*`, `test_no_speech_cutoff_unchanged_*` |
> | Risk: captions for suppressed early audio | **unreproduced risk, guarded** | `_hold_caption` keeps assistant text while `_drop_early` drops its audio, and releases it when the audio really plays. | tests `test_caption_of_a_dropped_early_answer_is_never_shown`, `test_held_caption_is_released_*`, `test_normal_answers_are_captioned_immediately` |
>
> Not done in this pass, because each needs the owner or live spend:
> - the real-Pear validation (candidate fallback, next / pause / stop / status, a long queue, a stalled player) and `suite_music.py --groups skill`;
> - the `suite_v3_voice.py` groups greeting, long_utterance, barge-in, idle and budget (cost ≤ $1.5);
> - CI (no minutes).
>
> Close #16 only after those runs, with the final commit and the run results linked.

## Package C open-defect backlog

| Item | Result |
|---|---|
| O-03 `google_connect_help` ok=True | Already fixed on `main` (`ok=True`, code `GOOGLE_NOT_CONNECTED`). Verified. |
| O-04 browser decisions / face disconnect at INFO | `BROWSER_NAV_BLOCKED` changed WARN → INFO (`browser.py`). `LINK_FACE_DISCONNECTED` was already INFO. Test `test_o04_*`. |
| O-05 distinct `jarvis status` exit code | **New exit 4** = ready except that the Godot face is not connected (a headless run). 3 stays "degraded (a real problem)", 1 "not running". Shown in `jarvis status --help`. `campaign_cli.py` and `campaign_realcore.py` accept 4. Tests `test_o05_*`. **Release note:** scripts that treated 3 as "headless OK" should accept 4. |
| O-06 timer "ringing" state | Already on `main` (`Timers.view` state `ringing`, `left_he`). Verified. |
| P01 B2 pause right after a fast next | Code already waits up to 8 s after a recent track change and presses again (`TRACK_SETTLE_S`). The real-Pear re-run of S10 is open (owner/Pear). |
| B3 calendar-delete refusal at INFO | Already on `main` (`calendar_guard` logs INFO). Verified. |
| B4 "בTel Aviv" | Already on `main` (`briefing.weather_place`: a known city in Hebrew, else "ב-Tel Aviv"). Verified. |
| B5 a claimed "stopped" with no tool call | The claim detector now covers `הפסקתי`, `דילגתי`, "resumed" and "skipped" (`live.py` `CLAIM_VERBS_HE` and the English regex). Test `test_b5_*`. |
| Q08 two reminders in one tick | **Not changed (design):** speech alerts are delivered one at a time, so JARVES never talks over itself. The second one waits for the next flush. A possible change: merge the speech alerts that are due together into one announcement. That shifts the voice behaviour, so it should go to the alerts owner together with the voice campaign. |
| CV-16 צחי pronunciation | Already on `main`: `pronounce.DEFAULT_ENTRIES = {}` (owner: no shipped entries). |
| CV-17 17-20k input tokens per delegation | **Not done.** The "before" and "after" numbers need live delegations, which cost money, plus the full voice groups to catch regressions (package risk). Plan: log `input_tokens` per delegation (usage already records it), then trim the backend tool list to the tools of the routed intent and shorten the instructions. Measure on `suite_v3_voice.py` 51-62. |
| Integration open point 1 (`helper: ok` on a fresh profile) | Fixed. The health row follows the switch, `disabled` while off, and listens to later flips (`register_helper`). Test `test_integration_point1_*`. |

## Tests run (focused, one at a time, Windows, PYTHONPATH = worktree)

- `tests/test_review_c.py`: 28 passed, 0 skipped. Every C-xx test was also run on the reviewed code and failed: 12 of 15 in the first batch (the other 3 are must-stay-normal checks), then 4 of 4 for C-06, C-07 and C-08.
- `tests/test_helper_approvals.py` + `test_campaign_features.py`: 68 passed.
- `tests/test_instagram.py`: 11 passed. `test_m3_sandbox.py` + `test_profiles.py` + `test_review_c.py`: passed.
- `tests/test_issue16.py` + `test_music_qa.py`: 81 passed. `test_hebrew_texts.py`: 19 passed. `test_dashboard.py -k setting`: passed.
- `test_selfcheck.py`, `test_swv3_p01.py`, `test_units.py`: passed.
- The full suite and CI were **not** run (RAM rule: focused runs only; CI has no minutes).

## Commits

| Commit | Content |
|---|---|
| `be025ba` | C-01..C-04 helper and taint fixes |
| `93fffc2` | C-06..C-08 Instagram and cross-profile fixes |
| `414c182` | OD-14 |
| `44213d8` | Backlog fixes |
| `f992ad4` | C-09 |
| this doc commit | This report |

OD-13 (a Codex second opinion, owner: "yes, one pass") was **not** run from this agent, because it needs the owner's Codex subscription sign-in.
