# SW-V2 WP-O — release hosting outside the private repo (public releases repo), signed update manifest

Issue #17. Based on `swv2-integrate` (WP-M6 + WP-L merged in). Owner guide: [docs/RELEASE_HOSTING.md](../RELEASE_HOSTING.md).

**Change of plan (19 Oct):** the owner will not add a card, so Cloudflare R2 was dropped (its free tier asks for a
payment method). The free path: a separate **public** GitHub repo that holds only release files
(`igalgerman/jarvis-releases`, no source code). The R2/S3 code was replaced. The Ed25519 manifest signing, the
app-side verification and their tests are kept unchanged.

## Decisions

| Need | Chosen | Why |
|---|---|---|
| Host | Public GitHub repo, releases only | Free, no card, no new account; release files up to 2 GiB each (installer ~170 MB). |
| CI auth | Fine-grained PAT `RELEASES_TOKEN`, **Contents: write on jarvis-releases only** | The workflow's `GITHUB_TOKEN` cannot write to another repo; this token cannot read the private source. |
| App read | `https://github.com/<repo>/releases/latest/download/latest.json` (anonymous, redirects to the file) | A plain download, not the REST API, so the 60/h anonymous API limit does not apply; checked at most once a day anyway. No token sent. |
| Integrity | Ed25519 signature inside `latest.json` (unchanged), SHA256 + size of the installer from the signed manifest | HTTPS proves github.com, not our CI; a leaked RELEASES_TOKEN alone cannot push an installer PCs accept. |
| Publish order | draft release → upload installer, `.sha256`, `latest.json` → publish with `make_latest` | A PC never sees a release with missing files; a re-run of a half-done draft completes it (replaces partial assets). |
| Retention | newest 3 `win-v*` (+ tags); `[pinned]` in title/notes exempt and not counted; other tags untouched; an older re-run never replaces a newer latest | Same rules as the private repo (WP-M6). |
| Default source | arg > env `JARVES_UPDATE_BASE_URL` > config `[update] base_url` > build default (`vars.JARVES_UPDATE_BASE_URL`) > **public releases repo once the app trusts a key** > private repo releases (`JARVES_UPDATE_TOKEN`) | Nothing changes for PCs until the public key is embedded; then no config edit is needed. `base_url` stays for custom hosting. |

## Bug found and fixed (WP-M6, affects the private repo too)

`release.notes()` writes "add `[pinned]` to a release's notes to keep it", and `is_pinned()` matched that text,
so **every** release counted as pinned and `prune` never deleted a release. `is_pinned` now ignores the
code-quoted mention. Regression test: `test_generated_notes_do_not_pin_every_release`.

## What was built

- `packaging/release.py`: `keygen --out FILE`; `public-publish --dir dist --keep 3` (stdlib GitHub client
  `GitHub`, `public_notes`, draft→assets→publish, prune). Skips with `Public releases: SKIPPED ...` (exit 0) without
  secrets; `::warning::` naming the missing one when only one is set; HTTP errors (e.g. bad token → 401) fail loudly;
  refuses a tampered dist (SHA256 vs build-info) and a malformed `RELEASES_REPO`.
- `.github/workflows/package.yml`: step "Publish to the public releases repo" (`if: always() && verify succeeded`),
  secrets `RELEASES_TOKEN`, `JARVES_UPDATE_SIGNING_KEY`, optional variable `RELEASES_REPO`. Private release unchanged.
- `core/src/jarves/updates.py`: `DEFAULT_RELEASES_REPO`, `public_releases_url()`, source order above; hosted check /
  signature / SHA256 + size / re-verify at install as before. Install still needs approval.
- `core/src/jarves/config.py` / `config.example.toml`: `[update] base_url`, `public_key` (docs updated).
- `docs/RELEASE_HOSTING.md` (Hebrew + English): create the repo (with README), token click steps, keygen, the exact
  `gh secret set` commands (values typed by the owner), test, other PC, pin, rotate.
- Guide `core/src/jarves/guides/data/release_hosting.toml` (WP-L format, no feature of its own); `updates.toml`
  links to it.
- Merge fix: `core/src/jarves/app.py` `_write_tools_md` had `"\n\n"` turned into two real line breaks by the WP-L
  merge into `swv2-integrate` (SyntaxError, nothing imported). Fixed in this branch; **also needs fixing on
  `swv2-integrate`** (the merge here carries it).

## Windows / Pi status

| Feature | Windows | Pi |
|---|---|---|
| update_check (public releases source) | implemented_unverified: offline tests (local fake GitHub API, CI → app end to end); live once the owner creates the repo + token + key | planned: no Pi package (#19) |

## Tests

- `core/tests/test_release_hosting.py` (18): fake GitHub REST server (auth, drafts, latest, tags, upload_url
  template, deletes, paging); publish 5 versions → newest 3 + pinned kept, foreign tag untouched, pruned tags
  deleted, draft published only after the 4 POSTs, no secret in the log; half-done draft re-run completes;
  older build never replaces newer; wrong token → 401; tampered dist / bad repo refuse; signing round trip
  incl. tamper / wrong key / no key / bad alg; OpenSSL fallback; keygen; hosted check anonymous; untrusted key =
  unavailable; approval + SHA/size refusal; cached release re-verified at install; manifest policy; source
  switch; config + build default; default = public repo once a key is trusted; **end to end CI publish → app
  check → install** using only `https://github.com/<repo>/releases/...` downloads; the pin regression.
- `test_release_hosting.py` + `test_updates.py` + `test_packaging.py`: 39 passed.
- Full suite after the merge (before the R2 → GitHub switch, same code otherwise): 1020 passed, 8 failed, 6 skipped.
  None of the 8 are in WP-O files. They reproduce on `swv2-integrate`: `test_core::test_push_to_talk_music_flow`
  (fails on unmodified `swv2-integrate` too); `test_guides` x4 + `test_capabilities` (feature `google_contacts`
  from main has no guide / matrix; briefing / Google-steps expectations from the WP-L ↔ main merge);
  `test_m3_core::test_reminder_is_spoken_then_shown_on_the_face`; `test_workspace_memory::test_setup_document_...`.
- Real API / cloud spend: $0. No repo, token or secret was created by the agent.

## Open questions for the owner

1. Create `igalgerman/jarvis-releases` (public, with README), the fine-grained token, and set the 2 secrets
   (docs/RELEASE_HOSTING.md steps 1–4).
2. Run `keygen` and give Claude the **public** key for `UPDATE_PUBLIC_KEYS` (until then PCs keep using the
   private repo's releases).

## Integration hooks (outside WP-O files)

- `cli.py` `cmd_update`: `UpdateChecker(home, cfg=cfg)`. `config.py`: `UpdateConfig` + `Config.update`.
- WP-M6 files extended: `updates.py`, `release.py` (also the `is_pinned` fix), `package.yml`, `jarves-checks.yml`,
  `build_windows.ps1`.
- `app.py` merge fix (above). Callers that create `UpdateChecker(home)` without `cfg` (Core start, tray) still get
  the env / build / public-repo defaults; only `[update]` config needs `cfg=`.
