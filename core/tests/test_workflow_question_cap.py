"""#760: at most 5 open owner question cards at once, most blocking first; nothing is hidden irrecoverably."""
import tempfile
import unittest
from pathlib import Path

from jarves.workflow.store import QUESTION_CAP, Store, questions_view


def q(ident, task, created, **extra):
    return {"id": ident, "task_id": task, "created_at": created, "status": "pending", "blocks": True, **extra}


class QuestionCapTests(unittest.TestCase):
    def test_order_is_blocked_tasks_then_age_and_cap_splits_shown_and_later(self):
        tasks = [{"id": "a", "depends_on": []}, {"id": "b", "depends_on": ["a"]}, {"id": "c", "depends_on": ["b"]},
                 {"id": "d", "depends_on": []}, {"id": "e", "depends_on": []}]
        qs = [q("old-lone", "d", 1), q("new-lone", "e", 9), q("root", "a", 5)]
        qs += [q(f"x{i}", f"x{i}", 2 + i) for i in range(6)]
        view = questions_view(qs, tasks)
        keys = [c["key"] for c in view["shown"]]
        self.assertEqual(keys[0], "q:root")                    # holds up a, b, c = 3 tasks
        self.assertEqual(keys[1], "q:old-lone")                # ties: oldest first
        self.assertEqual(len(view["shown"]), QUESTION_CAP)
        self.assertEqual(view["cards"], 9)
        self.assertEqual(len(view["later"]), 4)
        self.assertEqual(view["shown"][0]["blocked_tasks"], 3)
        every = [i for c in view["shown"] + view["later"] for i in c["ids"]]
        self.assertEqual(sorted(every), sorted(x["id"] for x in qs))   # nothing dropped
        self.assertEqual(view["open"], 9)
        self.assertEqual(view["shown_open"] + view["later_open"], 9)

    def test_gate_group_is_one_card_and_closed_questions_are_ignored(self):
        qs = [q(f"g{i}", f"t{i}", i, group="gate:V5") for i in range(4)] + [q("lone", "z", 1)]
        qs.append({**q("done", "d", 1), "status": "answered"})
        view = questions_view(qs, [])
        self.assertEqual(view["cards"], 2)
        self.assertEqual(view["open"], 5)
        self.assertIn("group:gate:V5", [c["key"] for c in view["shown"]])
        self.assertNotIn("done", [i for c in view["shown"] + view["later"] for i in c["ids"]])

    def test_non_blocking_question_does_not_count_tasks_and_small_sets_have_no_later(self):
        view = questions_view([q("n", "t", 1, blocks=False)], [])
        self.assertEqual(view["shown"][0]["blocked_tasks"], 0)
        self.assertEqual(view["later"], [])

    def test_snapshot_carries_view_and_leaves_statuses_untouched(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / "w.db")
            try:
                for i in range(8):
                    store.create_task({"id": f"t{i}", "title": f"T{i}", "lane": "bugs"})
                    store.ask({"task_id": f"t{i}", "title": f"Q{i}"})
                before = [(x["id"], x["status"], x.get("answer")) for x in store.snapshot()["questions"]]
                state = store.snapshot()
                self.assertEqual(len(state["questions_view"]["shown"]), 5)
                self.assertEqual(len(state["questions_view"]["later"]), 3)
                self.assertEqual(before, [(x["id"], x["status"], x.get("answer")) for x in state["questions"]])
                self.assertEqual(len(state["questions"]), 8)
            finally:
                store.close()

    def test_dashboard_folds_the_rest_and_overview_shows_n_of_m(self):
        base = Path(__file__).resolve().parents[1] / "src" / "jarves" / "workflow" / "static"
        app = (base / "app.js").read_text(encoding="utf-8")
        overview = (base / "overview.js").read_text(encoding="utf-8")
        self.assertIn("questions-later", app)
        self.assertIn("עוד ${n} ממתינות", app)
        self.assertIn("${n} more waiting", app)
        self.assertIn("questions_view", app)
        self.assertIn("shown_open", overview)
        self.assertIn("מתוך", overview)


if __name__ == "__main__":
    unittest.main()
