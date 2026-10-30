"""Automatic dispatch must preserve model and read-only boundaries."""
import pathlib
import runpy
import contextlib
import io
import json
import sys
import unittest
from unittest import mock

MOD = runpy.run_path(str(pathlib.Path(__file__).resolve().parents[1] / 'bin' / 'openswarm-auto'))
preference = MOD['preference']
G = MOD['main'].__globals__

class FleetAutoTests(unittest.TestCase):
    def test_task_profiles_cover_all_five_backends(self):
        expected = [('mechanical', 1, 'M1'), ('test', 2, 'K1'), ('impl', 2, 'GH1'),
                    ('impl', 3, 'C1'), ('impl', 4, 'GH2'), ('migration', 3, 'A1')]
        for kind, difficulty, lane in expected:
            self.assertEqual(preference(kind, difficulty)[0], lane)

    def test_complex_copilot_never_falls_back_to_haiku(self):
        for kind in ('impl', 'test', 'migration'):
            for difficulty in (4, 5):
                self.assertNotIn('GH1', preference(kind, difficulty))
        self.assertNotIn('GH2', preference('impl', 2))

    def test_review_uses_available_remote_lane_and_marks_read_only(self):
        def remote(req):
            if req['op'] == 'lanes':
                return [{'name':'GH1','state':'idle','backend':'copilot','model':'haiku'}]
            self.assertTrue(req['read_only'])
            return {'id':'review-1','state':'queued'}
        args = ['openswarm-auto','--type','review','--difficulty','2','--base','abcdef0',
                '--prompt','review','--verify','true']
        with mock.patch.object(sys, 'argv', args), mock.patch.dict(G, {'local_lanes':lambda: [], 'remote':remote}), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            MOD['main']()
        result = json.loads(out.getvalue())
        self.assertEqual((result['lane'], result['host'], result['read_only']), ('GH1','gocc',True))

    def test_review_uses_available_local_lane_and_marks_read_only(self):
        args = ['openswarm-auto','--type','review','--difficulty','2','--base','abcdef0','--prompt','review']
        lanes = [{'name':'K1','state':'idle','backend':'cursor','model':'composer'}]
        completed = mock.Mock(returncode=0, stdout=json.dumps({'ok':True,'id':'review-2','state':'queued'}))
        with mock.patch.object(sys, 'argv', args), mock.patch.dict(G, {'local_lanes':lambda: lanes, 'remote':lambda req: []}), \
                mock.patch.object(G['subprocess'], 'run', return_value=completed) as run, \
                contextlib.redirect_stdout(io.StringIO()) as out:
            MOD['main']()
        self.assertIn('--read-only', run.call_args.args[0])
        self.assertEqual(json.loads(out.getvalue())['host'], 'gores1')

    def test_review_with_only_pi_uses_isolated_one_shot(self):
        args = ['openswarm-auto','--type','review','--difficulty','2','--base','abcdef0','--prompt','review']
        lanes = [{'name':'M1','state':'idle','backend':'pi','model':'MiniMax'}]
        completed = mock.Mock(returncode=0, stdout=json.dumps({'id':'review-3','state':'started'}))
        with mock.patch.object(sys, 'argv', args), mock.patch.dict(G, {'local_lanes':lambda: lanes, 'remote':lambda req: []}), \
                mock.patch.object(G['subprocess'], 'run', return_value=completed) as run, \
                contextlib.redirect_stdout(io.StringIO()) as out:
            MOD['main']()
        command = run.call_args.args[0]
        self.assertEqual(command[1:3], ['run', '--type'])
        self.assertIn('--read-only', command)
        self.assertEqual(json.loads(out.getvalue())['execution_mode'], 'one-shot')

    def test_lost_remote_ack_does_not_dispatch_to_next_lane(self):
        calls = []
        def remote(req):
            calls.append(req['op'])
            if req['op'] == 'lanes':
                return [{'name': 'A1', 'state': 'idle', 'backend': 'agy', 'model': 'gemini'}]
            raise RuntimeError('gocc dispatch gateway unavailable')
        local = [{'name': 'K1', 'state': 'idle', 'backend': 'cursor', 'model': 'composer'}]
        args = ['openswarm-auto', '--type', 'migration', '--difficulty', '3', '--base', 'abcdef0', '--prompt', 'task']
        with mock.patch.object(sys, 'argv', args), mock.patch.dict(G, {'local_lanes': lambda: local, 'remote': remote}), \
                mock.patch.object(G['subprocess'], 'run') as run, self.assertRaisesRegex(RuntimeError, 'unavailable'):
            MOD['main']()
        self.assertEqual(calls, ['lanes', 'assign'])
        run.assert_not_called()

    def test_lost_local_ack_does_not_try_another_lane(self):
        lanes = [{'name': 'K1', 'state': 'idle', 'backend': 'cursor', 'model': 'composer'},
                 {'name': 'M1', 'state': 'idle', 'backend': 'pi', 'model': 'minimax'}]
        args = ['openswarm-auto', '--type', 'test', '--difficulty', '2', '--base', 'abcdef0', '--prompt', 'task']
        with mock.patch.object(sys, 'argv', args), mock.patch.dict(G, {'local_lanes': lambda: lanes, 'remote': lambda req: []}), \
                mock.patch.object(G['subprocess'], 'run', return_value=mock.Mock(stdout='', returncode=0)) as run, \
                self.assertRaisesRegex(RuntimeError, 'acknowledgement unavailable'):
            MOD['main']()
        self.assertEqual(run.call_count, 1)

    def test_explicit_remote_busy_falls_back_to_local_lane(self):
        def remote(req):
            if req['op'] == 'lanes':
                return [{'name': 'A1', 'state': 'idle', 'backend': 'agy', 'model': 'gemini'}]
            raise G['LaneBusy']('remote lane is busy')
        local = [{'name': 'K1', 'state': 'idle', 'backend': 'cursor', 'model': 'composer'}]
        args = ['openswarm-auto', '--type', 'migration', '--difficulty', '3', '--base', 'abcdef0', '--prompt', 'task']
        reply = mock.Mock(stdout=json.dumps({'ok': True, 'id': 'fallback-1', 'state': 'queued'}), returncode=0)
        with mock.patch.object(sys, 'argv', args), mock.patch.dict(G, {'local_lanes': lambda: local, 'remote': remote}), \
                mock.patch.object(G['subprocess'], 'run', return_value=reply) as run, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            MOD['main']()
        self.assertEqual(json.loads(output.getvalue())['lane'], 'K1')
        self.assertEqual(run.call_count, 1)

    def test_gateway_distinguishes_busy_from_unknown_failure(self):
        busy = {'ok': False, 'result': {'ok': False, 'error': 'lane A1 is busy (busy)'}}
        with mock.patch.object(G['subprocess'], 'run', return_value=mock.Mock(returncode=1, stdout=json.dumps(busy))):
            with self.assertRaises(G['LaneBusy']):
                G['remote']({'op': 'assign', 'lane': 'A1'})
        unknown = {'ok': False, 'error': 'gateway timeout'}
        with mock.patch.object(G['subprocess'], 'run', return_value=mock.Mock(returncode=1, stdout=json.dumps(unknown))):
            with self.assertRaisesRegex(RuntimeError, 'gateway rejected'):
                G['remote']({'op': 'assign', 'lane': 'A1'})

if __name__ == '__main__': unittest.main()
