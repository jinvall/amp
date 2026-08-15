#!/usr/bin/env bash
# Single entry point launcher for the AMP Silver stack (receiver + web UI).
# This just execs server/amp.py in one process (no background jobs, no orphans).
# Ports auto-allocate in 8090..8099 unless overridden.
#
# For an always-on service, prefer the systemd unit:
#   cp server/amp-receiver.service /etc/systemd/system/
#   systemctl daemon-reload && systemctl enable --now amp-receiver.service
#
# Usage:
#   ./start_receiver.sh [extra args passed to server/amp.py]
set -u

cd "$(dirname "$0")"

# Always use the project venv Python; do not fall back to system python3.
PY="$(cd server && pwd)/venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "ERROR: venv Python not found at $PY" >&2
  exit 1
fi

LOG_DIR="${LOG_DIR:-logs}"
mkdir -p "$LOG_DIR"

# ── Pre-flight: stop any existing receiver so ports are clean ──
existing_pids=$(pgrep -f "amp\.py" 2>/dev/null || true)
if [ -n "$existing_pids" ]; then
  echo "Stopping existing receiver (pid: $existing_pids)..."
  kill -TERM $existing_pids 2>/dev/null || true
  for i in $(seq 1 50); do
    if ! ss -tlnp 2>/dev/null | grep -qE '(:809[0-9]|:8093)'; then
      break
    fi
    sleep 0.1
  done
fi

echo "Starting AMP stack (server/amp.py)..."
exec "$PY" server/amp.py --host 0.0.0.0 "$@" 2>&1 | tee -a "$LOG_DIR/amp.log"
