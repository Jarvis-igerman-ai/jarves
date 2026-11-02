"""Hostile or malformed usage.json is 'unknown', never a throttle value or a crash (#776 follow-up to #877)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarves.workflow import usage

NOW = 1_000_000.0


class HostileSnapshotTests(unittest.TestCase):
    def read(self, text: str, size_pad: int = 0):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "usage.json"
            p.write_text(text + " " * size_pad, encoding="utf-8")
            return usage.read_snapshot(p, NOW)

    def test_good_snapshot(self):
        self.assertEqual(self.read('{"weekly_pct": 83, "ts": %s}' % (NOW - 5))["pct"], 83.0)

    def test_hostile_values_are_unknown(self):
        for text in (
            '{"weekly_pct": 90, "ts": NaN}', '{"weekly_pct": 90, "ts": Infinity}', '{"weekly_pct": NaN, "ts": %s}' % NOW,
            '{"weekly_pct": true, "ts": %s}' % NOW, '{"weekly_pct": "90", "ts": %s}' % NOW,
            '{"weekly_pct": 90, "ts": %s}' % (NOW + 5000),          # future timestamp
            '{"weekly_pct": 1e999, "ts": %s}' % NOW, '[1, 2]', '[' * 100000, '{"weekly_pct": 90}', '',
        ):
            self.assertIsNone(self.read(text), text[:40])

    def test_oversized_file_is_unknown(self):
        self.assertIsNone(self.read('{"weekly_pct": 90, "ts": %s}' % NOW, size_pad=usage.MAX_BYTES))

    def test_bad_five_hour_is_dropped(self):
        got = self.read('{"weekly_pct": 50, "ts": %s, "five_hour_pct": NaN}' % NOW)
        self.assertEqual((got["pct"], got["five_hour_pct"]), (50.0, None))


if __name__ == "__main__":
    unittest.main()
