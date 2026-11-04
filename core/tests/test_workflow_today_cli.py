"""#732: `workflow today` prints a short 'what needs me now' summary (claude-j keeps it on screen)."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from jarves.workflow import today
from jarves.workflow.__main__ import main
from jarves.workflow.store import Store

NOW = 1_800_000_000.0


def snap(tasks=(), questions=()):
    return {"tasks": list(tasks), "questions": list(questions), "agents": []}


class TodayView(unittest.TestCase):
    def test_empty_ledger_is_calm(self):
        out = today.text(today.build(snap(), NOW))
        self.assertIn("צריך אותך: כלום כרגע", out)
        self.assertIn("תקוע: אין", out)
        self.assertIn("אין עובד פעיל · 0 משימות מוכנות בתור", out)
        self.assertLessEqual(len(out.splitlines()), 10)

    def test_oldest_questions_first_top_three_with_minutes(self):
        qs = [{"id": f"q{i}", "title": f"question {i}", "status": "pending", "minutes": i, "created_at": NOW - i * 3600}
              for i in range(1, 6)]
        qs.append({"id": "qa", "title": "answered one", "status": "answered", "created_at": NOW - 99999})
        view = today.build(snap(questions=qs), NOW)
        self.assertEqual(view["needs_total"], 5)
        self.assertEqual([q["id"] for q in view["needs"]], ["q5", "q4", "q3"])
        out = today.text(view)
        self.assertIn("1. question 5 · כ־5 דק' · לפני 5 שעות", out)
        self.assertIn("ועוד 2 בלוח הבקרה", out)
        self.assertNotIn("answered one", out)
        self.assertIn("לענות בלשונית שאלות", out)
        self.assertLessEqual(len(out.splitlines()), 10)

    def test_held_answer_still_needs_owner_and_text_is_never_printed(self):
        q = {"id": "qh", "title": "held", "status": "answered", "held": True, "answer": "SECRET-TYPED", "created_at": NOW}
        out = today.text(today.build(snap(questions=[q]), NOW))
        self.assertIn("צריך אותך: 1", out)
        self.assertNotIn("SECRET-TYPED", out)

    def test_running_blocked_and_long_titles(self):
        tasks = [{"id": f"R{i}", "title": "x", "state": "running"} for i in range(6)]
        tasks += [{"id": "B1", "title": "b", "state": "blocked", "priority": 1, "updated_at": NOW},
                  {"id": "Q1", "title": "q", "state": "queued"},
                  {"id": "Q2", "title": "q", "state": "queued", "execution_allowed": False}]
        qs = [{"id": "q", "title": "y" * 200, "status": "pending", "created_at": NOW - 30}]
        view = today.build(snap(tasks, qs), NOW)
        out = today.text(view)
        self.assertIn("עובד עכשיו: 6 · R0, R1, R2, R3 ועוד 2", out)
        self.assertIn("תקוע: 1 · B1", out)
        self.assertEqual(view["queued"], 1)
        self.assertTrue(all(len(line) < 120 for line in out.splitlines()))
        self.assertIn("עכשיו", out)


class TodayCli(unittest.TestCase):
    def test_cli_text_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "wf"
            store = Store(home / "workflow.db")
            store.create_task({"id": "T1", "title": "do it", "version": "V4.5.1", "lane": "bugs"})
            store.ask({"task_id": "T1", "title": "pick one", "minutes": 2})
            store.close()
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(main(["today", "--home", str(home)]), 0)
            self.assertIn("pick one · כ־2 דק'", buf.getvalue())
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(main(["today", "--home", str(home), "--json"]), 0)
            data = json.loads(buf.getvalue())
            self.assertEqual(data["needs_total"], 1)
            self.assertEqual(data["needs"][0]["task_id"], "T1")


if __name__ == "__main__":
    unittest.main()
