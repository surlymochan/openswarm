#!/usr/bin/env bash
# One-command routing benchmark. Decision files are deterministic; latency is not.
set -euo pipefail

BENCH="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$BENCH/.." && pwd)"
RES="$BENCH/results"
OMO_DIR=""
BUN="${BUN:-/usr/local/bin/bun}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --omo-dir)
      OMO_DIR="${2:-}"
      shift 2
      ;;
    *)
      echo "unknown argument: $1" >&2
      echo "usage: bench/run.sh [--omo-dir /path/to/oh-my-openagent]" >&2
      exit 2
      ;;
  esac
done

mkdir -p "$RES"
rm -f "$RES/omo_skipped.txt" "$RES/omo.jsonl" "$RES/latency_omo.json"

PYTHON="$(command -v python3)"
if [[ ! -x "$BUN" ]]; then
  if command -v bun >/dev/null 2>&1; then
    BUN="$(command -v bun)"
  else
    BUN=""
  fi
fi

OS_CMD="$PYTHON $BENCH/route_openswarm.py --out $RES/openswarm.jsonl --latency-out $RES/latency_openswarm.json --decisions 1000 --cli-runs 25"
echo "running openswarm harness"
# shellcheck disable=SC2086
$OS_CMD

RAN_OMO=0
if [[ -n "$OMO_DIR" && -d "$OMO_DIR/packages/model-core" && -n "$BUN" && -x "$BUN" ]]; then
  echo "running OmO harness"
  "$BUN" "$BENCH/route_omo.ts" --omo-dir "$OMO_DIR" --out "$RES/omo.jsonl" --latency-out "$RES/latency_omo.json" --decisions 1000
  RAN_OMO=1
else
  if [[ -z "$OMO_DIR" ]]; then
    msg="OmO side skipped: --omo-dir was not passed."
  elif [[ ! -d "$OMO_DIR/packages/model-core" ]]; then
    msg="OmO side skipped: <omo-dir> has no packages/model-core."
  else
    msg="OmO side skipped: bun is not installed."
  fi
  echo "$msg"
  echo "$msg" > "$RES/omo_skipped.txt"
fi

echo "counting coverage"
if [[ "$RAN_OMO" -eq 1 ]]; then
  "$PYTHON" "$BENCH/coverage.py" --omo-dir "$OMO_DIR" --out "$RES/coverage.json" --bun "$BUN"
else
  "$PYTHON" "$BENCH/coverage.py" --out "$RES/coverage.json"
fi

REL_OS="python3 bench/route_openswarm.py --out bench/results/openswarm.jsonl --latency-out bench/results/latency_openswarm.json --decisions 1000 --cli-runs 25"
REL_COV="python3 bench/coverage.py --out bench/results/coverage.json"
REL_OMO="OmO skipped"
REL_SCORE="python3 bench/score.py --openswarm bench/results/openswarm.jsonl --out-dir bench/results --env bench/results/environment.json --latency-openswarm bench/results/latency_openswarm.json --latency-omo bench/results/latency_omo.json --coverage bench/results/coverage.json"
if [[ "$RAN_OMO" -eq 1 ]]; then
  REL_OMO="bun bench/route_omo.ts --omo-dir <omo-dir> --out bench/results/omo.jsonl --latency-out bench/results/latency_omo.json --decisions 1000"
  REL_COV="python3 bench/coverage.py --omo-dir <omo-dir> --out bench/results/coverage.json"
  REL_SCORE="$REL_SCORE --omo bench/results/omo.jsonl"
fi

"$PYTHON" - "$RES/environment.json" "$ROOT" "$OMO_DIR" "$BUN" "$RAN_OMO" "$REL_OS" "$REL_OMO" "$REL_COV" "$REL_SCORE" <<'PY'
import json, os, platform, subprocess, sys
out, root, omo, bun, ran_omo, rel_os, rel_omo, rel_cov, rel_score = sys.argv[1:]
def sh(cmd):
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""
cpu = ""
try:
    for line in open("/proc/cpuinfo"):
        if line.lower().startswith("model name"):
            cpu = line.split(":", 1)[1].strip()
            break
except OSError:
    cpu = platform.processor()
py = sh([sys.executable, "--version"])
env = {
    "date": sh(["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"]),
    "os": platform.platform(),
    "kernel": platform.release(),
    "cpu": cpu or platform.processor(),
    "cores": os.cpu_count(),
    "python": py.replace("Python ", "") if py.startswith("Python ") else py,
    "bun": sh([bun, "--version"]) if bun else "",
    "openswarm_commit": sh(["git", "-C", root, "rev-parse", "HEAD"]),
    "omo_dir": "<omo-dir>" if ran_omo == "1" and omo else "",
    "omo_commit": sh(["git", "-C", omo, "rev-parse", "HEAD"]) if omo else "",
    "commands": [rel_os, rel_omo, rel_cov, rel_score],
}
with open(out, "w") as f:
    json.dump(env, f, indent=2, sort_keys=True)
    f.write("\n")
PY

echo "scoring"
SCORE_CMD=("$PYTHON" "$BENCH/score.py"
  --openswarm "$RES/openswarm.jsonl"
  --out-dir "$RES"
  --env "$RES/environment.json"
  --latency-openswarm "$RES/latency_openswarm.json"
  --latency-omo "$RES/latency_omo.json"
  --coverage "$RES/coverage.json")
if [[ "$RAN_OMO" -eq 1 ]]; then
  SCORE_CMD+=(--omo "$RES/omo.jsonl")
fi
"${SCORE_CMD[@]}"

if command -v google-chrome >/dev/null 2>&1; then
  PREVIEW="$RES/_chart_preview.html"
  cat > "$PREVIEW" <<EOF
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><style>html,body{margin:0;padding:0;background:#fff}</style></head>
<body><img src="chart.svg" width="1100" height="760" alt="chart"></body>
</html>
EOF
  google-chrome --headless --disable-gpu --no-sandbox --hide-scrollbars \
    --default-background-color=FFFFFFFF \
    --window-size=1100,760 \
    --screenshot="$RES/chart.png" \
    "file://$PREVIEW" >/dev/null 2>&1 || true
  rm -f "$PREVIEW"
fi
echo "results in bench/results"
