#!/bin/bash
set -e

# ─── MPM fix (always — Railway re-enables mpm_event at container start) ────
a2dismod mpm_event 2>/dev/null || true
a2enmod mpm_prefork
echo "[wrapper] MPM: prefork enforced"

# ─── Filesystem scaffolding ────────────────────────────────────────────────
# Config dir must be writable by www-data (hooks + occ run as www-data).
# Data dir: even with S3 object storage, Nextcloud still touches /data for
# logs, tmp, .ocdata sentinel, and skeleton files on first login.
CONFIG_DIR=/var/www/html/config
mkdir -p "$CONFIG_DIR"             /var/www/html/data
chown -R www-data:www-data         "$CONFIG_DIR" /var/www/html/data
chmod 750                          "$CONFIG_DIR"

# ─── State machine: fast path vs. cleanup+fresh-install ────────────────────
# Decisions live in PHP because we need to query Postgres + write files + do
# all of it with the same escaping rules. Bash just orchestrates.
php /entrypoint-wrapper-state.php

# Entrypoint: install (if config.php absent / CAN_INSTALL present),
# run occ upgrade if version drifted, then run before-starting hooks, then
# exec the CMD (apache2-foreground).
exec /entrypoint.sh "$@"
