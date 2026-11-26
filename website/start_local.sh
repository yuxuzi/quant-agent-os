#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "Starting Quant-Agent-OS local MCP server (8000) + Demo Bridge (8010)..."
uv run python website/demo_bridge.py &
BRIDGE_PID=$!

cleanup() {
  echo ""
  echo "Stopping local servers..."
  kill "$BRIDGE_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

sleep 2
echo "Starting Quant-Agent-OS frontend (http://127.0.0.1:5173)..."
npm run dev --prefix website
