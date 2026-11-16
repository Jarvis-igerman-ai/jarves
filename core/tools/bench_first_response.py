"""Report-only: cold import + dashboard start + first HTTP responses, in-process, offline, throw-away JARVES home.

    python core/tools/bench_first_response.py [--runs 3]

Complements bench_startup.py (spawn to CORE_READY). Each run is a FRESH interpreter (cold imports) that times:
import of jarves.dashboard, Dashboard(...).start(), first GET /api/health-less page (the dashboard root) and a 2nd GET.
No microphone, no network beyond 127.0.0.1, nothing billable. Prints one JSON line per run and a median line.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]

CHILD = r'''
import http.client, json, sys, time
t0 = time.perf_counter()
from jarves import dashboard
from jarves.config import Config
t_import = time.perf_counter() - t0
from pathlib import Path
home = Path(sys.argv[1])
t = time.perf_counter()
d = dashboard.Dashboard(home, Config()).start()
t_start = time.perf_counter() - t
def get():
    s = time.perf_counter()
    c = http.client.HTTPConnection("127.0.0.1", d.port, timeout=30)
    c.request("GET", "/", headers={"Cookie": f"{dashboard.COOKIE}={d.url.split('t=')[1]}"})
    r = c.getresponse(); r.read(); c.close()
    return r.status, time.perf_counter() - s
st1, first = get()
st2, second = get()
d.stop()
print(json.dumps({"import_s": round(t_import, 3), "dashboard_start_s": round(t_start, 3), "first_get_s": round(first, 3),
                  "second_get_s": round(second, 3), "status": [st1, st2]}))
'''


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs", type=int, default=3)
    a = ap.parse_args()
    rows = []
    for _ in range(a.runs):
        home = Path(tempfile.mkdtemp(prefix="jarves-firstresp-"))
        env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "OPENAI_ADMIN_KEY")}
        env.update(JARVES_HOME=str(home), PYTHONPATH=str(CORE / "src"), PYTHONIOENCODING="utf-8")
        r = subprocess.run([sys.executable, "-c", CHILD, str(home)], env=env, capture_output=True, text=True, timeout=180)
        if r.returncode:
            print(r.stderr[-800:], file=sys.stderr)
            return 1
        row = json.loads(r.stdout.strip().splitlines()[-1])
        rows.append(row)
        print(json.dumps(row), flush=True)
    med = {k: statistics.median(r[k] for r in rows) for k in rows[0] if k != "status"}
    print("MEDIAN " + json.dumps({"runs": a.runs, **med}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
