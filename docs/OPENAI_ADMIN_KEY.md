# Optional read-only OpenAI admin key (usage and cost checks)

Needed only for the real monthly OpenAI cost (Usage page spend meter "from OpenAI", voice scenario 62 cost
reconciliation, `jarvis usage`). Everything else works without it; the spend display then says "estimate". Owner
decision OD-05: add it whenever you want.

## Create a read-only key
1. Sign in to platform.openai.com. A personal account is its own organization and you are its owner. If the
   Admin keys page is missing for your account, skip this key: everything works, the cost is just an estimate.
2. Settings, Organization, Admin keys (platform.openai.com/settings/organization/admin-keys).
3. Create new admin key, name it `JARVES-costs`, permission **Read only**. Copy it (starts with `sk-admin-`).
   OpenAI shows it once. The same guide, in Hebrew and English, is in the dashboard (Guides, "Real API bill").

## Save it (any one of these)
- Terminal: `jarvis connect openai-admin` (hidden input; type `s` to skip). `jarvis key --admin` does the same;
  `jarvis key --admin --remove` deletes it.
- Dashboard: Usage page, "OpenAI admin key" box (password field), Save key / Remove key / Test now.
- Hidden-input pattern for a local helper script: read the key with a hidden prompt (PowerShell
  `Read-Host -AsSecureString`, Python `getpass.getpass`), pass it to `jarvis connect openai-admin` through the
  terminal prompt, never as a command-line argument, never in the repo, never echoed.

## How it is handled
- Stored by `config.save_secret` in `<state_dir>/secrets.env` (private to your Windows user, outside the project).
  The key is never printed, echoed by the API, written to a log or included in a diagnostics bundle.
- Sent only to `GET https://api.openai.com/v1/organization/costs` (read-only, free, no model call), at most hourly.
- Code: `usage.save_admin_key / remove_admin_key / admin_key_saved`, `Monitor.refresh_costs`.
- Status: `implemented_unverified` (tests use a fake costs endpoint; no real admin key tested yet).
