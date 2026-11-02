"""#789 item 3: /api/report never runs gh on the request path; the GitHub list is cached for 5 minutes."""
import threading
import time
import unittest
from datetime import datetime
from unittest.mock import patch

from jarves.workflow import report


NOON = datetime(2026, 11, 2, 12, 0, tzinfo=report.TZ).timestamp()  # clock-free, mid-day


class ReportGhCacheTests(unittest.TestCase):
    def setUp(self):
        report._GH_CACHE.clear()
        self.addCleanup(report._GH_CACHE.clear)
        self.calls = []

    def fetch(self, since):
        self.calls.append(since)
        return True, [{"number": 1, "title": "old", "merged_at": since + 60},
                      {"number": 2, "title": "new", "merged_at": since + 7200}]

    def test_first_call_returns_at_once_and_refreshes_in_background(self):
        release = threading.Event()

        def slow(since):
            release.wait(5)
            return self.fetch(since)

        now = NOON
        start = time.perf_counter()
        self.assertEqual(report.gh_cached(now - 3600, now=now, fetch=slow), (False, []))
        self.assertLess(time.perf_counter() - start, 0.2)  # the request does not wait for gh
        release.set()
        for _ in range(100):
            if self.calls and not any(e.get("busy") for e in report._GH_CACHE.values()):
                break
            time.sleep(0.02)
        ok, rows = report.gh_cached(now - 3600, now=now, fetch=slow)
        self.assertTrue(ok)
        self.assertTrue(all(r["merged_at"] >= now - 3600 for r in rows))

    def test_fresh_cache_is_reused_and_filtered_and_stale_refreshes_once(self):
        now = NOON
        report.gh_cached(now - 10, now=now, fetch=self.fetch, wait=True)
        day_start = self.calls[0]
        ok, rows = report.gh_cached(day_start + 3600, now=now + 60, fetch=self.fetch)
        self.assertTrue(ok)
        self.assertEqual([r["number"] for r in rows], [2])
        self.assertEqual(len(self.calls), 1)  # within 5 minutes: no second gh call
        with patch.object(report.threading, "Thread") as thread:
            report.gh_cached(now - 10, now=now + report.GH_TTL_S + 1, fetch=self.fetch)
            report.gh_cached(now - 10, now=now + report.GH_TTL_S + 2, fetch=self.fetch)
        self.assertEqual(thread.call_count, 1)  # a refresh already running is not started again

    def test_server_report_route_uses_the_cache(self):
        from jarves.workflow import server
        src = open(server.__file__, encoding="utf-8").read()
        route = src[src.index('path == "/api/report"'):src.index('path == "/api/messages"')]
        self.assertIn("gh=gh_cached", route)


if __name__ == "__main__":
    unittest.main()
