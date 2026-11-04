"""Windows CI entry point for the unit suite.

The GitHub windows-latest runners deliver spurious console Ctrl+C events
to the job's process group while the suite runs (observed a handful per
full run, landing inside subprocess communicate waits and killing the
stock unittest runner mid-suite; the same tests are clean in isolation,
and neither the suite nor the product uses SIGINT semantics). Ignoring
SIGINT in the test process makes the run immune; child processes still
get the default disposition via subprocess' restore_signals.
"""
import atexit
import signal
import sys
import unittest

signal.signal(signal.SIGINT, signal.SIG_IGN)


def _shutdown_marker():
    print("suite-runner: atexit reached", file=sys.stderr, flush=True)


atexit.register(_shutdown_marker)

if __name__ == "__main__":
    program = unittest.main(module=None, argv=[sys.argv[0], "discover", "-s", "tests", "-v"],
                            exit=False)
    result = program.result
    print(f"suite-result: failures={len(result.failures)} errors={len(result.errors)} "
          f"skipped={len(result.skipped)} successful={result.wasSuccessful()}",
          file=sys.stderr, flush=True)
    sys.exit(0 if result.wasSuccessful() else 1)
