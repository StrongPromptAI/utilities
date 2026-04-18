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

# Detect whether a prior successful Nextcloud install exists in the DB.
# oc_appconfig.installedat is only set after a completed install.
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
    # Fast path: DB already has a complete install. Railway wiped the container
    # filesystem on redeploy, so rewrite config.php from env vars and skip install.
    echo "[wrapper] DB: installed — writing config.php, entrypoint will skip install"

    # Data directory is created by the entrypoint only during install. In the
    # skip-install fast path, we must create it ourselves — without it Nextcloud
    # 503s every request because datadirectory in config.php points nowhere.
    mkdir -p /var/www/html/data
    chown -R www-data:www-data /var/www/html/data
    chmod 750 /var/www/html/data

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
    # First run or DB wiped: drop stale oc_* tables and let entrypoint install fresh
    echo "[wrapper] DB: not installed — dropping oc_* tables for clean install"
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
