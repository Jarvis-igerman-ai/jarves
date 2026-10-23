# plugins.d: how a feature module is registered

Add `plugins.d/<feature>.toml` (see `jarves/plugin_registry.py`) instead of editing `PLUGIN_MODULES` in `dashboard.py`,
`ADDON_MODULES` in `setup_wizard.py` or the command table at the end of `cli.py`:

    dashboard = ["jarves.my_feature"]
    addons    = ["jarves.my_feature"]
    cli       = ["jarves.my_feature_cli"]     # add_cli(sub) + COMMANDS = {"name": fn}

Two PRs that each add a file here never conflict; two PRs that each add a line to a shared tuple always did.
