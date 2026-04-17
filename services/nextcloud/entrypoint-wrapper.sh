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

# Drop all oc_* tables so Nextcloud installs fresh against a clean DB.
# The DB has schema from an old/partial install that the current image can't
# upgrade. After a successful fresh install, this wrapper will be updated to
# use an installedat-based guard instead.
echo "[wrapper] dropping oc_* tables for clean install"
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

exec /entrypoint.sh "$@"
