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
    def test_required_default_never_silently_falls_back(self):
        with open(_load.FIXTURE_STATE) as stream:
            state = json.load(stream)
        state['codex']['models'] = [model for model in state['codex']['models'] if model['id'] != 'gpt-6-luna']
        decision, reason = xs.plan_codex({'difficulty': 3}, state, xs.load_policy(), True, [])
        self.assertIsNone(decision)
        self.assertIn('gpt-6-luna', reason)

    def test_required_model_uses_high_effort_when_available(self):
        with open(_load.FIXTURE_STATE) as stream:
            state = json.load(stream)
        model = dict(state['codex']['models'][0], id='gpt-6-luna', efforts=['low', 'medium', 'high'])
        state['codex']['models'].append(model)
        state['codex']['limits'] = {}
        decision, _ = xs.plan_codex({'difficulty': 3}, state, xs.load_policy(), True, [])
        self.assertEqual((decision['model'], decision['effort']), ('gpt-6-luna', 'high'))

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
