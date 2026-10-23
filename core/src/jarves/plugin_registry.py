"""Plugin lists in one small file per feature (plugins.d/<feature>.toml) instead of edits to shared tuples.

A new feature module used to be added by hand to `dashboard.PLUGIN_MODULES`, `setup_wizard.ADDON_MODULES` and to the
command table and `add_cli` calls at the end of `cli.py`: every PR touched those lines and conflicted (30-40 of ~150
merges touched dashboard.py / cli.py). Now the feature ships `plugins.d/<feature>.toml`:

    dashboard = ["jarves.my_feature"]     # imported by the dashboard server (registers routes / pages / features)
    addons    = ["jarves.my_feature"]     # imported by the setup wizard (registers features for the first-run offer)
    cli       = ["jarves.my_feature_cli"] # has add_cli(sub) (argparse) and COMMANDS = {"name": fn(args, cfg, home)}

The older entries stay in the tuples where they are; the files only ADD modules, in file-name order, after those.
"""
from __future__ import annotations

import importlib
import tomllib
from pathlib import Path

DIR = Path(__file__).with_name("plugins.d")
KINDS = ("dashboard", "addons", "cli")


def names(kind: str, directory: Path | None = None) -> tuple[str, ...]:
    """Module names listed for `kind` in the plugin files, in file-name order, without repeats."""
    assert kind in KINDS, kind
    out: list[str] = []
    for f in sorted((directory or DIR).glob("*.toml")):
        for n in tomllib.loads(f.read_text(encoding="utf-8")).get(kind, []):
            if n not in out:
                out.append(n)
    return tuple(out)


def modules(kind: str, directory: Path | None = None) -> list:
    return [importlib.import_module(n) for n in names(kind, directory)]
