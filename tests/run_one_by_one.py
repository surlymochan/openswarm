"""Probe: isolate the Windows KeyboardInterrupt phenomenon."""
import os, subprocess, sys, traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def mark(msg):
    print(f"PROBE {msg}", flush=True)

def loop(name, fn, n=60):
    hits = 0
    for i in range(n):
        try:
            fn()
        except KeyboardInterrupt:
            hits += 1
            mark(f"{name}: KeyboardInterrupt at iter {i}")
        except BaseException as e:
            mark(f"{name}: {type(e).__name__} at iter {i}: {e}")
    mark(f"{name}: done, {hits} interrupts / {n}")

def main():
    mark("start")
    loop("plain-child", lambda: subprocess.run([sys.executable, "-c", "import time; time.sleep(0.05)"],
                                               capture_output=True, timeout=20))
    import _load
    xs = _load.load()
    mark("module loaded")
    loop("git-sh", lambda: xs.sh(["git", "--version"], timeout=20))
    import unittest
    for tid in ("test_pi.PiBackend.test_success_parses_result_and_records_usage",
                "test_pi.PiBackend.test_risk_stops_runner_even_if_executor_reports_done"):
        result = unittest.TestResult()
        test = unittest.defaultTestLoader.loadTestsFromName(tid)
        try:
            test(result)
            mark(f"{tid}: errors={len(result.errors)} failures={len(result.failures)}")
            for _t, tb in result.errors + result.failures:
                print(tb[-600:], flush=True)
        except BaseException:
            mark(f"{tid}: RAISED")
            traceback.print_exc()
    mark("end")

if __name__ == "__main__":
    main()
