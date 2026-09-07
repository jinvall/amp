#!/usr/bin/env bash
# Desktop launcher for AMP Visualizer.
# Delegates to the canonical single entry point, then opens the web UI.
set -euo pipefail

AMP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
WEB_PORT="${WEB_PORT:-8093}"
URL="http://localhost:${WEB_PORT}"

# Start the receiver in the background via the single entry point.
# It handles cleanup, venv selection, ports, and logging.
"${AMP_DIR}/start_receiver.sh" >/dev/null 2>&1 &
disown

# Wait for web UI to become available
for i in $(seq 1 40); do
  if curl -sf "${URL}" >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done

echo "[launcher] Opening ${URL}"
xdg-open "${URL}" >/dev/null 2>&1 || true
