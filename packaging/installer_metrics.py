"""Installer size / time budgets (SW-V4 L, hook for package M's performance gate). Python stdlib only.

    python -I packaging/installer_metrics.py measure --dist DIST [--install-s S] [--uninstall-s S] [--upgrade-s S]
                                                    [--rollback-s S] [--build-s S] [--out metrics.json]
    python -I packaging/installer_metrics.py check --metrics metrics.json [--class owner|ci] [--budgets FILE]

`measure` reads DIST/build-info.json (written by build_windows.ps1) and the timings that packaging/smoke_windows.ps1
passes in, and writes {"installer_size_mb": ..., "silent_install_s": ...}. `check` compares them with the
`[installer.<name>]` tables of core/perf_budgets.toml (the same owner / ci ceilings as the other budgets; a metric that was
not measured is "n/a", never a failure). core/tools/perf_gate.py --installer-json FILE runs the same check and merges the
rows into its report, so the M gate fails when the installer grows or gets slow.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from pathlib import Path

BUDGETS = Path(__file__).resolve().parents[1] / "core" / "perf_budgets.toml"
TIMINGS = {"install_s": "silent_install_s", "uninstall_s": "silent_uninstall_s", "upgrade_s": "silent_upgrade_s",
           "rollback_s": "silent_rollback_s", "build_s": "build_s"}


def measure(dist: Path, **timings: float | None) -> dict:
    info = json.loads((Path(dist) / "build-info.json").read_text(encoding="utf-8-sig"))
    exe = Path(dist) / str(info["installer"])
    size = exe.stat().st_size if exe.is_file() else int(info["size"])
    out = {"installer_size_mb": round(size / (1024 * 1024), 1)}
    if info.get("build_s") is not None and timings.get("build_s") is None:
        timings["build_s"] = info["build_s"]
    for key, name in TIMINGS.items():
        v = timings.get(key)
        if v is not None:
            out[name] = round(float(v), 1)
    return out


def load_budgets(path: Path = BUDGETS) -> dict:
    return tomllib.loads(Path(path).read_text(encoding="utf-8")).get("installer", {})


def check(measured: dict, budgets: dict, cls: str) -> list[dict]:
    """Rows shaped like core/tools/perf_gate.check(): {metric, value, max, unit, status (ok|FAIL|n/a), baseline}."""
    rows = []
    for name, b in budgets.items():
        v, mx = measured.get(name), b[f"{cls}_max"]
        rows.append({"metric": f"installer.{name}", "value": v, "max": mx, "unit": b.get("unit", ""),
                     "status": "n/a" if v is None else ("ok" if v <= mx else "FAIL"), "baseline": b.get("baseline")})
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="installer_metrics.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("measure")
    m.add_argument("--dist", type=Path, required=True)
    for k in TIMINGS:
        m.add_argument("--" + k.replace("_", "-"), type=float)
    m.add_argument("--out", type=Path)
    c = sub.add_parser("check")
    c.add_argument("--metrics", type=Path, required=True)
    c.add_argument("--class", dest="cls", choices=("owner", "ci"))
    c.add_argument("--budgets", type=Path, default=BUDGETS)
    a = ap.parse_args(argv)
    if a.cmd == "measure":
        res = measure(a.dist, **{k: getattr(a, k) for k in TIMINGS})
        text = json.dumps(res, indent=1)
        if a.out:
            a.out.write_text(text, encoding="utf-8")
        print(text)
        return 0
    cls = a.cls or ("ci" if os.environ.get("CI") else "owner")
    rows = check(json.loads(a.metrics.read_text(encoding="utf-8")), load_budgets(a.budgets), cls)
    for r in rows:
        print(f"{r['metric']:<34} {r['value']!s:>8} / {r['max']!s:<6} {r['unit']:<3} {r['status']}")
    bad = [r for r in rows if r["status"] == "FAIL"]
    print("INSTALLER BUDGET: " + ("FAIL " + ", ".join(r["metric"] for r in bad) if bad else "OK"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
