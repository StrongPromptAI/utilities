#!/bin/bash
set -e

# Fix MPM FIRST — requires root, must happen before Apache starts.
# Railway re-enables mpm_event at container start; build-time RUN doesn't persist.
a2dismod mpm_event 2>/dev/null || true
a2enmod mpm_prefork
echo "[wrapper] MPM: prefork enforced"

# Write config.php BEFORE the nextcloud entrypoint runs its install check.
# Hooks run AFTER the install check — too late to prevent re-installation.
CONFIG_DIR="/var/www/html/config"
mkdir -p "$CONFIG_DIR"

# version.php presence tells the entrypoint "already installed"
if [ ! -f "$CONFIG_DIR/version.php" ]; then
    cp /var/www/html/version.php "$CONFIG_DIR/version.php" 2>/dev/null || true
fi

cat > "$CONFIG_DIR/config.php" <<PHP
<?php
\$CONFIG = array(
  'instanceid'           => '${NEXTCLOUD_INSTANCE_ID}',
  'secret'               => '${NEXTCLOUD_SECRET}',
  'installed'            => true,
  'dbtype'               => 'pgsql',
  'dbname'               => 'postgres',
  'dbhost'               => 'postgres.railway.internal',
  'dbport'               => '',
  'dbtableprefix'        => 'oc_',
  'dbuser'               => '${POSTGRES_USER}',
  'dbpassword'           => '${POSTGRES_PASSWORD}',
  'trusted_domains'      => array( 0 => '*' ),
  'datadirectory'        => '/var/www/html/data',
  'overwriteprotocol'    => 'https',
  'overwrite.cli.url'    => 'https://nextcloud-production-83ae.up.railway.app',
  'htaccess.RewriteBase' => '/',
  'loglevel'             => 2,
  'maintenance'          => false,
);
PHP

chown www-data:www-data "$CONFIG_DIR/config.php"
chmod 640 "$CONFIG_DIR/config.php"
echo "[wrapper] config.php + version.php written"

exec /entrypoint.sh "$@"
