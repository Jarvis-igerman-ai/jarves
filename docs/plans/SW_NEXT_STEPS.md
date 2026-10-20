# SW next steps (software only; phone / Pi / remote / multi-device are LATER)

Owner decisions 20 Oct 2026. Follow this file in order; tick items as done and link evidence (PR, run URL, report).
Rule: merge a PR only when ALL its CI checks are green; never merge with failing checks.

## Step 1 — close V3 (merge + green main)
- [ ] Merge open PRs one at a time when green (#32 #34 #38 #39 #40 #41 #42 #43 #44 #45, multilang PR when ready)
- [ ] `main` CI green on the exact SHA (jarves-checks), installer smoke job green
- [ ] Signed installer published to igalgerman/jarvis-releases from that SHA (latest.json signature verified)
- [ ] Docs: ROADMAP + HANDOFF updated; issue #17 comment with evidence

## Step 2 — FULL TEST CAMPAIGN (HIGH PRIORITY, start as soon as Step 1 merges are in) — see FULL_TEST_PLAN.md
- [ ] Manual test of every feature on this PC, by an agent driving the real Core (virtual mic/speaker where possible)
- [ ] Owner-assisted checklist (Google, Telegram, Instagram, real speaker, camera)
- [ ] Log review after each pass; every finding → bug table with severity → fix → retest
- [ ] Report: docs/reviews/SWV3_FULL_TEST_REPORT.md

## Step 3 — fixes from the campaign + known quality items
- [ ] Wake-word false positives (custom model or threshold)
- [ ] Same-tick reminders spoken late (<=30 s)
- [ ] Dashboard pages for timers and lists
- [ ] Hebrew wording review
- [ ] Free code-signing decision (docs/plans/CODE_SIGNING_FREE.md)

## Step 4 — new software features (in order)
- [ ] Conversation recall
- [ ] Calling: assisted dialing
- [ ] Virtual computer: only if the justification says yes
- [ ] Home Assistant verification, rocket alerts (LAST)

## Deferred (do not start): phone satellite, Pi, Tailscale remote, multi-device, hardware.
