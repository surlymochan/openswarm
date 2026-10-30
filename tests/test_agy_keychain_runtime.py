"""Verify credential rotation is not silently lost after a failed Keychain update."""
import contextlib
import io
import json
import pathlib
import runpy
import tempfile
import unittest
from unittest import mock


MODULE = runpy.run_path(str(pathlib.Path(__file__).resolve().parents[1] / 'bin/agy-via-local-proxy'),
                        run_name='agy_runtime_test')
GLOBALS = MODULE['main'].__globals__


class AgyKeychainRuntimeTests(unittest.TestCase):
    def test_failed_rotated_token_save_is_retried_on_next_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'token'
            original = json.dumps({'token': {'refresh_token': 'old'}}).encode()
            updated = json.dumps({'token': {'refresh_token': 'new'}}).encode()
            path.write_bytes(updated)
            child = mock.Mock()
            child.wait.return_value = 0
            persist = mock.Mock(side_effect=[RuntimeError('keychain locked'), None])
            with mock.patch.dict(GLOBALS, {'token_path': path, 'keychain_read': mock.Mock(return_value=original),
                                           'ensure_home': mock.Mock(), 'keychain_write': persist}), \
                 mock.patch.object(GLOBALS['subprocess'], 'Popen', return_value=child), \
                 mock.patch.object(GLOBALS['signal'], 'signal'), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(MODULE['main'](), 1)
                self.assertEqual(MODULE['main'](), 0)
            self.assertEqual(persist.call_count, 2)
            self.assertEqual(persist.call_args.args[0], updated)


if __name__ == '__main__':
    unittest.main()
