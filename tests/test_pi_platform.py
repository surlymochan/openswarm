"""Pi isolation defaults and executable selection by platform."""
import json
import os
import tempfile
import unittest
from unittest import mock

import _load

xs = _load.load()


class PiPlatformTests(unittest.TestCase):
    def test_pi_bin_platform_and_search_order(self):
        isolated = os.path.join(os.path.dirname(os.path.realpath(xs.__file__)), 'pi-isolated')
        with mock.patch.object(xs, 'envvar', return_value=None), \
                mock.patch.object(xs.os.path, 'isfile', return_value=True), \
                mock.patch.object(xs.os, 'access', return_value=True), \
                mock.patch.object(xs.shutil, 'which', return_value='/path/pi'), \
                mock.patch.object(xs.os.path, 'expanduser', return_value='/home/pi'):
            with mock.patch.object(xs.sys, 'platform', 'linux'):
                self.assertEqual(xs.pi_bin(), isolated)
            with mock.patch.object(xs.sys, 'platform', 'darwin'):
                self.assertEqual(xs.pi_bin(), '/path/pi')
                with mock.patch.object(xs.shutil, 'which', return_value=None):
                    self.assertEqual(xs.pi_bin(), '/home/pi')
                with mock.patch.object(xs, 'envvar', return_value='/override/pi'):
                    self.assertEqual(xs.pi_bin(), '/override/pi')

    def test_plan_and_review_respect_platform_and_explicit_policy(self):
        policy = {'difficulty_map': {'3': {'tier': 'test'}}}
        reviewer = {'backend': 'pi', 'binary': '/usr/bin/pi'}
        for platform, required in [('darwin', False), ('linux', True)]:
            for configured in (None, True, False):
                with self.subTest(platform=platform, configured=configured), \
                        mock.patch.object(xs.sys, 'platform', platform), \
                        mock.patch.object(xs, 'pi_bin', return_value='/usr/bin/pi'), \
                        mock.patch.object(xs, 'breakers', return_value={}):
                    actual = dict(policy, pi={} if configured is None else {'require_isolation': configured})
                    isolation = required if configured is None else configured
                    decision, reason = xs.plan_pi({'difficulty': 3}, {}, actual, True, [])
                    if isolation:
                        self.assertIsNone(decision)
                        self.assertIn('isolated launcher', reason)
                        with self.assertRaisesRegex(ValueError, 'isolated launcher'):
                            xs.review_command(reviewer, 'review', '/repo', '/tmp', actual)
                    else:
                        self.assertEqual(decision['backend'], 'pi')
                        command = xs.review_command(reviewer, 'review', '/repo', '/tmp', actual)
                        self.assertIn('read,grep,find,ls', command)

    def test_lane_config_and_rpc_respect_platform_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'lanes.json')
            lane = {'name': 'test', 'backend': 'pi', 'binary': '/usr/bin/pi',
                    'model': 'test', 'repo': tmp}
            with open(path, 'w') as stream:
                json.dump({'lanes': [lane]}, stream)
            for platform, configured, isolation in [('darwin', None, False), ('linux', None, True),
                                                      ('darwin', True, True), ('linux', False, False)]:
                policy = {'pi': {} if configured is None else {'require_isolation': configured}}
                with self.subTest(platform=platform, configured=configured), \
                        mock.patch.object(xs.sys, 'platform', platform), \
                        mock.patch.object(xs, 'load_policy', return_value=policy), \
                        mock.patch.object(xs, 'envvar', side_effect=lambda name, *args: path if name == 'LANES_FILE' else None), \
                        mock.patch.object(xs.os, 'access', return_value=True), \
                        mock.patch.object(xs, 'configure_executor_environment', return_value=(tmp, {})), \
                        mock.patch.object(xs, 'lane_root', return_value=tmp), \
                        mock.patch.object(xs.subprocess, 'Popen') as popen, \
                        mock.patch.object(xs.threading, 'Thread'):
                    if isolation:
                        with self.assertRaisesRegex(ValueError, 'isolated launcher'):
                            xs.lane_config()
                        with self.assertRaisesRegex(RuntimeError, 'isolated launcher'):
                            xs.LaneChild(lane, tmp, policy)
                        popen.assert_not_called()
                    else:
                        self.assertEqual(xs.lane_config()[0]['binary'], '/usr/bin/pi')
                        xs.LaneChild(lane, tmp, policy)
                        self.assertEqual(popen.call_args.args[0][0], '/usr/bin/pi')
