#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export UV_CACHE_DIR="$PROJECT_ROOT/.cache/uv"
export UV_PYTHON_INSTALL_DIR="$PROJECT_ROOT/.python"
export npm_config_cache="$PROJECT_ROOT/.cache/npm"
export NEXT_TELEMETRY_DISABLED=1
export WATCHPACK_POLLING=true
for port in 8000 3000; do
  if lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port $port is already in use. Stop the existing service before starting."
    exit 1
  fi
done
if [ ! -x "$PROJECT_ROOT/apps/api/.venv/bin/python" ]; then
  (cd "$PROJECT_ROOT/apps/api" && uv sync --python 3.12 --locked)
fi
if [ ! -d "$PROJECT_ROOT/apps/web/node_modules" ]; then
  (cd "$PROJECT_ROOT/apps/web" && npm ci --no-fund)
fi
cd "$PROJECT_ROOT/apps/api"
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 &
API_PID=$!
cd "$PROJECT_ROOT/apps/web"
npm run dev -- --port 3000 &
WEB_PID=$!
cleanup() { kill "$API_PID" "$WEB_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM
echo "Research Workbench: http://localhost:3000"
echo "API documentation: http://localhost:8000/docs"
wait "$API_PID" "$WEB_PID"
