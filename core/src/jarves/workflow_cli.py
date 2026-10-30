"""Discoverable `jarvis workflow` entry point; no Core needed for the service."""
from __future__ import annotations


def add_cli(sub) -> None:
    import argparse
    p = sub.add_parser("workflow", help="live development console, owner question inbox and external supervisor")
    p.add_argument("workflow_args", nargs=argparse.REMAINDER, help="serve | supervise | open | status | seed | sync | agent | report | next | ask | check")


def cmd_workflow(args, cfg, home) -> int:
    from .workflow.__main__ import main
    argv = args.workflow_args or ["status"]
    if "--profile-home" not in argv:
        argv = [*argv, "--profile-home", str(home)]
    return main(argv)


COMMANDS = {"workflow": cmd_workflow}
