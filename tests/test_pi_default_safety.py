"""Unisolated Pi and unsupported read-only modes must not be routed by default."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _load

xs = _load.load()


class PiDefaultSafetyTests(unittest.TestCase):
    def test_unisolated_binary_is_not_routed(self):
        with mock.patch.object(xs, 'pi_bin', return_value='/usr/bin/pi'):
            decision, reason = xs.plan_pi({'difficulty': 3}, {}, xs.load_policy(), True, [])
        self.assertIsNone(decision)
        self.assertIn('isolated launcher', reason)

    def test_review_can_use_pi_worktree(self):
        decision, reason = xs.plan_pi({'difficulty': 3, 'read_only': True}, {}, xs.load_policy(), True, [])
        self.assertIsNone(reason)
        self.assertEqual(decision['backend'], 'pi')


if __name__ == '__main__':
    unittest.main()
