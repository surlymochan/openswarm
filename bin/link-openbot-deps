#!/bin/bash
# Run in an executor worktree. Links the base checkout's installed dependencies, but keeps the workspace
# packages (@openbot/*) resolving to this worktree's own sources. Usage: link-workspace-deps.sh <base checkout>
set -euo pipefail
base=$(cd "$1" && pwd)
work=$(pwd)
[ "$base" = "$work" ] && exit 0
if [ -L node_modules ]; then unlink node_modules; fi
mkdir -p node_modules
for entry in "$base"/node_modules/* "$base"/node_modules/.[!.]*; do
  [ -e "$entry" ] || [ -L "$entry" ] || continue
  name=$(basename "$entry")
  [ "$name" = "@openbot" ] && continue
  ln -sfn "$entry" "node_modules/$name"
done
mkdir -p node_modules/@openbot
for link in "$base"/node_modules/@openbot/*; do
  ln -sfn "$(readlink "$link")" "node_modules/@openbot/$(basename "$link")"
done
for dir in "$base"/apps/*/node_modules "$base"/packages/*/node_modules "$base"/remote/*/node_modules; do
  [ -d "$dir" ] || continue
  rel=${dir#"$base"/}
  [ -d "$work/$(dirname "$rel")" ] && [ ! -e "$work/$rel" ] && ln -sfn "$dir" "$work/$rel"
done
echo "linked dependencies from $base; @openbot/* resolve to $work"
