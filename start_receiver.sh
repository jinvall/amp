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

PY="${PYTHON:-python3}"
LOG_DIR="${LOG_DIR:-logs}"
mkdir -p "$LOG_DIR"

echo "Starting AMP stack (server/amp.py)..."
exec "$PY" server/amp.py --host 0.0.0.0 "$@" 2>&1 | tee -a "$LOG_DIR/amp.log"
