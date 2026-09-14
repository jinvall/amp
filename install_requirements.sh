#!/usr/bin/env bash
# ── AMP Requirements Installer ──────────────────────────────────────────────
# Installs pip packages into their respective virtual environments.
#
# Usage:
#   ./install_requirements.sh           # install everything
#   ./install_requirements.sh --core    # main server only
#   ./install_requirements.sh --analysis  # analysis tools only
#   ./install_requirements.sh --separator # audio-separator only
#   ./install_requirements.sh --all     # everything including optional

set -euo pipefail

# ── Paths ────────────────────────────────────────────────────────────────────
ROOT="$(cd "$(dirname "$0")" && pwd)"
SERVER_VENV="$ROOT/server/venv"
BP_VENV="$ROOT/server/basic-pitch-venv"
SEP_VENV="$ROOT/audio-separator-env"
CORE_REQ="$ROOT/server/requirements.txt"
ANALYSIS_REQ="$ROOT/server/requirements-analysis.txt"
SEPARATOR_REQ="$ROOT/requirements-separator.txt"

# ── Colors ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${CYAN}[amp-install]${NC} $*"; }
ok()   { echo -e "${GREEN}[ok]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }
err()  { echo -e "${RED}[error]${NC} $*"; }

# ── Helpers ──────────────────────────────────────────────────────────────────
venv_exists() {
    [[ -d "$1" ]] && [[ -f "$1/bin/python" ]]
}

pip_install() {
    local venv="$1"
    local label="$2"
    local req_file="$3"
    
    if ! venv_exists "$venv"; then
        err "venv not found: $venv"
        return 1
    fi
    
    local pip="$venv/bin/pip"
    local py="$venv/bin/python"
    
    log "Installing into $label ($($py --version 2>&1))"
    
    # Upgrade pip first
    "$pip" install --upgrade pip setuptools wheel 2>/dev/null || true
    
    # Install requirements
    if [[ -f "$req_file" ]]; then
        "$pip" install -r "$req_file" 2>&1 | tail -5
    else
        warn "No requirements file: $req_file"
    fi
    
    ok "$label done"
}

# ── Parse args ───────────────────────────────────────────────────────────────
MODE="${1:---all}"

case "$MODE" in
    --core)
        pip_install "$SERVER_VENV" "server (core)" "$CORE_REQ"
        ;;
    --analysis)
        pip_install "$BP_VENV" "basic-pitch-venv (analysis)" "$ANALYSIS_REQ"
        ;;
    --separator)
        pip_install "$SEP_VENV" "audio-separator-env" "$SEPARATOR_REQ"
        ;;
    --all|"")
        log "Installing all requirements..."
        echo ""
        pip_install "$SERVER_VENV" "server (core)" "$CORE_REQ"
        echo ""
        pip_install "$BP_VENV" "basic-pitch-venv (analysis)" "$ANALYSIS_REQ"
        echo ""
        if venv_exists "$SEP_VENV"; then
            pip_install "$SEP_VENV" "audio-separator-env" "$SEPARATOR_REQ"
        else
            warn "audio-separator-env not found — skipping"
        fi
        ;;
    *)
        echo "Usage: $0 [--core|--analysis|--separator|--all]"
        exit 1
        ;;
esac

echo ""
log "Done."
