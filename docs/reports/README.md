# Reports: one dated file per report

A new report, review, audit or findings list goes to `docs/reports/<YYYY-MM-DD>-<topic>.md` (not `docs/reviews/`, whose
files each have their own row in [DOC_MAP](../DOC_MAP.md)). Reports here are covered by the one DOC_MAP row for this
folder, so a PR that adds one does not edit DOC_MAP and cannot conflict there (35 of ~150 merges touched DOC_MAP).

Start the file with one line saying what it is, the version it belongs to and its status (current / historical).
A report that becomes a maintained document moves to `docs/` or `docs/plans/` and gets its own DOC_MAP row.
No secrets, no personal data, no raw private conversations.
