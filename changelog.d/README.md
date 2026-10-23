# changelog.d: one file per change

Add `changelog.d/<YYYY-MM-DD>-<topic>.md` with your CHANGELOG bullets instead of editing `CHANGELOG.md`:

    - Added: what the user can now do, in one or two sentences. (guide, command, switch)
    - Fixed: what was wrong and is now right.

Kinds: Added, Changed, Fixed, Fixes, Docs, Version, Removed, Security, Deprecated (the same words as in CHANGELOG.md).
Why: every PR used to add its line at the top of the unreleased section, so any two PRs in flight conflicted there.
Readers (release notes, the issue registry's `Fixes:` tracking, the manual test sheet, the installer) merge these
files into the unreleased section; `python core/tools/changelog_build.py --write` folds them into `CHANGELOG.md` at
release time. `python core/tools/changelog_build.py --check` validates the names and lines.
