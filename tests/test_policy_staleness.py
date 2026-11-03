"""policy.json price/quota table staleness check (openswarm status warning).

Run: python3 -m unittest discover -s tests   (or: python3 tests/test_policy_staleness.py)
"""
import datetime as dt
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _load  # noqa: E402

xs = _load.load()


def policy(updated=None, limit=None):
    meta = {}
    if updated is not None:
        meta["tables_last_updated"] = updated
    if limit is not None:
        meta["stale_after_days"] = limit
    return {"_meta": meta}


class PolicyStaleness(unittest.TestCase):
    def test_fresh_tables_are_not_stale(self):
        r = xs.policy_staleness(policy("2026-10-06"), today=dt.date(2026, 10, 20))
        self.assertEqual(r["age_days"], 14)
        self.assertFalse(r["stale"])

    def test_old_tables_are_stale(self):
        r = xs.policy_staleness(policy("2026-10-06"), today=dt.date(2026, 11, 20))
        self.assertEqual(r["age_days"], 45)
        self.assertTrue(r["stale"])

    def test_limit_is_configurable(self):
        r = xs.policy_staleness(policy("2026-10-06", limit=60), today=dt.date(2026, 11, 20))
        self.assertFalse(r["stale"])

    def test_missing_meta_returns_none(self):
        self.assertIsNone(xs.policy_staleness({}))
        self.assertIsNone(xs.policy_staleness({"_meta": {}}))

    def test_unparseable_date_counts_as_stale(self):
        r = xs.policy_staleness(policy("not-a-date"), today=dt.date(2026, 11, 20))
        self.assertTrue(r["stale"])
        self.assertIsNone(r["age_days"])

    def test_shipped_policy_carries_the_date(self):
        shipped = xs.jload(os.path.join(xs.SKILL_DIR, "policy.json"), {})
        r = xs.policy_staleness(shipped)
        self.assertIsNotNone(r)
        self.assertEqual(r["last_updated"], "2026-10-06")


if __name__ == "__main__":
    unittest.main(verbosity=2)
