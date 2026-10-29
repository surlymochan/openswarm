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

    def test_lost_ack_blocks_repeated_dispatch(self):
        with mock.patch.dict(G, {'gateway':mock.Mock(side_effect=RuntimeError('lost ack'))}):
            with self.assertRaisesRegex(RuntimeError,'unresolved'): MOD['run_submit'](self.args)
            with self.assertRaisesRegex(RuntimeError,'unresolved'): MOD['run_submit'](self.args)

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
