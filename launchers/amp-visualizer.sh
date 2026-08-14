#!/usr/bin/env bash
# AMP Visualizer desktop launcher
# Ensures the Silver receiver is running, then opens the live visualizer UI.

set -euo pipefail

AMP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVER_DIR="${AMP_DIR}/server"
VENV_PYTHON="${SERVER_DIR}/venv/bin/python"
WEB_PORT="8093"
URL="http://localhost:${WEB_PORT}"

is_running() {
  pgrep -f "amp.py" >/dev/null 2>&1
}

if ! is_running; then
  echo "[launcher] Starting AMP receiver with venv..."
  cd "${SERVER_DIR}"
  nohup "${VENV_PYTHON}" amp.py --host 0.0.0.0 --pcm 8090 --control 8091 --viz 8092 --web-port "${WEB_PORT}" --output-dir "${AMP_DIR}/audio_segments" >> "${AMP_DIR}/logs/amp.log" 2>&1 &
  # Give it a moment to bind ports
  for i in $(seq 1 20); do
    if curl -sf "${URL}" >/dev/null 2>&1; then
      break
    fi
    sleep 0.5
  done
fi

echo "[launcher] Opening ${URL}"
xdg-open "${URL}" >/dev/null 2>&1 || true
