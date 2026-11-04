"""Windows CI entry point for the unit suite.

The GitHub windows-latest runners deliver spurious console Ctrl+C events
to the job's process group while the suite runs (observed a handful per
full run, landing inside subprocess communicate waits and killing the
stock unittest runner mid-suite; the same tests are clean in isolation,
and neither the suite nor the product uses SIGINT semantics). Ignoring
SIGINT in the test process makes the run immune; child processes still
get the default disposition via subprocess' restore_signals.
"""
import signal
import sys
import unittest

signal.signal(signal.SIGINT, signal.SIG_IGN)

if __name__ == "__main__":
    program = unittest.main(module=None, argv=[sys.argv[0], "discover", "-s", "tests", "-v"],
                            exit=False)
    sys.exit(0 if program.result.wasSuccessful() else 1)
