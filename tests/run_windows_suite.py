"""Windows CI entry point for the unit suite.

The GitHub windows-latest runners deliver spurious console Ctrl+C events
to the job's process group while the suite runs (observed a handful per
full run, landing inside subprocess communicate waits and killing the
stock unittest runner mid-suite; the same tests are clean in isolation,
and neither the suite nor the product uses SIGINT semantics). Ignoring
SIGINT in the test process makes the run immune; child processes still
get the default disposition via subprocess' restore_signals.
"""
import os
import signal
import sys
import unittest

signal.signal(signal.SIGINT, signal.SIG_IGN)

if __name__ == "__main__":
    program = unittest.main(module=None, argv=[sys.argv[0], "discover", "-s", "tests", "-v"],
                            exit=False)
    result = program.result
    verdict = (f"suite-result: failures={len(result.failures)} errors={len(result.errors)} "
               f"skipped={len(result.skipped)} successful={result.wasSuccessful()}")
    print(verdict, file=sys.stderr, flush=True)
    # The CI step gates on this verdict file, not on the process exit code:
    # on the Windows runners the step reports exit code 1 even when this
    # process exits 0 after a fully green suite (successful=True, atexit
    # reached, os._exit(0) — the code is imposed by the step's process-tree /
    # pipe plumbing, not by the suite). A missing verdict file fails the step.
    with open("suite-verdict.txt", "w") as fh:
        fh.write(verdict + "\n")
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0 if result.wasSuccessful() else 1)
