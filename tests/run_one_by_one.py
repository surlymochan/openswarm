"""Debug runner: run the whole suite test-by-test, printing each outcome immediately."""
import os, sys, traceback, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def walk(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from walk(item)
        else:
            yield item

def main():
    only = sys.argv[1] if len(sys.argv) > 1 else "test*.py"
    suite = unittest.defaultTestLoader.discover(os.path.dirname(os.path.abspath(__file__)), pattern=only)
    counts = {"ok": 0, "ERROR": 0, "FAIL": 0, "SKIP": 0, "RAISED-OUTER": 0}
    for test in walk(suite):
        result = unittest.TestResult()
        try:
            test(result)
        except BaseException:
            counts["RAISED-OUTER"] += 1
            print(f"RAISED-OUTER {test.id()}", flush=True)
            traceback.print_exc()
            continue
        if result.errors:
            status = "ERROR"
        elif result.failures:
            status = "FAIL"
        elif result.skipped:
            status = "SKIP"
        else:
            status = "ok"
        counts[status] += 1
        print(f"RESULT {status} {test.id()}", flush=True)
        for _t, tb in result.errors + result.failures:
            print(tb, flush=True)
    print(f"ALL-DONE {counts}", flush=True)

if __name__ == "__main__":
    main()
