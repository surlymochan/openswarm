"""Capacity queue time must not consume the executor's run budget."""
import pathlib
import tempfile
import unittest
from unittest import mock

import _load

xs = _load.load()


class QueueBudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.rdir = pathlib.Path(self.tmp.name, 'run')
        self.rdir.mkdir()
        self.clock = [100.0]

    def reserve_after_long_wait(self, *args, **kwargs):
        self.clock[0] += 180
        self.assertTrue(kwargs['keep_waiting']())
        return True

    def test_direct_run_gets_full_budget_after_capacity_wait(self):
        xs.jsave(str(self.rdir / 'status.json'), {'queued_at': 100.0, 'budget_minutes': 1})
        executor = mock.Mock(return_value=(0, {'status': 'done'}, {'usage': {}}, ''))
        with mock.patch.object(xs, 'NOW', side_effect=lambda: self.clock[0]), \
             mock.patch.object(xs, 'reserve_provider_slot', side_effect=self.reserve_after_long_wait), \
             mock.patch.dict(xs.EXECUTORS, {'pi': executor}):
            attempts, final, error = xs.run_executor_with_rate_limit(
                'pi', {'backend': 'pi', 'model': 'fake'}, {}, 'task', self.tmp.name,
                str(self.rdir), 160.0, xs.load_policy())
        self.assertIsNone(error)
        self.assertEqual(final['status'], 'done')
        self.assertEqual(executor.call_args.args[5], 60.0)
        status = xs.jload(str(self.rdir / 'status.json'), {})
        self.assertEqual(status['queued_seconds'], 180.0)
        self.assertEqual(status['deadline'], 340.0)
        self.assertEqual(attempts[0]['error_class'], None)

    def test_lane_gets_full_budget_after_capacity_wait(self):
        lane = {'name': 'L1', 'backend': 'pi', 'model': 'fake'}
        spec = {'run_id': 'run', 'task': 'task', 'started': 100.0,
                'queued_at': 100.0, 'budget_minutes': 1}
        workspace = {'repo': self.tmp.name, 'root': self.tmp.name, 'path': self.tmp.name}
        child = mock.Mock()
        child.alive.return_value = True
        turn = mock.Mock(return_value=({'status': 'done'}, {}, None, 0, 'events', None))
        finalize = mock.Mock(side_effect=lambda lane, spec, *args, **kwargs: dict(spec))
        with mock.patch.object(xs, 'RUNS', self.tmp.name), \
             mock.patch.object(xs, 'NOW', side_effect=lambda: self.clock[0]), \
             mock.patch.object(xs, 'lane_prepare_workspace', return_value=workspace), \
             mock.patch.object(xs, 'run_workspace_prepare', return_value=None), \
             mock.patch.object(xs, 'add_lessons_to_prompt', side_effect=lambda prompt, *a: (prompt, [])), \
             mock.patch.object(xs, 'add_patterns_to_prompt', side_effect=lambda prompt, *a: (prompt, [])), \
             mock.patch.object(xs, 'reserve_provider_slot', side_effect=self.reserve_after_long_wait), \
             mock.patch.object(xs, 'lane_control_messages'), \
             mock.patch.object(xs, 'lane_run_turn', turn), \
             mock.patch.object(xs, 'lane_finalize_run', finalize):
            result = xs.lane_do_run(lane, spec, child, None, {})
        self.assertEqual(result['started'], 280.0)
        self.assertEqual(result['queued_seconds'], 180.0)
        self.assertEqual(xs.jload(str(self.rdir / 'status.json'), {})['deadline'], 340.0)
        self.assertEqual(finalize.call_args.args[5], None)
        turn.assert_called_once()

    def test_retry_capacity_wait_preserves_remaining_budget(self):
        xs.jsave(str(self.rdir / 'status.json'), {'queued_at': 100.0, 'budget_minutes': 1})
        timeouts = []

        def execute(*args):
            timeouts.append(args[5])
            if len(timeouts) == 1:
                self.clock[0] += 10
                return 1, None, {'usage': {}}, '429 rate limit'
            return 0, {'status': 'done'}, {'usage': {}}, ''

        with mock.patch.object(xs, 'NOW', side_effect=lambda: self.clock[0]), \
             mock.patch.object(xs, 'reserve_provider_slot', side_effect=self.reserve_after_long_wait), \
             mock.patch.object(xs, 'trip_breaker'), \
             mock.patch.object(xs, 'rate_limit_delay', return_value=0), \
             mock.patch.object(xs, 'wait_run_rate_limit', return_value=True), \
             mock.patch.dict(xs.EXECUTORS, {'pi': execute}):
            attempts, final, error = xs.run_executor_with_rate_limit(
                'pi', {'backend': 'pi', 'model': 'fake'}, {}, 'task', self.tmp.name,
                str(self.rdir), 160.0, {'rate_limit': {'max_retries': 1}})
        self.assertIsNone(error)
        self.assertEqual(final['status'], 'done')
        self.assertEqual(timeouts, [60.0, 50.0])
        self.assertEqual(len(attempts), 2)
        self.assertEqual(xs.jload(str(self.rdir / 'status.json'), {})['queued_seconds'], 360.0)

    def test_lane_cancel_during_second_capacity_wait_counts_both_waits(self):
        lane = {'name': 'L1', 'backend': 'pi', 'model': 'fake', 'fallback_models': ['other']}
        spec = {'run_id': 'run', 'task': 'task', 'started': 100.0,
                'queued_at': 100.0, 'budget_minutes': 1}
        workspace = {'repo': self.tmp.name, 'root': self.tmp.name, 'path': self.tmp.name}
        child = mock.Mock()
        child.alive.return_value = True

        def reserve(*args, **kwargs):
            self.clock[0] += 180
            if self.clock[0] > 300:
                spec['abort_requested'] = True
                return False
            return True

        turn = mock.Mock(return_value=(None, {}, 'exec_error', 1, 'events', 'model not available'))
        finalize = mock.Mock(side_effect=lambda lane, spec, *args, **kwargs: dict(spec))
        with mock.patch.object(xs, 'RUNS', self.tmp.name), \
             mock.patch.object(xs, 'NOW', side_effect=lambda: self.clock[0]), \
             mock.patch.object(xs, 'lane_prepare_workspace', return_value=workspace), \
             mock.patch.object(xs, 'run_workspace_prepare', return_value=None), \
             mock.patch.object(xs, 'add_lessons_to_prompt', side_effect=lambda prompt, *a: (prompt, [])), \
             mock.patch.object(xs, 'add_patterns_to_prompt', side_effect=lambda prompt, *a: (prompt, [])), \
             mock.patch.object(xs, 'reserve_provider_slot', side_effect=reserve), \
             mock.patch.object(xs, 'lane_run_turn', turn), \
             mock.patch.object(xs, 'trip_breaker'), \
             mock.patch.object(xs, 'lane_finalize_run', finalize):
            result = xs.lane_do_run(lane, spec, child, None, {})
        self.assertEqual(result['queued_seconds'], 360.0)
        self.assertEqual(xs.jload(str(self.rdir / 'status.json'), {})['queued_seconds'], 360.0)
        self.assertEqual(finalize.call_args.args[5], 'stopped')
        turn.assert_called_once()


if __name__ == '__main__':
    unittest.main()
