"""Count routing coverage from the pinned sources.

OmO numbers come from importing packages/model-core at --omo-dir (category and
agent fallback chains). openswarm numbers come from policy.json.
"""
import json
import os
import subprocess
import sys

BENCH = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BENCH)


def policy_model_ids(policy):
    found = set()
    limits = (policy.get("ocgo") or {}).get("monthly_limits") or {}
    for table in limits.values():
        if not isinstance(table, dict):
            continue
        for key in table:
            if isinstance(key, str) and not key.startswith("_"):
                found.add(key)
    for model in ((policy.get("deepseek") or {}).get("tiers") or {}).values():
        if isinstance(model, str):
            found.add(model)
    return sorted(found)


def openswarm_side(policy):
    backends = list((policy.get("routing") or {}).get("default_order") or [])
    models = policy_model_ids(policy)
    return {
        "backends": len(backends),
        "backend_names": backends,
        "model_ids_in_policy": len(models),
        "model_ids": models,
    }


def omo_side(omo_dir, bun):
    script = os.path.join(BENCH, "coverage.ts")
    proc = subprocess.run(
        [bun, script, "--omo-dir", omo_dir],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"coverage.ts failed ({proc.returncode})")
    return json.loads(proc.stdout)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--omo-dir", default="")
    ap.add_argument("--out", default=os.path.join(BENCH, "results", "coverage.json"))
    ap.add_argument("--bun", default=os.environ.get("BUN", "/usr/local/bin/bun"))
    args = ap.parse_args()
    with open(os.path.join(ROOT, "policy.json")) as f:
        policy = json.load(f)
    report = {
        "openswarm": openswarm_side(policy),
        "omo": None,
        "omo_dir": "<omo-dir>" if args.omo_dir else "",
        "note": (
            "OmO providers and model ids are the union of category and agent "
            "fallback chains in packages/model-core. openswarm model ids are the "
            "OpenCode Go monthly-cap keys plus deepseek.tiers values in policy.json. "
            "Codex slugs are not listed in policy.json."
        ),
    }
    if args.omo_dir:
        if not os.path.isdir(os.path.join(args.omo_dir, "packages", "model-core")):
            raise SystemExit(f"no packages/model-core under {args.omo_dir}")
        bun = args.bun if os.path.isfile(args.bun) and os.access(args.bun, os.X_OK) else "bun"
        data = omo_side(args.omo_dir, bun)
        commit = subprocess.run(
            ["git", "-C", args.omo_dir, "rev-parse", "HEAD"],
            capture_output=True, text=True,
        )
        data["commit"] = (commit.stdout or "").strip()
        data["source"] = "<omo-dir>/packages/model-core"
        report["omo"] = data
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)
        f.write("\n")
    sw = report["openswarm"]
    print(
        f"openswarm backends={sw['backends']} policy_model_ids={sw['model_ids_in_policy']}"
    )
    if report["omo"]:
        om = report["omo"]
        print(
            f"omo categories={om['categories']} providers={om['distinct_providers']} "
            f"model_ids={om['distinct_model_ids']} commit={om['commit']}"
        )


if __name__ == "__main__":
    main()
