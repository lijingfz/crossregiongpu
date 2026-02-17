#!/usr/bin/env bash
# deploy.sh — Deploy GPU Scheduler Web Dashboard to an EC2 instance
#
# Usage:
#   sudo ./web_dashboard/deploy/deploy.sh [--port PORT]
#
# Prerequisites:
#   - Ubuntu/Amazon Linux EC2 instance
#   - Python 3.11+
#   - Nginx installed (apt install nginx / yum install nginx)
#
# Environment variables (set in /etc/default/web-dashboard):
#   AUTH_SECRET_KEY  — JWT signing key (required)
#   MEMORY_ID        — AgentCore Memory ID
#   MEMORY_REGION    — AWS region for Memory service
#
# Requirements: 8.1, 8.2, 8.3, 8.4, 8.5

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
INSTALL_DIR="/opt/web-dashboard"
PORT=8000

# ── Argument parsing ────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --help|-h)
      echo "Usage: sudo $0 [--port PORT]"
      exit 0 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

log()  { echo "[web-dashboard-deploy] $*"; }
err()  { echo "[web-dashboard-deploy] ERROR: $*" >&2; }
die()  { err "$@"; exit 1; }

# ── Pre-flight checks ──────────────────────────────────────────────────

[[ $EUID -eq 0 ]] || die "This script must be run as root (sudo)."

command -v python3 &>/dev/null || die "Python 3 not found."
command -v nginx &>/dev/null   || die "Nginx not found. Install: apt install nginx"

log "Deploying Web Dashboard from ${PROJECT_ROOT} to ${INSTALL_DIR}"

# ── Step 1: Copy project files ──────────────────────────────────────────

log "Copying project files..."
mkdir -p "$INSTALL_DIR"
rsync -a --exclude='.venv' --exclude='__pycache__' --exclude='.git' \
  "$PROJECT_ROOT/" "$INSTALL_DIR/"

# ── Step 2: Create virtual environment and install dependencies ─────────

log "Setting up Python virtual environment..."
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip -q
"$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/web_dashboard/requirements.txt" -q
"$INSTALL_DIR/.venv/bin/pip" install -e "$INSTALL_DIR" -q

log "Dependencies installed."

# ── Step 3: Environment file ────────────────────────────────────────────

ENV_FILE="/etc/default/web-dashboard"
if [[ ! -f "$ENV_FILE" ]]; then
  log "Creating environment file template at ${ENV_FILE}"
  cat > "$ENV_FILE" <<'EOF'
# Web Dashboard environment variables
# AUTH_SECRET_KEY=<your-jwt-secret>
# MEMORY_ID=<agentcore-memory-id>
# MEMORY_REGION=us-west-2
EOF
  log "WARNING: Edit ${ENV_FILE} and set AUTH_SECRET_KEY before starting the service."
fi

# ── Step 4: Install systemd service ─────────────────────────────────────

log "Installing systemd service..."
sed "s|--port 8000|--port ${PORT}|g" \
  "$INSTALL_DIR/web_dashboard/deploy/web-dashboard.service" \
  > /etc/systemd/system/web-dashboard.service

systemctl daemon-reload
systemctl enable web-dashboard

# ── Step 5: Configure Nginx ─────────────────────────────────────────────

log "Configuring Nginx reverse proxy..."
SERVER_NAME=$(hostname -f 2>/dev/null || echo "_")

sed "s|\${SERVER_NAME}|${SERVER_NAME}|g; s|127.0.0.1:8000|127.0.0.1:${PORT}|g" \
  "$INSTALL_DIR/web_dashboard/deploy/nginx.conf" \
  > /etc/nginx/sites-available/web-dashboard

# Enable site (create symlink if sites-enabled exists)
if [[ -d /etc/nginx/sites-enabled ]]; then
  ln -sf /etc/nginx/sites-available/web-dashboard /etc/nginx/sites-enabled/web-dashboard
  # Remove default site to avoid conflicts
  rm -f /etc/nginx/sites-enabled/default
fi

nginx -t || die "Nginx configuration test failed."
systemctl reload nginx

# ── Step 6: Start the service ───────────────────────────────────────────

log "Starting web-dashboard service..."
systemctl restart web-dashboard

# ── Done ────────────────────────────────────────────────────────────────

log ""
log "Deployment complete."
log "  Service status : systemctl status web-dashboard"
log "  Logs           : journalctl -u web-dashboard -f"
log "  Accessible at  : http://${SERVER_NAME}:80"
log ""
log "Remember to set AUTH_SECRET_KEY in ${ENV_FILE} if not already configured."
