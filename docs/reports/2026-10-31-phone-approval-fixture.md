# Phone approval fixture publication race (#716)

Windows job 124464887723 on PR #712 returned APPROVAL_ALREADY_ANSWERED where the test expected STALE_TEXT. The fixture waited for in-memory pending registration, which occurs before signed-file publication; the phone route reads only that published file. The unchanged test file subsequently passed locally.

The test helper now waits for the matching signed published row using the existing bounded wait helper. All stale-text/gone-row/decline assertions remain. No runtime approval, timeout, fingerprint, CI retry or skip policy changes.

Validation: all six fingerprint/publication tests and six changelog tests passed (12 total). The new event-controlled held-publication regression fails with the previous helper and passes with the replacement; it uses no timing sleep. Prepush remains required before publication. Protected CI/queue remain required; this report does not claim merge or Windows verification.
