"""openswarm side of the routing benchmark.

Decisions are produced in-process, the same way tests/_load.py loads bin/openswarm.
A separate loop measures CLI cold-start of `bin/openswarm route`.
"""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time

BENCH = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BENCH)
PROVIDER = {"codex": "chatgpt-subscription", "ocgo": "opencode-go", "deepseek": "deepseek"}


def load_json(path):
    with open(path) as f:
        return json.load(f)


def load_jsonl(path):
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_openswarm(home):
    os.environ["OPENSWARM_HOME"] = home
    for key in list(os.environ):
        if key.startswith(("OPENSWARM_", "XSWARM_")) and key != "OPENSWARM_HOME":
            os.environ.pop(key)
    name = "openswarm_bench"
    sys.modules.pop(name, None)
    loader = importlib.machinery.SourceFileLoader(name, os.path.join(ROOT, "bin", "openswarm"))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    mod.breakers = lambda: {}
    return mod


def blank_usage():
    return {"5h": 0.0, "7d": 0.0, "30d": 0.0}


def build_state(xs, policy, catalogue, scenario):
    """Synthetic OPENSWARM_STATE_FILE. Tiers come from the real classifiers."""
    codex_models = []
    ocgo_models = []
    legacy_kw = policy["codex"]["legacy_keywords"]
    for spec in catalogue["models"]:
        if "codex" in spec["backends"]:
            desc = spec["description"]
            codex_models.append({
                "id": spec["id"],
                "description": desc,
                "priority": spec["priority"],
                "tier": xs.classify_codex(desc, policy),
                "legacy": any(k in desc.lower() for k in legacy_kw),
                "efforts": list(spec["efforts"]),
                "default_effort": "medium",
                "context_window": spec["context_window"],
                "limit_id": "codex",
            })
        if "ocgo" in spec["backends"]:
            usage = dict(blank_usage())
            usage.update(scenario.get("ocgo_usage", {}).get(spec["id"], {}))
            ocgo_models.append({
                "id": spec["id"],
                "name": spec["id"],
                "family": spec["family"],
                "release_date": spec["release_date"],
                "cost_in": None,
                "cost_out": None,
                "context": spec["context"],
                "variants": ["low", "medium", "high", "max"],
                "reasoning": True,
                "monthly_limit_usd": spec["monthly_limit_usd"],
                "spend_usd": blank_usage(),
                "usage": usage,
                "excluded": bool(spec["excluded"]),
                "tier": spec.get("tier"),
            })
    # Price terciles match probe_ocgo: sort usable models by cost_out, then cut
    # into thirds. Equal prices keep catalogue order (stable sort).
    by_id = {m["id"]: m for m in catalogue["models"]}
    prices = load_json(os.path.join(BENCH, "prices.json"))
    for m in ocgo_models:
        band = by_id[m["id"]]["cost_band"]
        cost = prices["ocgo"]["bands"][band]
        m["cost_in"] = cost["cost_in"]
        m["cost_out"] = cost["cost_out"]
    usable = sorted(
        [m for m in ocgo_models if not m["excluded"] and m["cost_out"] is not None],
        key=lambda m: (m["cost_out"],),
    )
    n = len(usable)
    for i, m in enumerate(usable):
        m["tier"] = "fast" if i < n / 3 else ("balanced" if i < 2 * n / 3 else "deep")

    cx = scenario["codex"]
    state = {
        "generated_at": 0,
        "generated_at_local": "bench",
        "claude": {
            "plan": "pro", "org_type": "", "rate_limit_tier": "", "provider": "official",
            "metered_by_anthropic": True, "five_hour": None, "seven_day": None,
            "source": "bench", "age_minutes": None, "stale": True,
        },
        "codex": {
            "binary": "/usr/bin/codex", "version": "bench", "plan": "plus",
            "models_source": "bench", "config_model": None,
            "limits": {"codex": {"name": "codex", "windows": {
                "five_hour": {"used": cx["five_hour"], "window_minutes": 300, "resets_at": None,
                              "resets_at_local": None, "hours_to_reset": None, "pace": None},
                "seven_day": {"used": cx["seven_day"], "window_minutes": 10080, "resets_at": None,
                              "resets_at_local": None, "hours_to_reset": None, "pace": None},
            }, "observed_at": None, "age_minutes": 1}},
            "models": codex_models,
        },
        "ocgo": {
            "binary": "/usr/bin/opencode", "plan": "go", "plan_inferred": False,
            "limits_source": "bench", "spend_note": "bench", "models": ocgo_models,
        },
        "deepseek": {
            "plan": "pay-as-you-go", "balance": scenario["deepseek_balance_cny"],
            "currency": "CNY", "available": True,
            "api_models": ["deepseek-v4-pro", "deepseek-flash"],
            "opencode_models": ["deepseek-v4-pro", "deepseek-flash"],
        },
    }
    return state


def catalogue_index(catalogue):
    return {m["id"]: m for m in catalogue["models"]}


