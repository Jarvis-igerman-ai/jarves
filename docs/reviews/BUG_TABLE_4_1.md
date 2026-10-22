# Bug table 4.1 (first sweep, 21 Oct 2026)

Release rule: every release from 4.1 / 5.0 fixes all known bugs. Sources: SWV3/SWV4 campaign tables, SWV4_C_REVIEW,
SWV4_K_DASHBOARD, SWV4_R01_DEFENSE, SWV5_SECURITY_REVIEW, open GitHub issues (#1-#5 are roadmap/migration epics, no bugs),
TODO/FIXME with issue refs in core/src (none), flaky tests named by the owner. Status: fixed (already on main) / fixed here
(PR fix/bug-sweep-4-1) / open (reason). CI is the verifier for this PR (merge freeze until the 4.0.0 tag).

| id | sev | area | status | fix PR |
|---|---|---|---|---|
| V4-HE-1, V4-HE-2, V4-DG-1, V4-CLI-1 | P1-P3 | health/diag/CLI | fixed (main) | earlier |
| V4-WA-1, V4-WA-2 | P2/P3 | WhatsApp | fixed (main) | earlier |
| V4-SC-1 | P2 | self-check shutdown | fixed (main) | earlier |
| V4-RP-1, RP-2, RP-3 | P2/P3 | repo names | fixed (main) | earlier |
| V4-AG-1 / V4-VC-7 | P3 | taint wording | fixed (main) | earlier |
| V4-LG-1 | P3 | logging | fixed (main) | earlier |
| V4-UP-1 | P3 | update check http base | fixed here | fix/bug-sweep-4-1 |
| V4-VC-6 | P3 | voice shutdown log noise | fixed here | fix/bug-sweep-4-1 |
| V4-VC-8 | P3 | wake config silently replaced | fixed here | fix/bug-sweep-4-1 |
| V4-VC-10 | P3 | Hebrew plural wording | fixed here | fix/bug-sweep-4-1 |
| C-REVIEW dep | P3 | `cryptography` undeclared | fixed here (pyproject + uv.lock) | fix/bug-sweep-4-1 |
| V4-VC-1..5, 12-14, H-C2-1, C-01..C-09 | P1-P3 | voice / security review | fixed (main) | earlier |
| SR-1..SR-4 (SWV5) | P2/P3 | mobile access | fixed (main) | earlier |
| V4-VC-9 | P3 | split-utterance watchdog | open: needs live voice re-run | - |
| V4-VC-11, H-43 | P3 | backend wording / harness timing | open | - |
| V4-HP-1 | P3 | helper off message | open (not reproduced) | - |
| CV-04 (2/20 false wakes), CV-07 | P3 | wake model | open: needs owner recordings | - |
| CV-17 / tool descriptions -40% (92 tools) | P3 | prompt size | open: needs live voice re-run to verify | - |
| O-01, O-02 | P3 | CLI parity decisions | open: owner decision | - |
| D-10..D-18 | P3 | dashboard Hebrew/layout | open | - |
| R-02..R-09 | P3 | residual security risks | open (documented) | - |
| flaky: idle-write sample, dashboard load, privacy_state frame wait, perf gate noise | P3 | tests | fixed here (warm-up + median, event-driven waits, settle) | fix/bug-sweep-4-1 |
| perf gate noise (spawn_to_ready_s, threads), installer 198 MB, duplicate docs | P3 | perf/docs | open | - |
