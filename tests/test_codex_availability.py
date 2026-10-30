"""The debug model catalogue can contain models unavailable to the signed-in account."""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _load

xs = _load.load()


class CodexAvailabilityTests(unittest.TestCase):
    def test_account_model_list_filters_catalogue(self):
        models = [{"slug": name, "visibility": "list", "description": "workhorse",
                   "supported_reasoning_levels": [{"effort": "medium"}]}
                  for name in ("available", "not-available")]
        with mock.patch.object(xs, "sh", return_value=(0, json.dumps({"models": models}), "")), \
             mock.patch.object(xs, "codex_visible_models", return_value={"available"}):
            result, source = xs.codex_models("/fake/codex", xs.load_policy())
        self.assertEqual([row["id"] for row in result], ["available"])
        self.assertIn("account model/list", source)


if __name__ == "__main__":
    unittest.main()
