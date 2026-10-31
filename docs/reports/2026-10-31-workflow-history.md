# Workflow history and explicit provider/model routing

PR #712 extends the native-manager workflow with a bounded action journal in the same private SQLite ledger, owner-selected retrospective export, and model selection through the existing agents interfaces. No new LLM scheduler or automatic skill mutation. Optional JARVES management is prepared as issue #715, not activated.

## Validation

- 184 targeted tests passed: workflow store/HTTP/native/control/sync/history, model routing, fake OpenCode integration, CLI lazy routing and changelog format.
- All eight `core/tools/prepush.py` checks passed before publication; rerun after the final UI navigation/report edit.
- JavaScript syntax and `git diff --check` passed.
- Browser QA used clearly labelled synthetic state with the actual static UI: agent -> action, recorded failure details, 100 + 5 event pagination with frozen history, and a prepared /retro prompt. Backend auth/CSRF/privacy/export behavior was tested separately through real local HTTP tests. This is not real Claude/Codex/OpenCode or Windows acceptance.
- Broader existing native-agent integration tests cannot complete in this host because creation of their Unix sockets raises `PermissionError: [Errno 1] Operation not permitted`. They remain required in CI; no test was skipped or weakened.
- Independent Codex review found and rechecked fixes for preserving local rules/security hooks, external actor lane/dispatch policy, credential redaction and bounded observer contention. The repository's named independent Opus Tier R review is still outstanding; no Opus runtime is available here.

## Explicit limits

Requested model is not an attestation of effective model. OpenCode retains full native approval per prompt and has no reliable measured dollar cap. External history currently mirrors normalized provider summaries and may omit raw tool detail; custom workflow homes are not automatically mirrored from Core. Native instrumentation records only received events and reported relationships, not missing past actions or hidden reasoning. History is bounded to seven days / 20,000 events; retrospective export to 5,000. Detail capture is opt-in and secret redaction is best effort; review exports before sharing. /retro and its writing-for-agents dependency were inspected upstream, not installed or executed on the owner's PC.

Native PC setup, real provider/model verification, full provider tool coverage and OS supervisor recovery remain UNVERIFIED. The owner requested native PC acceptance after merge; protected pre-merge checks are retained.
