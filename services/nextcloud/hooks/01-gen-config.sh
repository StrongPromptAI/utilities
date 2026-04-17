#!/bin/bash
# Regenerate config.php + copy version.php from env vars on every startup.
# Railway wipes the container filesystem on each redeploy. Without both files,
# Nextcloud re-runs installation against an already-populated DB and fails.
# version.php presence is what the entrypoint checks to determine "already installed".

CONFIG_DIR="/var/www/html/config"
CONFIG_FILE="$CONFIG_DIR/config.php"
mkdir -p "$CONFIG_DIR"

# version.php must exist to skip re-install
if [ ! -f "$CONFIG_DIR/version.php" ]; then
    cp /var/www/html/version.php "$CONFIG_DIR/version.php" 2>/dev/null || true
fi

cat > "$CONFIG_FILE" <<PHP
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

chown www-data:www-data "$CONFIG_FILE"
chmod 640 "$CONFIG_FILE"
echo "[gen-config] config.php + version.php written"
