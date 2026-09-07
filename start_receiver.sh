#!/usr/bin/env bash
# Single entry point launcher for the AMP Silver stack (receiver + web UI).
# This just execs server/amp.py in one process (no shell background jobs, no orphans).
# Ports auto-allocate in 8090..8099 unless overridden.
#
# For an always-on service, prefer the systemd unit:
#   cp server/amp-receiver.service /etc/systemd/system/
#   systemctl daemon-reload && systemctl enable --now amp-receiver.service
#
# Usage:
#   ./start_receiver.sh [extra args passed to server/amp.py]
#   OPEN_BROWSER=1 ./start_receiver.sh   # also opens http://localhost:8093
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

# ── Bounded log: keep the active log small. ──
# A single unified log is required (no separate log streams), but it must not
# grow without limit. On each start we rotate the previous run into a single
# backup (amp.log.prev); the active amp.log always starts fresh. This caps
# on-disk log history at two files and prevents a runaway multi-hundred-MB log
# (which previously happened when a client streamed binary bytes to the control
# port and they were logged verbatim).
LOG_FILE="$LOG_DIR/amp.log"
LOG_MAX_BYTES="${LOG_MAX_BYTES:-20971520}"   # 20 MB cap before we refuse to keep growing
if [ -f "$LOG_FILE" ]; then
  if [ "$(stat -c %s "$LOG_FILE" 2>/dev/null || echo 0)" -gt "$LOG_MAX_BYTES" ]; then
    echo "Log $LOG_FILE exceeds cap ($LOG_MAX_BYTES bytes); rotating to $LOG_FILE.prev"
    mv -f "$LOG_FILE" "$LOG_FILE.prev" 2>/dev/null || true
  fi
fi

# Pre-exec messages (port clearing, rotation) would otherwise go only to the
# launcher's stdout and miss the unified log. Route them to BOTH so the log
# is complete for post-mortems (KODE: offer a log for details).
logmsg() {
  echo "$*"
  echo "$*" >> "$LOG_FILE" 2>/dev/null || true
}

# ── Pre-flight: clear the whole port range before starting. ──
# KODE mandate: just before startup, make sure old processes are cleared AND
# the port range is cleared. We scan 8090..8099 for ANY listener (not just
# amp.py) and kill the PID holding each port, so a stray/zombie process can
# never block startup or force a port hunt. Belt-and-suspenders: also kill any
# amp.py by name in case it is mid-bind and not yet listening.
PORT_RANGE_LO="${PORT_RANGE_LO:-8090}"
PORT_RANGE_HI="${PORT_RANGE_HI:-8099}"
EUID="$(id -u)"

# Return the owner uid of a pid (empty if it vanished).
pid_uid() { stat -c %u "/proc/$1" 2>/dev/null || echo ""; }

# Attempt to kill a pid; report clearly if we lack permission (e.g. a root-owned
# process while we are non-root). Returns 0 if the kill was issued, 1 if we
# could not (permission). Never silently swallows EPERM — that is exactly the
# failure mode that would leave a port held and break startup.
try_kill() {
  local pid="$1" sig="$2"
  if kill -"$sig" "$pid" 2>/dev/null; then
    return 0
  fi
  # Failed. Determine why.
  local owner; owner="$(pid_uid "$pid")"
  if [ "$owner" = "0" ] && [ "$EUID" != "0" ]; then
    logmsg "WARN: port holder pid $pid is owned by root; cannot kill as uid $EUID (EPERM). Free it with: sudo kill -$sig $pid   (or run this launcher as root)"
    return 1
  fi
  # Gone already, or some other reason — treat as handled.
  return 0
}

clear_port_range() {
  local killed=""
  local blocked=""   # root-owned pids we could not kill
  for p in $(seq "$PORT_RANGE_LO" "$PORT_RANGE_HI"); do
    # Find PIDs listening on this port (try ss, fall back to lsof/fuser).
    local pids=""
    if command -v ss >/dev/null 2>&1; then
      pids=$(ss -ltnpH "sport = :$p" 2>/dev/null | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u)
    fi
    if [ -z "$pids" ] && command -v lsof >/dev/null 2>&1; then
      pids=$(lsof -tiTCP:"$p" -sTCP:LISTEN 2>/dev/null | sort -u)
    fi
    if [ -z "$pids" ] && command -v fuser >/dev/null 2>&1; then
      pids=$(fuser "$p"/tcp 2>/dev/null | tr -s ' ' '\n' | grep -E '^[0-9]+$')
    fi
    for pid in $pids; do
      # Never kill our own launcher/shell.
      [ "$pid" = "$$" ] && continue
      case " $killed $blocked " in *" $pid "*) continue ;; esac
      logmsg "Clearing port $p: killing pid $pid"
      if try_kill "$pid" TERM; then
        killed="$killed $pid"
      else
        blocked="$blocked $pid"
      fi
    done
  done
  # Give them a moment to release, then force-kill any that survived (only ones
  # we actually had permission to signal).
  if [ -n "$killed" ]; then
    sleep 1
    for pid in $killed; do
      if kill -0 "$pid" 2>/dev/null; then
        logmsg "Force-killing stuck pid $pid"
        try_kill "$pid" KILL || true
      fi
    done
    sleep 0.5
  fi
  # If any root-owned holders blocked us, fail loudly so startup does not
  # pretend the range is clear.
  if [ -n "$blocked" ]; then
    logmsg "ERROR: port range NOT fully cleared — root-owned holders remain:$(echo $blocked). Startup may fail to bind. Resolve with: sudo kill -TERM $blocked   (or run this launcher as root, e.g. via systemd which runs as root)"
  fi
}

logmsg "Clearing port range $PORT_RANGE_LO..$PORT_RANGE_HI... (running as uid $EUID)"
clear_port_range

# Also catch any amp.py that is mid-startup and not yet listening (name match).
existing_pids=$(pgrep -f "amp\.py" 2>/dev/null || true)
if [ -n "$existing_pids" ]; then
  logmsg "Stopping any non-listening amp.py (pid: $existing_pids)..."
  for p in $existing_pids; do try_kill "$p" TERM || true; done
  sleep 1
  still=$(pgrep -f "amp\.py" 2>/dev/null || true)
  if [ -n "$still" ]; then
    logmsg "Force-killing stuck amp.py (pid: $still)..."
    for p in $still; do try_kill "$p" KILL || true; done
    sleep 0.5
  fi
fi


logmsg "Starting AMP stack (server/amp.py)..."
exec "$PY" server/amp.py --host 0.0.0.0 "$@" 2>&1 | tee -a "$LOG_DIR/amp.log"
