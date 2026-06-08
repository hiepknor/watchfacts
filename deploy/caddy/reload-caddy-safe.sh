#!/usr/bin/env bash
set -euo pipefail

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  echo "This script uses sudo and expects user '$USER' to have permissions for Caddy service and /etc/caddy."
fi

DEFAULT_CONFIG="/etc/caddy/Caddyfile"
CONFIG_FILE="${1:-$DEFAULT_CONFIG}"
BACKUP_DIR="/etc/caddy/caddyfile-backups"
TS="$(date +%Y%m%d-%H%M%S)"
BASE_NAME="$(basename "$CONFIG_FILE")"
BACKUP_FILE="${BACKUP_DIR}/${BASE_NAME}.${TS}.bak"

if [ ! -f "$CONFIG_FILE" ]; then
  echo "Config not found: $CONFIG_FILE"
  exit 1
fi

mkdir -p "$BACKUP_DIR"

echo "Backup: $CONFIG_FILE -> $BACKUP_FILE"
sudo cp "$CONFIG_FILE" "$BACKUP_FILE"

echo "Validating: $CONFIG_FILE"
if ! sudo caddy validate --config "$CONFIG_FILE" >/tmp/caddy-validate.out 2>&1; then
  echo "Caddy validation failed. Dumping diagnostics:"
  cat /tmp/caddy-validate.out
  exit 1
fi

echo "Reloading Caddy"
if ! sudo systemctl reload caddy; then
  echo "Reload failed. Restoring: $BACKUP_FILE"
  sudo cp "$BACKUP_FILE" "$CONFIG_FILE"
  sudo caddy validate --config "$CONFIG_FILE"
  sudo systemctl reload caddy
  echo "Rollback complete"
  exit 1
fi

echo "Reload succeeded"
