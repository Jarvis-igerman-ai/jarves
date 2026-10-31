import unittest

from jarves.workflow.autopilot_grant import grant_state

NOW = 1_000_000.0


class GrantStateTests(unittest.TestCase):
    def test_states(self):
        self.assertEqual(grant_state({"ok": True, "grant": None}, NOW)[0], "none")
        self.assertEqual(grant_state(None, NOW)[0], "none")
        self.assertEqual(grant_state({"grant": {"expires_at": NOW - 5}}, NOW)[0], "expired")
        self.assertEqual(grant_state({"grant": {"expires_at": NOW + 3600}}, NOW)[0], "soon")
        self.assertEqual(grant_state({"grant": {"expires_at": NOW + 86400}}, NOW)[0], "valid")
        self.assertEqual(grant_state({"grant": {"expires_at": NOW + 3 * 86400}}, NOW), ("valid", NOW + 3 * 86400))


if __name__ == "__main__":
    unittest.main()
