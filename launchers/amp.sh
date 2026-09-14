#!/usr/bin/env bash
# AMP Silver — single launcher for the entire stack
# Starts receiver, waits for web UI, opens browser
set -euo pipefail
AMP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
WEB_PORT="${WEB_PORT:-8093}"
URL="http://localhost:${WEB_PORT}"
"${AMP_DIR}/start_receiver.sh" >/dev/null 2>&1 &
disown
for i in $(seq 1 40); do
  curl -sf "${URL}" >/dev/null 2>&1 && break
  sleep 0.25
done
xdg-open "${URL}" >/dev/null 2>&1 || true
