"""SW-V4 campaign PASS C (voice): every existing voice scenario (1-62 of suite_voice / suite_v3_voice /
suite_v4_delegation, 71-78 WhatsApp, 79-84 recall) plus the campaign-C scenarios 63-70 (orchestrator) and 100-112
(self-diagnostics, WhatsApp read-only checks, approvals wording, popup keys, pronunciation default, wake custom
model, mute, latency), on the REAL GPT-Live with the virtual mic / speaker of suite_voice.py. SILENT and HEADLESS:
TEST-profile temp homes, fake external services (agents, WhatsApp bridge), approval popups invisible.

    <venv python> tools/suite_v4_campaign_c.py --only 1-14 --max-minutes 12
    <venv python> tools/suite_v4_campaign_c.py --list

One store + one raw report for the whole pass: docs/reviews/swv4_campaign_voice/SUITE_RAW_REPORT.md.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "tests"))
import suite_v3_voice as v3  # noqa: E402  (1-62)
import suite_v4_recall  # noqa: E402,F401  (79-84)
import suite_v4_whatsapp  # noqa: E402,F401  (71-78)
import suite_v4_campaign_c_new  # noqa: E402,F401  (63-70, 100+)

sv = v3.sv
sv.ROOT = Path(os.environ.get("TEMP", ".")) / "jarves-v4-campaign-c"
sv.STORE = sv.ROOT / "suite_results.json"
sv.REPORT = sv.REPO / "docs" / "reviews" / "swv4_campaign_voice" / "SUITE_RAW_REPORT.md"

if __name__ == "__main__":
    asyncio.run(sv.main())
