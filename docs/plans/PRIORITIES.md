# Priorities and goals (owner, 20 Oct 2026) — follow in this order; do not start a lower item before the higher one is done

> **Migration note (20 Oct 2026, repository audit).** This file was restored from the legacy private branch
> `v3-priorities` (head `612278e`), whose owner decisions never reached `main`: the later `main` version of this
> file was created fresh and held only the last two bullets (WhatsApp and "V4 approved"). Those two bullets are kept
> verbatim at the end. Personal names in pronunciation decisions are replaced by neutral descriptions. Items 1-3 are
> historical (V3 was integrated, tested and released as `v3.0.0`); the "dev home" repository rule in the owner answers below is
> **superseded** by the 20 Oct 2026 public-only decision in `AGENTS.md` / `docs/NOW.md`.

1. **Finish the integration.** `v3-final` = every V3 branch merged (campaign fixes included), no conflicts, Hebrew/locale/guide/capability tests green. Three consecutive full offline suite runs, zero skipped, zero flakes. Push to `v3-final`.
2. **Finish full testing.** Campaign A (features), B (dashboard + logs — done), C (voice, live budget ~$5 of $10) on the offline TEST profile; fix P0–P2, list P3; every test PASS / FAIL / BLOCKED with evidence; log review after each pass. Owner-assisted items (Google, Telegram, real speaker/mic/camera, Pear, wake samples) stay BLOCKED until the owner runs `jarvis connect`.
3. **Release.** Export the tested `v3-final` to the public repo `igalgerman/jarves` (sanitized: no private logs/screens/email, noreply author), CI green there, tag `v3.0.0`, signed installer published. Close the superseded PRs in the private repo.
4. **Only then, if time remains: plan SW-V4.** Read docs/ROADMAP.md, docs/plans/FEATURE_SUGGESTIONS.md, MULTI_DEVICE_REMOTE.md, CAMERA_PLAN.md, the campaign bug tables and HANDOFF questions; write docs/JARVES_SW_V4_WORK_PACKAGE.md (software only; phone/Pi/hardware stay later) with prioritized work packages P0/P1/P2, acceptance tests, and open questions for the owner. Candidates already agreed: conversation recall, assisted dialing (Phone Link), virtual-computer prototype only if justified, Home Assistant verification, rocket alerts (last), remaining dashboard translations, wake-word custom model with owner samples, free code-signing decision.

Rules: hand-merge only when all checks green; never `git add -A`; focused tests, one pytest at a time (RAM); no secrets in chat; use a normal PowerShell window (not the `!` shell) for anything that needs typed input.

## Owner answers (20 Oct 2026, late)
- Approval prompts spoken aloud name only the kind of action (no message text); the full text stays on screen. (Change to build in the approvals/voice code.)
- Answer in the language of the user's sentence (English question -> English). Already done (CV-05).
- Build Timers and Lists dashboard pages + CLI (V4 P0.3).
- CLI `features on X` for an unavailable feature: save it as ON and show "unavailable" (same as the dashboard) (V4 P0.3, O-02).
- Public repo license: none yet (all rights reserved). Release tag: v3.0.0. Old private PRs (#32 #34 #38 #41 #42 #45 #48): close with a "superseded by v3-final" note after the release. *(The original "dev home" rule — public repo for code + CI, private repo for personal notes/HANDOFF/logs, sync with a snapshot script — is superseded: since 20 Oct 2026 `igalgerman/jarves` is the only active repository.)*
- Languages (owner, 20 Oct late): ONLY English (default) and Hebrew. Arabic and Russian are dropped: no more work, not part of V4 (remove from the dashboard picker when convenient). Popup approved; `jarvis connect` finished (Google, Pear, wake connected; Telegram/Instagram/Home skipped).
- Owner answers (20 Oct, late evening): a contact's name sounds better WITHOUT the pronunciation dictionary -> ship no default entries. Keep gmail_send, calendar_write, camera, camera_when_away, face_recognition, dashboard_lan ON in the real profile (sends still need exact confirmation; no delete tool). Remove the default (misspelled) entry for the owner's first name. Spoken approvals include the recipient's first name; dashboard Clear uses the full approvals popup.
- WhatsApp (owner, 20 Oct late): keep the safe handoff for sending now. Real read/send via a linked-device bridge goes to V4 after the v3.0.0 release: owner chose his MAIN number, read-only first; message text goes to OpenAI only for messages he asks about. (Risk: unofficial linked device can get the number restricted; see docs/plans/WHATSAPP_REAL_ACCESS.md.)
- V4 approved by the owner (20 Oct 2026, night): start everything possible in parallel (limit: RAM); live-spend cap $25 total (OD-01); installer stays unsigned (OD-08); report = short update when something finishes or blocks.
