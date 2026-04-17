#!/bin/bash
set -e

# Fix MPM (requires root — hooks run as www-data and can't do this)
a2dismod mpm_event 2>/dev/null || true
a2enmod mpm_prefork
echo "[wrapper] MPM: prefork enforced"

CONFIG_DIR="/var/www/html/config"
mkdir -p "$CONFIG_DIR"
chown -R www-data:www-data "$CONFIG_DIR"
chmod 750 "$CONFIG_DIR"

# Check if Nextcloud is properly installed in the DB.
# Railway wipes the filesystem on each deploy, so we can't rely on config.php
# persisting. Instead, check oc_appconfig for the installedat key — that only
# exists after a successful install. Based on the result:
#   installed  → write config.php so the entrypoint skips re-install
#   not installed → drop all oc_* tables so the entrypoint installs fresh
INSTALLED=$(php -r "
\$dsn = 'pgsql:host=postgres.railway.internal;dbname=postgres';
try {
    \$pdo = new PDO(\$dsn, getenv('POSTGRES_USER'), getenv('POSTGRES_PASSWORD'));
    \$stmt = \$pdo->query(\"SELECT 1 FROM oc_appconfig WHERE appid='core' AND configkey='installedat' LIMIT 1\");
    echo \$stmt->fetchColumn() ? 'yes' : 'no';
} catch (Exception \$e) {
    echo 'no';
}
" 2>/dev/null || echo "no")

if [ "$INSTALLED" = "yes" ]; then
    echo "[wrapper] DB: installed — writing config.php, skipping install"

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
    echo "[wrapper] config.php written"

else
    echo "[wrapper] DB: partial/missing install — dropping oc_* tables for clean install"
    php -r "
    \$dsn = 'pgsql:host=postgres.railway.internal;dbname=postgres';
    \$pdo = new PDO(\$dsn, getenv('POSTGRES_USER'), getenv('POSTGRES_PASSWORD'), [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]);
    \$tables = \$pdo->query(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'oc_%'\")->fetchAll(PDO::FETCH_COLUMN);
    foreach (\$tables as \$t) {
        \$pdo->exec('DROP TABLE IF EXISTS \"' . \$t . '\" CASCADE');
    }
    echo '[wrapper] dropped ' . count(\$tables) . ' oc_* tables' . PHP_EOL;
    " 2>&1
    echo "[wrapper] entrypoint will install fresh using NEXTCLOUD_ADMIN_USER env var"
fi

exec /entrypoint.sh "$@"
