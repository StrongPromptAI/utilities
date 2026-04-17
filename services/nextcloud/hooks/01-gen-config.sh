#!/bin/bash
# Regenerate config.php from env vars on every startup.
# Railway wipes the container filesystem on each redeploy, so config.php
# must be recreated — otherwise Nextcloud re-runs installation against
# an already-populated database and fails with permission errors.

CONFIG_FILE="/var/www/html/config/config.php"
mkdir -p /var/www/html/config

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
echo "[gen-config] config.php written from env vars"
