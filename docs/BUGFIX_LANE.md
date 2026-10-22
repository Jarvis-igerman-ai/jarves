# Bugfix lane: from a problem JARVES finds in himself to a fixed release

Owner rule (CLAUDE.md): every release from 4.1/5.0 fixes all known bugs and issues. This is the lane that feeds it.

1. **Find.** JARVES records problems into one local list (`jarvis issues`, dashboard page **Bug reports**, header chip
   "Issues: N"): self-test and speed test failures, Self-Diagnostics findings (incl. the crash check), tools that keep
   failing (5 failures of one tool and code within an hour), crashes and errors with an exception, and dashboard script
   errors. Each problem is **one record** (id `JI-xxxxxxxx`, the same on every install) with severity P1-P3, a count, the
   versions seen and **content-free evidence only** (codes, numbers, timings, function names; never text, paths, addresses
   or keys).
2. **Report.** The owner presses **Report** (or `jarvis issues report ID`). The exact text is shown, the approvals popup asks,
   and only then a GitHub issue labelled `from-diagnostics` is filed on `igalgerman/jarves` (a repeat comments on the
   existing issue). No token: the text is saved in `issue_reports/` to attach by hand. No network before the approval.
3. **Triage.** `python core/tools/triage_issues.py` lists the open `from-diagnostics` issues grouped by registry id, worst
   first, and says FIX, CLOSE (the CHANGELOG already lists it) or DUPLICATE. It changes nothing unless `--close-fixed`.
4. **Fix.** One branch per issue or small group, a failing test first, the fix, the test green in CI. Severity order: P1
   (crash, data or privacy risk), P2 (a feature broken), P3 (slow or cosmetic). P0-P2 block a stable release
   (docs/RELEASING.md).
5. **Say it was fixed.** Put the id or code on a **Fixes** line of the CHANGELOG section of the release, e.g.
   `- Fixes: JI-1a2b3c4d, TOOL_play_NO_PLAYER`. Installed copies read the CHANGELOG after an update, mark the matching
   records `fixed in <version>` and tell the owner once. If the same problem shows up again in a version that should have
   the fix, the record reopens as new (a regression).
6. **Close.** After the release, `triage_issues.py --close-fixed` closes the GitHub issues with "Fixed in <version>".
