# Security hygiene audit: file permissions and secret handling (JAR-153)

Status: PROPOSAL, read-only audit of `core/src/jarves` at `origin/main` (2026-11-09). No code changed, no alert dismissed.
All fixes below touch secret storage or subprocess launch, so they are **Tier R** and need owner approval and Opus review.
Evidence is grep/file:line. Nothing was run against a real Windows ACL (ACL effect is UNVERIFIED, see 1.3).

## 1. Token / session files not owner-only on Windows (cf. #824)

### 1.1 Already hardened (reference pattern)
- `workflow/server.py:40` `owner_only_file` (icacls, `/inheritance:r`), used at :69, :90, :128 (#824 fix).
- `phone_trust.py:132` `_owner_only_acl` (icacls by SID, better than username).
- `approval_keys.py:85-105` direct `SetNamedSecurityInfoW` protected DACL (no subprocess).

### 1.2 Secret writers that rely only on `os.open(..., 0o600)` / `chmod` (a no-op on NTFS)
| Finding | Evidence | Content |
|---|---|---|
| S1 | `chatgpt_auth.py:65-84` `_write_private` (comment at :83: "%LOCALAPPDATA% is already per-user") | ChatGPT OAuth tokens; reused by `google_auth.py:41,107,140` (Google token + client secret) |
| S2 | `channels_common.py:33-40` `write_private_json` | GitHub token (`github_client.py:103`), other channel tokens, `anydo.py:72` token.json |
| S3 | `config.py:457-466` `save_secret` -> `secrets.env` | every provider API key |
| S4 | `dashboard_auth.py:151-158` `_save` (`write_text` first, chmod after: a window with the default ACL; chmod errors swallowed) | device token hashes, CSRF values |
| S5 | `link.py:66-72` `load_token` (`write_text` then chmod: same race, silent `except OSError`) | link/bridge token |
| S6 | `mobile.py:101-112` `write_private` | TLS private key, trust stores |
| S7 | `whatsapp_store.py:92-96`, `tailnet.py:101` (chmod only), `profiles.py:348,390`, `orchestrator/bridge.py:83-88,199`, `runtime/lock.py:80` | WhatsApp store, tailnet key copy, profile files, bridge socket |

### 1.3 Risk assessment
- Default `state_dir()` is `%LOCALAPPDATA%\jarves` (`config.py:382`), whose inherited ACL is per-user (plus SYSTEM/Administrators). Exposure is low by default.
- It is real when `JARVES_HOME` is overridden (`config.py:379`) to a shared or non-profile folder (as #824 showed with `CodexSandboxUsers` Modify), and for profile dirs (`profiles.py`).
- `selftest.py:183` checks "readable by other users" with POSIX modes only; it cannot detect a loose NTFS ACL. **UNVERIFIED**: no test inspects a real ACL.

### 1.4 Proposal (Tier R)
1. One shared helper (for example `jarves/fsperm.py: write_private_bytes`): create temp 0600, apply the `approval_keys` DACL routine (or `phone_trust._owner_only_acl`) to the temp file BEFORE the rename, atomic replace, return a bool, never raise. Move the duplicated `_write_private` copies (S1-S7) onto it.
2. Fix S4/S5 to create with `os.open(..., 0o600)` instead of write-then-chmod.
3. Extend `selftest.py:183` with a Windows ACL check (`win_dacl_sddl`, `approval_keys.py:105`) that warns when `Users`/`Everyone`/other SIDs can read these files.
4. Hermetic tests: inject `run` as `phone_trust` does and assert the ACL call is made for each writer. Only such a test may justify a `Fixes #N` line.

## 2. Log redaction gaps

Central redactor: `logs.py:56-90` `_PATTERNS`, `logs.py:131` `redact`, `logs.py:105` `redact_deep`. It is broad (OpenAI/Anthropic, JWT, Google, Telegram, Meta, GitHub, Slack, AWS, Stripe, PEM, URL params, key=value). Gaps:

| Finding | Evidence | Gap |
|---|---|---|
| R1 | Parallel, weaker redactors: `github_client.py:36` (one token shape), `openrouter.py:44` (one key shape), `anydo.py:325-330` (bearer + any 32+ char run) | They do not call `logs.redact`, so a PEM block, `password=` or URL `?code=` inside a provider error body passes through. `model_lab.py:91` and `orchestrator/envelope.py:63` already delegate to `logs.redact`: copy that model. |
| R2 | `workflow/trace.py:18,27` `scrub` redacts by KEY name (`SECRET_KEY`) | A token in a free-text value under an innocent key may be kept. Only the first lines of `scrub` were read: UNVERIFIED. |
| R3 | `logs.py:90` `_USER_PATH` covers `\Users\x`, `/Users/x` and `/home/x` | UNC paths (`\\host\share\x`) and domain names are not masked. Minor. |
| R4 | `logs.py` `_PATTERNS` has no standalone shapes for HuggingFace `hf_`, npm `npm_`, SendGrid `SG.`, Twilio `SK...` | Caught only when next to a keyword such as `token=`. Add after a test for false positives. |
| R5 | `logs.py:105-115` `redact_deep` returns the value UNREDACTED past depth 12 and does not redact dict KEYS | Deeply nested secrets, or a secret used as a key, leak. |

Proposal: route R1 through `logs.redact`; make `redact_deep` redact (not return) past the depth limit; table-driven tests for R4 shapes plus negative cases.

## 3. subprocess `shell=True` and shell-string uses

- `grep -rE "shell\s*=\s*True" core/src/jarves` returns **no matches**. `os.system` appears once, `os.system("")` at `logs.py:786` (constant, enables ANSI). No `create_subprocess_shell` or `os.popen`.
- **The one effective shell**: `skills/helper.py:488` passes `shell=sys.platform == "win32"` with `prop.command` (a string) into `isolation.popen`, which calls `subprocess.Popen(args, shell=shell)` at `isolation.py:474` (and `_restricted_popen`). On Windows this is `cmd.exe /c <model-proposed string>`. Mitigations: the approval gate and regex deny lists at `skills/helper.py:18,91` and `orchestrator/policy.py:35`. Regex deny lists can in principle be bypassed by cmd quoting/`^` escapes (UNVERIFIED). POSIX uses `["/bin/sh","-c",cmd]`, same trust model.
- Other launches are argv lists: `workflow/discuss.py:154` (`cmd /c start` with repo path from the owner; low risk), `orchestrator/channel.py:63` (PowerShell `-Command` f-string; checked: `pids` are cast with `int()` at :56, safe), `projectmgr/gitops.py:200` and `workflow/focus.py:56` (`shell=False`).

Proposal (Tier R, Gate/approvals path): (a) show the exact shell string in the approval UI on Windows; (b) add a test that fails on any new `shell=True` literal (grep guard in `core/tests`), allow-listing `skills/helper.py`/`isolation.py`.

## 4. Suggested order
1. `shell=True` test guard (cheap, Tier N).
2. Shared private-write helper with ACL (S1-S7), Tier R.
3. Redactor unification (R1, R5).
4. Windows ACL selftest check.

No GitHub alert was dismissed or touched by this audit.
