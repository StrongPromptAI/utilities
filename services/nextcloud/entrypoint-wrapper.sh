#!/bin/bash
set -e

# Fix MPM — requires root. Runs every deploy regardless of path.
a2dismod mpm_event 2>/dev/null || true
a2enmod mpm_prefork
echo "[wrapper] MPM: prefork enforced"

CONFIG_DIR="/var/www/html/config"
mkdir -p "$CONFIG_DIR"
chown -R www-data:www-data "$CONFIG_DIR"
chmod 750 "$CONFIG_DIR"

# Always drop all oc_* tables and let the entrypoint do a clean install.
#
# The fast-path approach (detect installedat, regenerate config.php from env,
# skip install) is fundamentally broken without a persistent volume on
# /var/www/html/data and /var/www/html/config:
#   - Hand-written config.php instanceid/secret fight the DB-stored values
#   - occ upgrade hits "Configuration was not read or initialized correctly"
#   - needsDbUpgrade=true sticks, /status.php returns 503, deploy fails
#
# Revisit this once a Railway volume is mounted at /var/www/html/data (and
# ideally /var/www/html/config too — then config.php persists naturally and
# this wrapper can disappear entirely).
echo "[wrapper] dropping oc_* tables — fresh install each deploy (no data volume yet)"
php -r "
\$pdo = new PDO('pgsql:host=postgres.railway.internal;dbname=postgres',
               getenv('POSTGRES_USER'), getenv('POSTGRES_PASSWORD'),
               [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]);
\$tables = \$pdo->query(\"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'oc_%'\")->fetchAll(PDO::FETCH_COLUMN);
foreach (\$tables as \$t) {
    \$pdo->exec('DROP TABLE IF EXISTS \"' . \$t . '\" CASCADE');
}
echo '[wrapper] dropped ' . count(\$tables) . ' oc_* tables' . PHP_EOL;
" 2>&1

exec /entrypoint.sh "$@"
