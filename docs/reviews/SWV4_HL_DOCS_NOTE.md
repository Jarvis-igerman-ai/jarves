# V4 H + L integration: documentation note

PR #24 (branch `v4/pkg-n-docs`, the `docs/USER_GUIDE*.md` set) is not merged into this base, so the two user guide
sections are not written here. They must be added to the guide when #24 lands:

- **Phone calls (assisted dialing)**: say "call <name>"; JARVES finds the number in your saved contacts, shows the
  approval popup (who, number, where the number came from) and only after you approve opens Phone Link; you press the
  green call button yourself. Off by default (`assisted_dialing`, Settings > Phone calls); `jarvis dial <name|number>`;
  the Phone calls page. Details: `docs/reviews/SWV4_H_DIALING.md`.
- **Installer, silent upgrade, update flow, rollback**: updates are offered, never forced; a silent setup run upgrades
  in place and keeps the previous installer; a downgrade is refused; the Updates page and `jarvis update --rollback`
  restore the previous version; data migrations back up first. Details: `docs/reviews/SWV4_L_INSTALLER.md`,
  `docs/INSTALL_TEST.md`.

Integration decisions: the popup label for the dialing recipient is the shared "Recipient" / "נמען"; the bridge
payload check runs both the F sidecar `.sha256` check and the L pin check (an unpinned bridge in the stage is a finding
until `packaging/bridge_pin.json` is added by the bridge build).
