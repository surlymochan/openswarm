"""Copilot lane must not claim read-only when writes remain possible."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _load

xs = _load.load()


class CopilotReadonlyTests(unittest.TestCase):
    def test_readonly_profile_can_use_copilot(self):
        from unittest import mock
        with mock.patch.object(xs, 'copilot_bin', return_value='/bin/true'):
            decision, _ = xs.plan_copilot({'read_only': True, 'difficulty': 3}, {}, xs.load_policy(), True, [])
        self.assertEqual(decision['backend'], 'copilot')

if __name__ == '__main__':
    unittest.main()
