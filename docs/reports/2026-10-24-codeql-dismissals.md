# CodeQL dismissals, 24 Oct 2026

Owner approved dismissing 10 alerts on Jarvis-igerman-ai/jarves, each individually with its own written reason (no blanket rule, no path exclusion). Each was re-read on main before dismissal.

| Alert | File | Reason | Evidence |
|---|---|---|---|
| #14, #18 | core/tools/campaign_features.py | false positive | `rec()` runs `logs.redact` on evidence before the jsonl write and the print; CodeQL does not model it as a sanitizer |
| #16, #17 | core/tests/test_diag_bundle_strip.py | used in tests | synthetic PLANTED fixture (marked FAKE) proving the diagnostics bundle strips it |
| #22, #23 | core/tools/redaction_audit.py | used in tests | deliberate synthetic planted values proving no leak (PR #133, #135) |
| #25 | packaging/release.py | false positive | prints secret NAMES only, values are only emptiness-tested |
| #26, #27, #28 | core/tests/test_redaction_planted.py | used in tests | synthetic planted values, negative redaction test (PR #133, #135) |

Still open (7): #6, #7 (mobile path), #13 (approvals), #15 (health), #19, #20 (CLI), #21 (logging): planned for review.
