"""Routing scenarios against a synthetic state (tests/fixtures/state.json).

No Codex/OpenCode install, network access or local quota files are needed.
Run: python3 -m unittest discover -s tests   (or: python3 tests/test_route.py)
"""
import copy, json, os, sys, time, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _load  # noqa: E402

xs = _load.load()
xs.breakers = lambda: {}  # isolate from any breakers on disk
xs.load_policy = lambda: xs.jload(f"{xs.SKILL_DIR}/policy.json", {})  # ignore policy.local.json overrides

POLICY = xs.load_policy()
OPT_IN_POLICY = copy.deepcopy(POLICY)
OPT_IN_POLICY["routing"]["default_order"] = ["codex", "ocgo", "deepseek"]
with open(_load.FIXTURE_STATE) as f:
    BASE = json.load(f)
NOW = time.time()
CODEX_MODELS = {m["id"]: m for m in BASE["codex"]["models"]}
DEEP = {m["id"] for m in BASE["codex"]["models"] if m["tier"] == "deep"}


def win(used, hours_left, minutes):
    return xs.window_stats(used * 100, minutes, NOW + hours_left * 3600)


def scenario(codex_week=0.3, codex_hleft=100, claude=(0.2, 0.3), claude_fresh=True, ds_balance=55.0, ocgo_usage=0.0):
    st = copy.deepcopy(BASE)
    st["codex"]["limits"] = {"codex": {"windows": {"seven_day": win(codex_week, codex_hleft, 10080)}}}
    st["claude"].update({"provider": "official", "metered_by_anthropic": True, "stale": not claude_fresh,
                         "five_hour": win(claude[0], 3, 300), "seven_day": win(claude[1], 100, 10080)})
    st["deepseek"]["balance"] = ds_balance
    for m in st["ocgo"]["models"]:
        m["usage"] = {"5h": ocgo_usage, "7d": ocgo_usage, "30d": ocgo_usage}
    return st


def prof(type_="impl", d=3, **kw):
    return {"type": type_, "difficulty": d, **kw}


class RoutingScenarios(unittest.TestCase):
    def check(self, p, st, expect, policy=POLICY):
        r = xs.route(p, st, policy)
        for k, v in expect.items():
            ok = v(r.get(k)) if callable(v) else r.get(k) == v
            self.assertTrue(ok, f"{k}={r.get(k)!r}; route={json.dumps({x: r.get(x) for x in ('ok', 'backend', 'model', 'effort', 'skipped', 'reasons')})}")
        return r

    def test_01_normal_impl_goes_to_codex_balanced(self):
        self.check(prof(d=3), scenario(), {"backend": "codex", "effort": "medium",
                   "model": lambda m: CODEX_MODELS[m]["tier"] == "balanced" and not CODEX_MODELS[m]["legacy"]})

    def test_02_hardest_goes_to_codex_deep(self):
        self.check(prof("debug", 5), scenario(), {"backend": "codex", "model": lambda m: m in DEEP})

    def test_03_mechanical_low_difficulty_uses_codex_by_default(self):
        self.check(prof("mechanical", 1), scenario(), {"backend": "codex"})

    def test_04_review_uses_codex_when_non_gpt_backends_are_disabled(self):
        self.check(prof("review", 3), scenario(), {"backend": "codex"})

    def test_05_codex_soft_limit_spills_easy_work(self):
        self.check(prof(d=3), scenario(codex_week=0.90), {"backend": "ocgo"}, OPT_IN_POLICY)

    def test_06_codex_soft_limit_keeps_hard_work_with_lower_effort(self):
        self.check(prof("debug", 5), scenario(codex_week=0.90),
                   {"backend": "codex", "effort": "medium"}, OPT_IN_POLICY)

    def test_07_codex_hard_limit_and_ocgo_exhausted_fall_to_deepseek(self):
        self.check(prof(d=3), scenario(codex_week=0.97, ocgo_usage=0.9), {"backend": "deepseek"}, OPT_IN_POLICY)

    def test_08_everything_exhausted_is_unroutable(self):
        self.check(prof(d=3), scenario(codex_week=0.97, ocgo_usage=0.9, ds_balance=3),
                   {"ok": False}, OPT_IN_POLICY)

    def test_09_expiring_unused_quota_raises_effort(self):
        self.check(prof(d=3), scenario(codex_week=0.4, codex_hleft=10), {"backend": "codex", "effort": "high"})

    def test_10_fast_burn_lowers_effort(self):
        self.check(prof(d=4), scenario(codex_week=0.7, codex_hleft=84), {"backend": "codex", "effort": "medium"})

    def test_11_tight_claude_delegates_review(self):
        self.check(prof(d=3), scenario(claude=(0.85, 0.5)), {"claude_review": "delegate"})

    def test_12_stale_claude_uses_light_review(self):
        self.check(prof(d=3), scenario(claude_fresh=False), {"claude_review": "light"})

    def test_13_huge_context_prefers_1m_backend(self):
        self.check(prof(d=3, context_tokens=600000), scenario(), {"backend": "ocgo"}, OPT_IN_POLICY)

    def test_14_forced_backend_and_model_are_honoured(self):
        self.check(prof(d=2, backend="deepseek", model="deepseek-flash"), scenario(),
                   {"backend": "deepseek", "model": "deepseek-flash"})

    def test_15_never_routes_ultra_effort(self):
        self.check(prof("debug", 5, backend="codex"), scenario(codex_week=0.1, codex_hleft=5),
                   {"ok": True, "effort": lambda e: e != "ultra"})

    def test_16_missing_binaries_are_skipped_not_crashed(self):
        st = scenario()
        st["codex"]["binary"] = None
        st["ocgo"]["binary"] = None
        self.check(prof(d=3), st, {"backend": "deepseek",
                                   "skipped": lambda s: set(s) == {"codex", "ocgo"}}, OPT_IN_POLICY)

    def test_17_default_order_excludes_opt_in_backends(self):
        self.assertEqual(POLICY["routing"]["default_order"], ["codex"])
        self.check(prof("mechanical", 1), scenario(), {"backend": "codex"})
        self.check(prof("review", 3), scenario(), {"backend": "codex"})

    def test_18_opt_in_backends_remain_forceable(self):
        for backend in ("ocgo", "deepseek"):
            with self.subTest(backend=backend):
                self.check(prof(d=3, backend=backend), scenario(), {"backend": backend})

    def test_19_configured_opt_in_backends_join_automatic_order(self):
        policy = copy.deepcopy(POLICY)
        policy["routing"]["default_order"] = ["ocgo", "codex"]
        self.check(prof("mechanical", 1), scenario(), {"backend": "ocgo"}, policy)


if __name__ == "__main__":
    unittest.main(verbosity=2)
