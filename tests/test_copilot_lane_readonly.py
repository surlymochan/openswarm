"""Copilot lane must not claim read-only when writes remain possible."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _load

xs = _load.load()


class CopilotReadonlyTests(unittest.TestCase):
    def test_readonly_profile_is_not_routed_to_copilot(self):
        decision, reason = xs.plan_copilot({'read_only': True, 'difficulty': 3}, {}, xs.load_policy(), True, [])
        self.assertIsNone(decision)
        self.assertIn('read-only isolation is unavailable', reason)

if __name__ == '__main__':
    unittest.main()
