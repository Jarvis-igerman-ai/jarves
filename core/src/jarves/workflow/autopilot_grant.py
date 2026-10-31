"""Owner grant for the workflow autopilot (T2). The owner runs `workflow autopilot grant` once on the PC; the typed-code
popup is shown by the Orchestrator (never here). This module only forwards to the `autopilot` op."""
from __future__ import annotations

import json
import sys
from pathlib import Path

DEFAULT_MODELS = "claude-sonnet-5-5,claude-haiku-5-5,claude-opus-5-5"


def grant_state(status, now: float, margin: float = 86400.0) -> tuple[str, float]:
    """Classify a `grant-status` answer: none | expired | soon (expires within margin) | valid; plus expires_at."""
    grant = status.get("grant") if isinstance(status, dict) else None
    if not isinstance(grant, dict):
        return "none", 0.0
    try:
        exp = float(grant.get("expires_at") or 0)
    except (TypeError, ValueError):
        return "none", 0.0
    if exp <= now:
        return "expired", exp
    return ("soon" if exp - now < margin else "valid"), exp


def run(argv: list[str], profile_home: Path | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="workflow autopilot")
    ap.add_argument("sub", choices=["grant", "revoke", "grant-status"])
    ap.add_argument("--project", default="jarves")
    ap.add_argument("--models", default=DEFAULT_MODELS, help="comma separated, at most 3")
    ap.add_argument("--days", type=float, default=7.0, help="at most 7")
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args(argv)
    from ..config import load_config, state_dir
    from ..orchestrator import service as svc
    cfg, _ = load_config()
    home = state_dir()
    op = {"grant": "grant", "revoke": "revoke", "grant-status": "status"}[a.sub]
    r = svc.call(Path(profile_home or home), cfg, "autopilot", by="cli", action=op, project=a.project,
                 models=a.models, days=a.days, max_concurrent=a.workers)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
