"""Group every WARNING / ERROR line of the campaign Core logs (all test homes) by component / code / detail fields.

    .venv/Scripts/python.exe tools/campaign_logsum.py OUT.json LOG [LOG ...]

The campaign report (campaign_report_v4.py) classifies each group as expected / noise / bug."""
from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

KEYS = ("tool", "result_code", "reason", "err_code", "feature", "job", "kind", "exc_type")


def main() -> int:
    out, logs = sys.argv[1], sys.argv[2:]
    groups: dict[tuple, dict] = collections.OrderedDict()
    for lp in logs:
        for line in Path(lp).read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("level") not in ("WARNING", "ERROR", "CRITICAL"):
                continue
            detail = " ".join(f"{k}={d[k]}" for k in KEYS if d.get(k) not in (None, ""))
            key = (d["level"], d["component"], d["code"], re.sub(r"\d+", "N", d.get("msg", ""))[:70], detail[:90])
            g = groups.setdefault(key, {"count": 0, "first": d.get("ts", "")[11:19], "homes": set()})
            g["count"] += 1
            g["homes"].add(Path(lp).parts[-4] if len(Path(lp).parts) > 4 else Path(lp).name)
    rows = [{"level": k[0], "component": k[1], "code": k[2], "msg": k[3], "detail": k[4], "count": v["count"], "first": v["first"]}
            for k, v in groups.items()]
    Path(out).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    for r in rows:
        print(f"{r['count']:>4} {r['level']:<7} {r['component']}/{r['code']}  {r['detail']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
