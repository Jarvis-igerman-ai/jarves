"""Owner questions are visible (6 pending -> count 6 in the console payload) and owner gates get one question each."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarves.workflow import autopilot_watch as aw
from jarves.workflow.store import Store


def _open(snapshot):
    """Same rule as the console UI (static/app.js) and the claude-j status line: field is 'status'."""
    return [q for q in snapshot["questions"] if q["status"] in {"pending", "deferred"}]


class OwnerQuestions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "w.sqlite3")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def task(self, ident, **extra):
        return self.store.create_task({"id": ident, "title": ident, "lane": "features", "version": "V4.5.1",
                                       "acceptance": [{"id": "C1", "label": "ok"}], **extra})

    def test_six_pending_questions_are_counted_six(self):
        for i in range(6):
            self.task(f"t{i}")
            self.store.ask({"id": f"q-{i}", "task_id": f"t{i}", "title": "?", "blocks": False})
        snap = self.store.snapshot()
        self.assertEqual({q["status"] for q in snap["questions"]}, {"pending"})
        self.assertTrue(all("state" not in q for q in snap["questions"]))
        self.assertEqual(len(_open(snap)), 6)
        self.store.answer("q-0", "ok")
        self.assertEqual(len(_open(self.store.snapshot())), 5)

    def test_gate_questions_once_capped_and_non_blocking(self):
        for i in range(20):
            self.task(f"g{i:02d}", owner_gate="צריך מכשיר אמיתי", execution_allowed=False, version="V5" if i < 3 else "V4.5.1")
        self.task("free", execution_allowed=False)  # no gate: no question
        self.task("open-task")                      # runnable: no question
        first = aw.gate_questions_step(self.store)
        self.assertEqual(len(first), 15)
        self.assertEqual(first[0], "g03")           # V4.5.1 before V5
        self.assertEqual(len(aw.gate_questions_step(self.store)), 5)
        self.assertEqual(aw.gate_questions_step(self.store), [])
        snap = self.store.snapshot()
        gates = [q for q in snap["questions"] if q["id"].startswith("gate-")]
        self.assertEqual(len(gates), 20)
        self.assertTrue(all(q["blocks"] is False and q["status"] == "pending" for q in gates))
        self.assertIn("צריך מכשיר אמיתי", gates[0]["details"])
        self.assertTrue(gates[0]["title"].startswith("צריך אותך: "))
        self.assertEqual(gates[0]["choices"][0]["label"], "לתזמן להיום")
        self.assertTrue(gates[0]["choices"][0]["recommended"])
        self.assertEqual(gates[0]["safe_default"], "שום דבר לא נעשה ולא מאושר בלעדיך")
        self.assertEqual({q["group"] for q in gates}, {"gate:V4.5.1", "gate:V5"})  # one card per version
        self.assertTrue(all(t["state"] == "queued" for t in snap["tasks"]))

    def test_answered_gate_is_not_recreated(self):
        self.task("g", owner_gate="approve", execution_allowed=False)
        self.assertEqual(aw.gate_questions_step(self.store), ["g"])
        self.store.answer("gate-g", "Skip for this version")
        self.assertEqual(aw.gate_questions_step(self.store), [])


if __name__ == "__main__":
    unittest.main()
