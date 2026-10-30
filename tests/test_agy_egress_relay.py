import json
import os
import pathlib
import subprocess
import tempfile
import unittest


WRAPPER = pathlib.Path(__file__).resolve().parents[1] / "bin" / "agy-via-relay"


class AgyRelayTests(unittest.TestCase):
    def test_relay_scope_and_inherited_fallback(self):
        with tempfile.TemporaryDirectory() as root:
            directory = pathlib.Path(root)
            probe = directory / "curl"
            agent = directory / "agent"
            agent.write_text('#!/bin/sh\nprintf "{\\"proxy\\":\\"%s\\"}\\n" "$HTTPS_PROXY"\n')
            agent.chmod(0o755)
            env = dict(os.environ, PATH=root, OPENSWARM_AGY_REAL_BIN=str(agent), HTTPS_PROXY="http://inherited:9999")
            for available, expected in ((True, "http://127.0.0.1:18744"), (False, "http://inherited:9999")):
                probe.write_text("#!/bin/sh\nexit %d\n" % (0 if available else 1))
                probe.chmod(0o755)
                result = subprocess.run([str(WRAPPER)], env=env, capture_output=True, text=True, check=True)
                self.assertEqual(json.loads(result.stdout)["proxy"], expected)
                self.assertEqual("relay unavailable" in result.stderr, not available)


if __name__ == "__main__":
    unittest.main()
