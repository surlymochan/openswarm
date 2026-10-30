"""Hub bridge must preserve repository, acknowledgement, and recovery boundaries."""
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MOD = runpy.run_path(str(ROOT / 'bin/hub-openswarm-bridge'))
G = MOD['run_submit'].__globals__

class HubBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.repo = self.path / 'repo'; self.repo.mkdir()
        self.workspace = self.path / 'workspace'; self.workspace.mkdir()
        def git(*args): subprocess.run(['git','-C',str(self.repo),*args],check=True,capture_output=True)
        git('init','-q'); (self.repo/'a').write_text('x')
        git('add','a'); git('-c','user.name=test','-c','user.email=test@test','commit','-qm','init')
        self.card = self.path/'card'; self.card.write_text('Repository: openbot\nExecutor-Type: mechanical\nDifficulty: 1\nAdd a function.\n')
        self.config = self.path/'config.json'
        self.config.write_text(json.dumps({'workspace':str(self.workspace),'repo':str(self.repo)}))
        self.args = ['--detach','--cwd',str(self.workspace),'--task-file',str(self.card),'--title','test',
                     '--budget-minutes','4']
        self.patches = [mock.patch.dict(G, {'config_path':self.config, 'store':self.path/'store'})]
        for p in self.patches: p.start(); self.addCleanup(p.stop)

    def test_submit_and_result_keep_executor_host(self):
        def fake(req):
            if req['op']=='submit':
                self.assertEqual((req['type'],req['difficulty']),('mechanical',1))
                return {'id':'run-1','state':'queued','host':'gores1','backend':'pi','model':'MiniMax'}
            return {'id':'run-1','result':{'result':{'summary':'done'}}}
        with mock.patch.dict(G, {'gateway':fake}), contextlib.redirect_stdout(io.StringIO()) as output:
            MOD['run_submit'](self.args)
            self.assertEqual(json.loads(output.getvalue())['executor_host'],'gores1')
            output.seek(0); output.truncate()
            self.assertTrue(MOD['mapped']('run-1','info'))
            self.assertIn('Executor host: gores1',json.loads(output.getvalue())['result']['result']['summary'])

    def test_finished_remote_commit_is_fetched_for_local_review(self):
        remote = self.path / 'remote'
        subprocess.run(['git', 'clone', '-q', str(self.repo), str(remote)], check=True)
        base = subprocess.check_output(['git', '-C', str(remote), 'rev-parse', 'HEAD'], text=True).strip()
        subprocess.run(['git', '-C', str(remote), 'checkout', '-qb', 'openswarm/run-1'], check=True)
        (remote / 'a').write_text('remote result')
        subprocess.run(['git', '-C', str(remote), '-c', 'user.name=test', '-c', 'user.email=test@test',
                        'commit', '-qam', 'result'], check=True)
        commit = subprocess.check_output(['git', '-C', str(remote), 'rev-parse', 'HEAD'], text=True).strip()
        info = {'id': 'run-1', 'result': {'branch': 'openswarm/run-1', 'commit': commit,
                                           'workspace': {'repo': str(remote), 'base': base},
                                           'result': {'summary': 'done'}}}
        def gateway(req):
            if req['op'] == 'submit':
                return {'id':'run-1','state':'queued','host':'gores1','backend':'pi','model':'MiniMax'}
            if req['op'] == 'info':
                return info
            bundle = subprocess.run([sys.executable, str(ROOT / 'bin/fleet-result-bundle')],
                                    input=json.dumps(info), text=True, capture_output=True, check=True)
            return json.loads(bundle.stdout)
        with mock.patch.dict(G, {'gateway': gateway}), contextlib.redirect_stdout(io.StringIO()) as output:
            MOD['run_submit'](self.args)
            output.seek(0); output.truncate()
            self.assertTrue(MOD['mapped']('run-1', 'info'))
            result = json.loads(output.getvalue())['result']
        self.assertEqual(result['local_review_ref'], 'refs/openswarm/run-1')
        actual = subprocess.check_output(['git', '-C', str(self.repo), 'show', 'refs/openswarm/run-1:a'], text=True)
        self.assertEqual(actual, 'remote result')
        self.assertEqual((self.repo / 'a').read_text(), 'x')

    def test_result_fetch_failure_keeps_completed_result_visible(self):
        def gateway(req):
            if req['op'] == 'submit':
                return {'id':'run-1','state':'queued','host':'gores1','backend':'pi','model':'MiniMax'}
            if req['op'] == 'bundle':
                raise RuntimeError('transport unavailable')
            return {'id':'run-1', 'result':{'branch':'openswarm/run-1', 'commit':'f' * 40,
                                            'result':{'summary':'done'}}}
        with mock.patch.dict(G, {'gateway':gateway}), contextlib.redirect_stdout(io.StringIO()) as output:
            MOD['run_submit'](self.args)
            output.seek(0); output.truncate()
            self.assertTrue(MOD['mapped']('run-1', 'info'))
            result = json.loads(output.getvalue())['result']
        self.assertIn('could not be fetched', result['local_review_error'])
        self.assertIn('done', result['result']['summary'])
        self.assertIn('could not fetch', result['result']['summary'])

    def test_lost_ack_blocks_repeated_dispatch(self):
        with mock.patch.dict(G, {'gateway':mock.Mock(side_effect=RuntimeError('lost ack'))}):
            with self.assertRaisesRegex(RuntimeError,'unresolved'): MOD['run_submit'](self.args)
            with self.assertRaisesRegex(RuntimeError,'unresolved'): MOD['run_submit'](self.args)

    def test_no_lane_rejection_allows_later_dispatch(self):
        response = subprocess.CompletedProcess([], 1, json.dumps({'ok':False, 'error':'no available fleet lane'}), '')
        with mock.patch.object(subprocess, 'run', return_value=response):
            with self.assertRaisesRegex(RuntimeError, 'no available fleet lane'):
                MOD['gateway']({'op':'submit'})
        with mock.patch.dict(G, {'gateway':mock.Mock(side_effect=MOD['NoAvailableLane']('no available fleet lane'))}):
            with self.assertRaisesRegex(RuntimeError, 'no available fleet lane.*retry'):
                MOD['run_submit'](self.args)
        with mock.patch.dict(G, {'gateway':mock.Mock(return_value={'id':'run-after-busy','state':'queued',
                                                                  'host':'gocc','backend':'agy','model':'x'})}), \
             contextlib.redirect_stdout(io.StringIO()):
            MOD['run_submit'](self.args)
        db = MOD['db_open']()
        self.assertEqual(db.execute("select state from handoffs order by created").fetchall(),
                         [('rejected',), ('accepted',)])
        db.close()

    def test_exhausted_busy_lanes_allow_later_dispatch(self):
        response = subprocess.CompletedProcess([], 1, json.dumps({'ok':False, 'error':'available lanes changed or rejected the task'}), '')
        with mock.patch.object(subprocess, 'run', return_value=response):
            with self.assertRaises(MOD['NoAvailableLane']):
                MOD['gateway']({'op':'submit'})
        with mock.patch.dict(G, {'gateway':mock.Mock(side_effect=MOD['NoAvailableLane']('busy'))}):
            with self.assertRaisesRegex(RuntimeError, 'retry'):
                MOD['run_submit'](self.args)
        db = MOD['db_open']()
        self.assertEqual(db.execute('select state from handoffs').fetchone(), ('rejected',))
        db.close()

    def test_unknown_gateway_error_is_not_exposed(self):
        response = subprocess.CompletedProcess([], 1, json.dumps({'ok':False, 'error':'arbitrary remote text'}), '')
        with mock.patch.object(subprocess, 'run', return_value=response):
            with self.assertRaisesRegex(RuntimeError, '^fleet gateway rejected request$'):
                MOD['gateway']({'op':'submit'})

    def test_remote_gateway_preserves_known_dispatch_failure(self):
        req = {'op':'submit', 'prompt':'task', 'title':'test', 'base':'a'*40,
               'type':'migration', 'difficulty':1, 'budget':4, 'verify':None}
        result = subprocess.CompletedProcess([], 1, json.dumps({'ok':False, 'error':'no available fleet lane'}), '')
        with mock.patch.object(sys, 'stdin', io.StringIO(json.dumps(req))), \
             mock.patch.object(subprocess, 'run', return_value=result), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(SystemExit):
                runpy.run_path(str(ROOT / 'bin/hub-fleet-gateway'))
        self.assertEqual(json.loads(output.getvalue())['error'], 'no available fleet lane')

    def test_no_lane_through_gateway_then_retry(self):
        fake_home = self.path / 'gateway-home'
        fake_cli = fake_home / '.local/bin/openswarm'
        fake_cli.parent.mkdir(parents=True)
        marker = self.path / 'called'
        fake_cli.write_text('#!' + sys.executable + '\n' +
            'import json,pathlib,sys\np=pathlib.Path(' + repr(str(marker)) + ')\n' +
            'if not p.exists():\n p.touch(); print(json.dumps({"ok":False,"error":"no available fleet lane"})); sys.exit(1)\n' +
            'print(json.dumps({"ok":True,"id":"run-after-busy","state":"queued","host":"gocc","backend":"agy","model":"x"}))\n')
        fake_cli.chmod(0o755)
        tunnel = self.path / 'gateway-tunnel.py'
        tunnel.write_text('import os,sys\nos.environ["HOME"]=' + repr(str(fake_home)) + '\n' +
                          'os.execv(sys.executable,[sys.executable,' + repr(str(ROOT / 'bin/hub-fleet-gateway')) + '])\n')
        with mock.patch.dict(G, {'transport':[sys.executable, str(tunnel)]}):
            with self.assertRaisesRegex(RuntimeError, 'no available fleet lane.*retry'):
                MOD['run_submit'](self.args)
            with contextlib.redirect_stdout(io.StringIO()) as output:
                MOD['run_submit'](self.args)
            self.assertEqual(json.loads(output.getvalue())['id'], 'run-after-busy')
        db = MOD['db_open']()
        self.assertEqual(db.execute('select state from handoffs order by created').fetchall(),
                         [('rejected',), ('accepted',)])
        db.close()

    def test_review_profile_reaches_gateway(self):
        self.card.write_text('Repository: openbot\nExecutor-Type: review\nDifficulty: 2\nReview the code.\n')
        gateway = mock.Mock(return_value={'id':'run-review','state':'queued','host':'gocc','backend':'copilot','model':'x'})
        with mock.patch.dict(G, {'gateway':gateway}), contextlib.redirect_stdout(io.StringIO()):
            MOD['run_submit'](self.args)
        self.assertEqual(gateway.call_args.args[0]['type'], 'review')

    def test_unknown_workspace_and_dirty_repo_fail_before_dispatch(self):
        with mock.patch.dict(G, {'gateway':mock.Mock()}) as _:
            bad=list(self.args); bad[bad.index('--cwd')+1]=str(self.path)
            with self.assertRaisesRegex(ValueError,'mapping'): MOD['run_submit'](bad)
            (self.repo/'a').write_text('modified')
            with self.assertRaisesRegex(ValueError,'clean checkout'): MOD['run_submit'](self.args)
            G['gateway'].assert_not_called()

    def test_other_project_is_not_silently_mapped(self):
        self.card.write_text('Repository: other-project\nDo the work.\n')
        with mock.patch.dict(G, {'gateway':mock.Mock()}):
            with self.assertRaisesRegex(ValueError,'Repository: openbot'):
                MOD['run_submit'](self.args)
            G['gateway'].assert_not_called()

    def test_oversized_card_does_not_create_a_pending_handoff(self):
        self.card.write_text('Repository: openbot\n' + 'x' * 50000)
        with mock.patch.dict(G, {'gateway':mock.Mock()}):
            with self.assertRaisesRegex(ValueError, 'invalid task card'):
                MOD['run_submit'](self.args)
            G['gateway'].assert_not_called()
        db = MOD['db_open']()
        self.assertEqual(db.execute('select count(*) from handoffs').fetchone()[0], 0)
        db.close()

    def test_oversized_title_and_verify_do_not_create_a_pending_handoff(self):
        for args in (self.args + ['--title', 'x' * 161], self.args + ['--verify', 'x' * 4097]):
            with self.assertRaisesRegex(ValueError, 'invalid task title or verify'):
                MOD['run_submit'](args)
        db = MOD['db_open']()
        self.assertEqual(db.execute('select count(*) from handoffs').fetchone()[0], 0)
        db.close()

    def test_pending_reconciliation_requires_verified_terminal_run(self):
        with mock.patch.dict(G, {'gateway':mock.Mock(side_effect=RuntimeError('lost ack'))}):
            with self.assertRaisesRegex(RuntimeError,'unresolved'): MOD['run_submit'](self.args)
        db = MOD['db_open']()
        request_id = db.execute("select request_id from handoffs where state='pending'").fetchone()[0]
        db.close()
        args = ['bridge', 'reconcile', '--request-id', request_id, '--host', 'gores1', '--run-id', 'run-1',
                '--confirm', 'matching-terminal-run-verified']
        with mock.patch.object(sys, 'argv', ['bridge', *args]), \
             mock.patch.dict(G, {'gateway':mock.Mock(return_value={'id':'run-1','result':None})}):
            with self.assertRaisesRegex(ValueError,'not terminal'): MOD['main']()
        with mock.patch.object(sys, 'argv', ['bridge', *args]), \
             mock.patch.dict(G, {'gateway':mock.Mock(return_value={'id':'run-1','result':{'status':'done'}})}), \
             contextlib.redirect_stdout(io.StringIO()):
            MOD['main']()
        db = MOD['db_open']()
        self.assertEqual(db.execute('select state,run_id from handoffs').fetchone(),('accepted','run-1'))
        db.close()

if __name__ == '__main__': unittest.main()
