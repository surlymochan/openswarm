"""Debug runner: run test_agy test-by-test, printing each outcome immediately."""
import io, os, sys, traceback, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def walk(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from walk(item)
        else:
            yield item

def main():
    import test_agy  # noqa: F401  (module under debug)
    suite = unittest.defaultTestLoader.loadTestsFromName("test_agy")
    for test in walk(suite):
        result = unittest.TestResult()
        try:
            test(result)
        except BaseException:
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
        print(f"RESULT {status} {test.id()}", flush=True)
        for _t, tb in result.errors + result.failures:
            print(tb, flush=True)
    print("ALL-DONE", flush=True)

if __name__ == "__main__":
    main()
