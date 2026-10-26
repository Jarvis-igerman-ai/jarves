# Hotfix recovery entry (#467)

- `core/src/jarves/hotfix_recover.py`: standard-library-only restore of a hotfix backup (`python -I -m jarves.hotfix_recover --latest | --id | --list | --dry-run`); same manifest and backup ids as `jarvis hotfix revert`; verifies every saved original (sha256) and every path before changing anything.
- Installed `jarvis.cmd recover-hotfix` calls it with the bundled Python (no Core import). `jarvis hotfix status` and a failed auto-revert print the command.
- Guide `hotfix.toml` (EN+HE step and problem), capabilities row = the existing `hotfix` feature (evidence text updated). No prompt/tool additions.
- Open: not run on a real installed build (owner PC); the installer payload change (jarvis.cmd) ships with the next full installer.