def decision(task, scenario, routed, index):
    ok = bool(routed.get("ok"))
    model = routed.get("model") if ok else None
    backend = routed.get("backend") if ok else None
    spec = index.get(model) if model else None
    tier = (routed.get("tier") if ok else None) or (spec.get("tier") if spec else None)
    effort = routed.get("effort") if ok else None
    reasons = list(routed.get("reasons") or [])
    if not ok:
        skipped = routed.get("skipped") or {}
        for name in sorted(skipped):
            reasons.append(f"skipped {name}: {skipped[name]}")
    row = {
        "task_id": task["id"],
        "scenario": scenario["id"],
        "tool": "openswarm",
        "backend": backend,
        "provider": PROVIDER.get(backend) if backend else None,
        "model": model,
        "effort": effort,
        "variant": effort,
        "tier": tier,
        "attempts": 1 if ok else 0,
        "failed_attempts": 0,
        "reasons": reasons,
    }
    return row


def route_all(xs, policy, catalogue, scenarios, trace):
    index = catalogue_index(catalogue)
    rows = []
    samples = []
    for scenario in scenarios["scenarios"]:
        state = build_state(xs, policy, catalogue, scenario)
        for task in trace:
            prof = {"type": task["type"], "difficulty": task["difficulty"]}
            if task.get("context_tokens"):
                prof["context_tokens"] = task["context_tokens"]
            t0 = time.perf_counter()
            routed = xs.route(prof, state, policy)
            samples.append((time.perf_counter() - t0) * 1000.0)
            rows.append(decision(task, scenario, routed, index))
    return rows, samples


def percentile(samples, p):
    if not samples:
        return None
    xs = sorted(samples)
    k = (len(xs) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def summarize_latency(samples, kind):
    return {
        "kind": kind,
        "unit": "ms",
        "n": len(samples),
        "median": round(percentile(samples, 0.50), 4),
        "p95": round(percentile(samples, 0.95), 4),
        "min": round(min(samples), 4),
        "max": round(max(samples), 4),
    }


def cli_cold_starts(state_path, runs):
    home = tempfile.mkdtemp(prefix="openswarm-bench-cli-")
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(("OPENSWARM_", "XSWARM_")):
            env.pop(key)
    env["OPENSWARM_HOME"] = home
    env["OPENSWARM_STATE_FILE"] = state_path
    cmd = [sys.executable, os.path.join(ROOT, "bin", "openswarm"), "route",
           "--type", "impl", "--difficulty", "3", "--context-tokens", "24000"]
    samples = []
    for _ in range(runs):
        t0 = time.perf_counter()
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
        samples.append((time.perf_counter() - t0) * 1000.0)
        if proc.returncode != 0:
            raise SystemExit(f"openswarm route failed: {proc.stderr[-400:]}")
    return {
        **summarize_latency(samples, "cli_cold_start"),
        "command": "python3 bin/openswarm route --type impl --difficulty 3 --context-tokens 24000",
        "note": "Each sample is a new Python process. OPENSWARM_STATE_FILE points at a temporary copy of the fresh-quota state. This is the product route CLI.",
    }


def write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(BENCH, "results", "openswarm.jsonl"))
    ap.add_argument("--latency-out", default=os.path.join(BENCH, "results", "latency_openswarm.json"))
    ap.add_argument("--decisions", type=int, default=1000)
    ap.add_argument("--cli-runs", type=int, default=25)
    ap.add_argument("--skip-cli", action="store_true")
    args = ap.parse_args()

    home = tempfile.mkdtemp(prefix="openswarm-bench-home-")
    xs = load_openswarm(home)
    policy = xs.jload(os.path.join(ROOT, "policy.json"), {})
    catalogue = load_json(os.path.join(BENCH, "catalogue.json"))
    scenarios = load_json(os.path.join(BENCH, "scenarios.json"))
    trace = load_jsonl(os.path.join(BENCH, "trace.jsonl"))

    rows, samples = route_all(xs, policy, catalogue, scenarios, trace)
    # Repeat the same decisions until the latency sample is large enough.
    # The recorded decisions stay a single pass.
    guard = 0
    while len(samples) < args.decisions and guard < 20:
        _, more = route_all(xs, policy, catalogue, scenarios, trace)
        samples.extend(more)
        guard += 1

    write_jsonl(args.out, rows)
    latency = {"in_process": summarize_latency(samples, "in_process")}
    if not args.skip_cli:
        state = build_state(xs, policy, catalogue, scenarios["scenarios"][0])
        fd, state_path = tempfile.mkstemp(prefix="openswarm-bench-state-", suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(state, f)
        latency["cli_cold_start"] = cli_cold_starts(state_path, args.cli_runs)
        os.remove(state_path)
    os.makedirs(os.path.dirname(args.latency_out), exist_ok=True)
    with open(args.latency_out, "w") as f:
        json.dump(latency, f, indent=2, sort_keys=True)
        f.write("\n")
    print(f"openswarm decisions {len(rows)} -> {args.out}")
    print(f"in-process n={latency['in_process']['n']} median={latency['in_process']['median']}ms "
          f"p95={latency['in_process']['p95']}ms")
    if "cli_cold_start" in latency:
        c = latency["cli_cold_start"]
        print(f"cli cold start n={c['n']} median={c['median']}ms p95={c['p95']}ms")


if __name__ == "__main__":
    main()
