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

# Diagnostic + cleanup. Previous "permission denied for oc_migrations" errors
# suggest leftover objects (sequences, views, types) or tables in non-public
# schemas that the simple DROP TABLE didn't catch. Print role state and drop
# everything owned by the connecting user that matches oc_* across schemas.
php -r "
\$pdo = new PDO('pgsql:host=postgres.railway.internal;dbname=postgres',
               getenv('POSTGRES_USER'), getenv('POSTGRES_PASSWORD'),
               [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]);

\$r = \$pdo->query(\"SELECT current_user, session_user, current_setting('is_superuser') AS su\")->fetch();
echo '[wrapper/diag] current_user=' . \$r['current_user'] . ' session_user=' . \$r['session_user'] . ' superuser=' . \$r['su'] . PHP_EOL;

// Any oc_* tables across ALL schemas?
foreach (\$pdo->query(\"SELECT schemaname, tablename, tableowner FROM pg_tables WHERE tablename LIKE 'oc_%'\") as \$row) {
    echo '[wrapper/diag] leftover table ' . \$row['schemaname'] . '.' . \$row['tablename'] . ' owner=' . \$row['tableowner'] . PHP_EOL;
}

// Any oc_* roles?
foreach (\$pdo->query(\"SELECT rolname FROM pg_roles WHERE rolname LIKE 'oc_%' OR rolname LIKE 'nextcloud%'\") as \$row) {
    echo '[wrapper/diag] leftover role ' . \$row['rolname'] . PHP_EOL;
}
"

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
echo "[wrapper] dropping oc_* tables + sequences + views + types — fresh install each deploy"
php -r "
\$pdo = new PDO('pgsql:host=postgres.railway.internal;dbname=postgres',
               getenv('POSTGRES_USER'), getenv('POSTGRES_PASSWORD'),
               [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]);

\$counts = ['tables'=>0,'sequences'=>0,'views'=>0,'types'=>0];

foreach (\$pdo->query(\"SELECT schemaname, tablename FROM pg_tables WHERE tablename LIKE 'oc_%'\") as \$r) {
    \$pdo->exec('DROP TABLE IF EXISTS \"' . \$r['schemaname'] . '\".\"' . \$r['tablename'] . '\" CASCADE');
    \$counts['tables']++;
}
foreach (\$pdo->query(\"SELECT sequence_schema, sequence_name FROM information_schema.sequences WHERE sequence_name LIKE 'oc_%'\") as \$r) {
    \$pdo->exec('DROP SEQUENCE IF EXISTS \"' . \$r['sequence_schema'] . '\".\"' . \$r['sequence_name'] . '\" CASCADE');
    \$counts['sequences']++;
}
foreach (\$pdo->query(\"SELECT table_schema, table_name FROM information_schema.views WHERE table_name LIKE 'oc_%'\") as \$r) {
    \$pdo->exec('DROP VIEW IF EXISTS \"' . \$r['table_schema'] . '\".\"' . \$r['table_name'] . '\" CASCADE');
    \$counts['views']++;
}
foreach (\$pdo->query(\"SELECT n.nspname AS schema, t.typname AS name FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE t.typname LIKE 'oc_%' AND t.typtype='c'\") as \$r) {
    \$pdo->exec('DROP TYPE IF EXISTS \"' . \$r['schema'] . '\".\"' . \$r['name'] . '\" CASCADE');
    \$counts['types']++;
}
echo '[wrapper] dropped: ' . json_encode(\$counts) . PHP_EOL;
" 2>&1

exec /entrypoint.sh "$@"
