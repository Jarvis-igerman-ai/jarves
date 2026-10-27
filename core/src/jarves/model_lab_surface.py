"""Dashboard routes of the Model Lab page (V7 M4, #418). READ ONLY: the page lists archived runs and compares models;
it never starts a run (that stays `jarvis modellab run`, the owner's own command). No key is ever read or returned.
Local computer only, like the camera section."""
from __future__ import annotations

from . import model_lab as ml


def _local_only(ctx):
    who = getattr(ctx, "principal", None)
    if who is not None and who.get("kind") != "local":
        return 403, {"error": "the model lab page works only on this computer"}
    return None


def api_runs(ctx, m, body):
    refused = _local_only(ctx)
    if refused:
        return refused
    return 200, {"runs": ml.list_runs(ctx.home), "max_run_cap_usd": ml.MAX_RUN_CAP_USD, "tasks": len(ml.TASKS)}


def api_compare(ctx, m, body):
    """GET /api/modellab/compare/<id>[,<id>...][?paid=1 is not supported: the paid view is the same call with /all]."""
    refused = _local_only(ctx)
    if refused:
        return refused
    ids = [i for i in m.group(2).split(",") if i][:6]
    reports = {i: r for i in ids if (r := ml.load_run(ctx.home, i)) is not None}
    if not reports:
        return 404, {"error": "no such run"}
    return 200, ml.compare(reports, free_only=m.group(1) != "all")


def dashboard_routes(register_route) -> None:
    register_route("GET", r"/api/modellab/runs", api_runs)
    register_route("GET", r"/api/modellab/(compare|all)/([0-9,\-]+)", api_compare)
