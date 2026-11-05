"""policy.json ocgo.monthly_limits vs the official OpenCode Go pricing tables.

The limits table is hand-maintained from https://opencode.ai/docs/go/; this
test pins it to a dated snapshot of that page (tests/fixtures/
ocgo_go_docs_snapshot.json) so a policy edit that invents a model or a
limit — or a docs refresh that forgets a tabled model — fails loudly
instead of silently mispricing quota headroom in `openswarm status`.

Run: python3 -m unittest discover -s tests   (or: python3 tests/test_ocgo_policy_tables.py)
"""
import datetime as dt
import json
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURE = os.path.join(HERE, "fixtures", "ocgo_go_docs_snapshot.json")


def load_policy():
    with open(os.path.join(ROOT, "policy.json"), encoding="utf-8") as f:
        return json.load(f)


def load_fixture():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


class OcgoPolicyTables(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = load_policy()["ocgo"]
        cls.fixture = load_fixture()
        cls.snap = {m["id"]: m for m in cls.fixture["models"]}

    def test_fixture_carries_provenance(self):
        self.assertEqual(self.fixture["source"], "https://opencode.ai/docs/go/")
        retrieved = dt.date.fromisoformat(self.fixture["retrieved"])
        self.assertGreaterEqual(retrieved, dt.date(2026, 11, 6))
        self.assertGreaterEqual(len(self.snap), 25)

    def test_every_policy_entry_exists_in_snapshot_with_same_limit(self):
        for plan in ("go", "plus"):
            table = self.policy["monthly_limits"][plan]
            for mid, limit in table.items():
                if mid == "_default":
                    continue
                self.assertIn(mid, self.snap, f"{plan}: {mid} not in official snapshot")
                self.assertEqual(
                    self.snap[mid][plan], limit,
                    f"{plan}: {mid} policy={limit} official={self.snap[mid][plan]}",
                )

    def test_policy_tables_cover_every_tabled_snapshot_model(self):
        expected = {
            mid for mid, m in self.snap.items()
            if isinstance(m["go"], int) and "policy_table_exempt" not in m
        }
        for plan in ("go", "plus"):
            tabled = set(self.policy["monthly_limits"][plan]) - {"_default"}
            self.assertEqual(tabled, expected, f"{plan}: tabled set drifted from snapshot")

    def test_default_fallback_present(self):
        for plan in ("go", "plus"):
            self.assertIn("_default", self.policy["monthly_limits"][plan])

    def test_policy_source_names_the_snapshot_source(self):
        self.assertIn(self.fixture["source"], self.policy["monthly_limits_source"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
