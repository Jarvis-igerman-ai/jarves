# SW-V2 WP-O — release hosting outside the private repo (Cloudflare R2), signed update manifest

Issue #17. Based on `swv2-integrate` (includes WP-M6). Owner guide: [docs/RELEASE_HOSTING.md](../RELEASE_HOSTING.md).

## Decisions (researched 19 Oct 2026)

| Need | Chosen | Why |
|---|---|---|
| Host | **Cloudflare R2** (owner decision), S3 API, path-style, region `auto` | Free tier: 10 GB-month Standard storage, 1 M Class A, 10 M Class B ops/month, egress free (developers.cloudflare.com/r2/pricing). 3 installers ≈ 0.5 GB. Cloudflare may ask for a payment method on first R2 use (third-party guides; not stated on the pricing page). |
| Public read | r2.dev Public Development URL (or a custom domain) | No domain needed; r2.dev is rate-limited / "non-production", fine for a few home PCs; custom domain documented as the upgrade. |
| Upload client | **stdlib SigV4** in `packaging/release.py` | No aws-cli/boto3 version drift (aws-cli ≥2.23 default checksums have had R2 compat caveats); verified against AWS's three published SigV4 S3 examples (GET object, PUT object, ListObjects) in tests. |
| Manifest integrity | **Ed25519 detached signature inside `latest.json`** (`{"manifest", "signature"}` envelope, signature over a domain prefix + canonical JSON) | Simplest sound scheme: one file (atomic, no manifest/sig race), one key; `cryptography` already ships in the app (pyjwt[crypto]); CI signs with `cryptography` or the OpenSSL 3 CLI. Sigstore/minisign rejected: extra tools on the PC. HTTPS alone rejected: proves the host, not that our CI made the file. |
| Path | public, optional unguessable prefix (repo variable `R2_PREFIX`, default `jarves/windows`) | Obscurity only; documented as not a secret. Safety comes from the signature + SHA256 + size. |
| Default source | config `[update] base_url` > env `JARVES_UPDATE_BASE_URL` > build default (repo variable `JARVES_UPDATE_BASE_URL` baked into `_build_info.json`) > GitHub | Other PCs need no config edit once the variable is set. |

## What was built

- `packaging/release.py`: `keygen --out FILE` (private PEM, refuses overwrite; prints public key + key id),
  `r2-publish --dir dist --keep 3`: verifies installer SHA256 vs build-info, refuses to replace a newer
  `latest.json` (old re-runs), uploads `JARVES-Setup-<v>.exe` + `.sha256` (immutable cache) then the signed
  `latest.json` last (max-age 300), prunes to the newest 3 versions; versions in `<prefix>/pinned.txt` and the
  current version are never deleted; nothing outside `<prefix>/JARVES-Setup-*` is touched. Without any secret:
  `R2 hosting: SKIPPED ...` exit 0; with some missing: `::warning::` naming them, exit 0. Bad credentials fail loudly.
- `.github/workflows/package.yml`: step "Host on Cloudflare R2" (`if: always() && verify succeeded`, so it runs
  even if the GitHub Release step failed); secrets R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET,
  R2_PUBLIC_BASE + **JARVES_UPDATE_SIGNING_KEY** (6th secret, needed for signing); optional variable R2_PREFIX.
  GitHub Release path unchanged (fallback).
- `.github/workflows/jarves-checks.yml` + `packaging/build_windows.ps1`: optional repo variable
  `JARVES_UPDATE_BASE_URL` baked into `_build_info.json` as `update_base_url`.
- `core/src/jarves/updates.py`: hosted source (`UpdateChecker(home, cfg=...)`): GET `<base>/latest.json`, verify
  Ed25519 against `UPDATE_PUBLIC_KEYS` (+ optional `update.public_key`), require product/platform/channel main,
  matching installer name/version, 64-hex SHA256, positive size, HTTPS url; newer-only. Install (approval still
  required, unchanged gate) re-fetches and re-verifies the manifest, then checks SHA256 **and size**. No GitHub
  token sent to the host; switching source drops the cached answer; plain-HTTP base URLs are ignored.
  `UPDATE_PUBLIC_KEYS = ()` until the owner runs keygen: hosted checks then say "unavailable" (never trust without key).
- `core/src/jarves/config.py`: `[update] base_url` (https only, validated), `public_key`; `config.example.toml`.
- `core/src/jarves/cli.py`: `jarvis update` passes the config (one-line hook).
- `docs/RELEASE_HOSTING.md`: Hebrew + English click-by-click (account, R2, bucket, public URL, account id, bucket-
  scoped Object Read & Write token, keygen, the exact `gh secret set` / `gh variable set` commands with values typed
  by the owner, test, other PC, pinning, rotation).
- Guide (coordinated with WP-L, branch `worktree-agent-af899c5d114b7394c`): new data file
  `core/src/jarves/guides/data/release_hosting.toml` (no feature of its own, links to `updates`); checked against
  WP-L's schema/test rules (fields, Hebrew, step kinds). It loads once WP-L's `guides/` package is merged.

## Windows / Pi status

| Feature | Windows | Pi |
|---|---|---|
| update_check (hosted source) | implemented_unverified — offline tests (local S3 stub, signing round trip); live once the owner creates the bucket + secrets + public key | planned — no Pi package (#19) |

No new feature switch (it is the source of the existing `update_check`), so `updates.PLATFORMS` is unchanged.

## Tests

- `core/tests/test_release_hosting.py` (17): SigV4 = AWS published signatures (3 vectors); in-process S3 stub that
  re-verifies every request's SigV4 + payload hash, with ListObjectsV2 paging; publish 5 versions → newest 3 + pinned
  kept, other folders/files untouched, installer uploaded before the manifest, no secret in the log; older re-run
  does not replace a newer manifest; wrong secret → 403 error; clean skip / partial warning; signing round trip CI →
  app incl. tamper, wrong key, no key, bad alg; OpenSSL fallback signature verifies in the app; keygen; hosted check
  (no GitHub token sent, notice), untrusted/missing key = unavailable, install approval + SHA/size refusal, cached
  release re-verified at install, manifest policy (channel/https/name/size), source switch, config + build default.
- Full suite: see final report (run from `core/` with `uv run --frozen pytest tests -q`).
- Real API / cloud spend: $0. No Cloudflare account, no credentials, no network.

## Open questions for the owner

1. Create the Cloudflare account + bucket + token and set the 6 secrets (docs/RELEASE_HOSTING.md, steps 1–8).
2. Run `keygen` and give Claude the **public** key to embed in `UPDATE_PUBLIC_KEYS` (until then hosted updates
   are deliberately "unavailable").
3. r2.dev or a custom domain? r2.dev is fine for home use; a domain removes its rate limit.
4. Set the repo variable `JARVES_UPDATE_BASE_URL` so installers check R2 by default (recommended).

## Integration hooks (outside WP-O files)

- `core/src/jarves/cli.py` `cmd_update`: `UpdateChecker(home, cfg=cfg)`.
- `core/src/jarves/config.py`: new `UpdateConfig` + `Config.update`.
- WP-M6 files extended (`updates.py`, `release.py`, `package.yml`, `jarves-checks.yml`, `build_windows.ps1`).
- WP-L: `guides/data/release_hosting.toml` (new file only; WP-L's `updates.toml` may add `links = [..., "release_hosting"]`).
- Tray / Core start (WP-M1) creating `UpdateChecker(home)` should pass `cfg=` so `[update] base_url` applies
  (the build default and env var work without it).
