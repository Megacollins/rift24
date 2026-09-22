#!/usr/bin/env bash
# Serve the static desk + landing page from the repo root so app/ can read ../data/demo_state.json and ../reports/*.
# Usage: bash scripts/serve.sh [port]     ->  http://localhost:5210/app/landing/  (desk: http://localhost:5210/app/)
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${1:-5210}"
[ -f data/demo_state.json ] || python -m scripts.build_demo_state
echo "Rift24 landing: http://localhost:${PORT}/app/landing/   desk: http://localhost:${PORT}/app/   (Ctrl+C to stop)"
exec python -m http.server "$PORT"
