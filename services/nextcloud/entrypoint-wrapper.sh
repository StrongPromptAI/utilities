#!/bin/bash
# Phase 2 final form: persistent-mode wrapper, fail-closed on inconsistency.
#
#   state=healthy   → config.php present on volume + DB has installedat → noop.
#                     Entrypoint will skip install.
#   state=fresh     → neither present. Drop any orphan oc_admin*/oc_user*
#                     roles (Nextcloud install leaks them when crashed) and
#                     let the entrypoint install. Install writes config.php
#                     onto the persistent /var/www/html/config volume, so this
#                     branch should fire once and never again.
#   state=inconsistent → exactly one present, the other missing. REFUSE to
#                     self-heal (would imply silent data loss). Log the
#                     reconciliation options and exit 1. Railway will
#                     crashloop; admin reconciles by hand.
#
# Persistence invariants expected at boot:
#   - /var/www/html/config       — Railway volume
#   - Postgres                   — Railway volume (db)
#   - /var/www/html/data         — Railway Storage Bucket via OBJECTSTORE_S3_*
#
# Design rationale (Codex + GLM 5.1 quick-take, 2026-04-21):
#   - Auto-destructive "drop oc_* whenever state is weird" is a foot-gun.
#     A transient postgres slowness returning an empty check can't be allowed
#     to nuke a healthy DB. So: fail-closed on mismatch.
#   - Identity values (instanceid, secret, passwordsalt) live ONLY in config.php,
#     never in oc_appconfig. Reading them from DB is impossible; persisting
#     the file on a volume is the only sound approach.
set -e

# ─── Always: MPM fix (Railway re-enables mpm_event at container start) ─────
a2dismod mpm_event 2>/dev/null || true
a2enmod mpm_prefork
echo "[wrapper] MPM: prefork enforced"

# ─── Always: data dir perms + sentinel ─────────────────────────────────────
# Even with S3 as primary object store, /var/www/html/data is still required
# to exist locally with a sentinel. Nextcloud 33 checks for `.ncdata` (content
# "# Nextcloud data directory") on EVERY boot. The install writes it; skipping
# install does not. Since this dir is ephemeral (regenerated each deploy),
# write the sentinel every time — idempotent.
mkdir -p /var/www/html/data
echo "# Nextcloud data directory" > /var/www/html/data/.ncdata
chown -R www-data:www-data /var/www/html/data
chmod 750 /var/www/html/data
chmod 640 /var/www/html/data/.ncdata

# ─── Always: config volume perms ───────────────────────────────────────────
# The volume comes up owned by root. Nextcloud + occ run as www-data and
# need write access (e.g., for `occ config:system:set`).
mkdir -p /var/www/html/config
chown -R www-data:www-data /var/www/html/config
chmod 750 /var/www/html/config

# ─── State detection ───────────────────────────────────────────────────────
CONFIG_FILE=/var/www/html/config/config.php
[ -f "$CONFIG_FILE" ] && config_present=yes || config_present=no

db_installed=$(php -r "
// Step 1: CONNECT. Failure here = postgres unreachable = fatal; let it bubble.
try {
    \$pdo = new PDO('pgsql:host=' . (getenv('POSTGRES_HOST') ?: 'postgres.railway.internal') .
                   ';dbname=' . (getenv('POSTGRES_DB') ?: 'postgres'),
                   getenv('POSTGRES_USER'), getenv('POSTGRES_PASSWORD'),
                   [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
                    PDO::ATTR_TIMEOUT => 10]);
} catch (Throwable \$e) {
    echo 'unreachable:' . \$e->getMessage();
    exit;
}
// Step 2: QUERY. Missing table = fresh DB = 'no'. Other errors also 'no' —
// the only way we return 'yes' is a clean successful fetch of the sentinel.
try {
    \$r = \$pdo->query(\"SELECT 1 FROM oc_appconfig WHERE appid='core' AND configkey='installedat' LIMIT 1\")->fetchColumn();
    echo \$r ? 'yes' : 'no';
} catch (PDOException \$e) {
    echo 'no';
}
")

echo "[wrapper] state: config.php=$config_present, db_installedat=$db_installed"

# ─── Branch ────────────────────────────────────────────────────────────────
case "$config_present/$db_installed" in
    yes/yes)
        echo "[wrapper] state=healthy — entrypoint will skip install"
        ;;
    no/no)
        echo "[wrapper] state=fresh — cleaning any orphan roles, then entrypoint will install"
        php -r "
        \$pdo = new PDO('pgsql:host=' . (getenv('POSTGRES_HOST') ?: 'postgres.railway.internal') .
                       ';dbname=' . (getenv('POSTGRES_DB') ?: 'postgres'),
                       getenv('POSTGRES_USER'), getenv('POSTGRES_PASSWORD'),
                       [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]);
        // Orphan oc_admin*/oc_user* roles left from a prior crashed install
        // block the next install with 'permission denied for table oc_migrations'.
        // Drop ownership first so DROP ROLE succeeds.
        \$n = 0;
        foreach (\$pdo->query(\"SELECT rolname FROM pg_roles
                                 WHERE rolname LIKE 'oc_admin%'
                                    OR rolname LIKE 'oc_user%'\") as \$r) {
            \$q = '\"' . str_replace('\"', '\"\"', \$r['rolname']) . '\"';
            try { \$pdo->exec('DROP OWNED BY ' . \$q . ' CASCADE'); } catch (Throwable \$e) {}
            try { \$pdo->exec('DROP ROLE IF EXISTS ' . \$q); \$n++; } catch (Throwable \$e) {}
        }
        // Also drop any lingering oc_* tables. On a truly fresh DB this is a noop.
        \$t = 0;
        foreach (\$pdo->query(\"SELECT schemaname, tablename FROM pg_tables WHERE tablename LIKE 'oc_%'\") as \$r) {
            \$pdo->exec('DROP TABLE IF EXISTS \"' . \$r['schemaname'] . '\".\"' . \$r['tablename'] . '\" CASCADE');
            \$t++;
        }
        echo '[wrapper] cleaned ' . \$n . ' role(s), ' . \$t . ' table(s)' . PHP_EOL;
        "
        ;;
    yes/no|no/yes)
        echo "[wrapper] ────────────────────────────────────────────────────"
        echo "[wrapper] INCONSISTENT STATE — refusing to start."
        echo "[wrapper]   config.php present?    $config_present"
        echo "[wrapper]   DB has installedat?    $db_installed"
        echo "[wrapper] A prior install was partial, or a restore went sideways."
        echo "[wrapper] Self-healing disabled to avoid silent data loss."
        echo "[wrapper]"
        echo "[wrapper] RESOLUTION OPTIONS:"
        echo "[wrapper]   - If DB is authoritative (config volume is empty/corrupt):"
        echo "[wrapper]       rm /var/www/html/config/config.php  (via dashboard or shell)"
        echo "[wrapper]       + redeploy — wrapper will error again until DB also wiped,"
        echo "[wrapper]         so either restore config or drop oc_* tables manually."
        echo "[wrapper]   - If config volume is authoritative (DB was wiped):"
        echo "[wrapper]       restore DB from Railway volume backup, then redeploy."
        echo "[wrapper]   - If both are garbage (POC / it's fine to lose everything):"
        echo "[wrapper]       rm -rf /var/www/html/config/*  AND  DROP SCHEMA public CASCADE;"
        echo "[wrapper]       then CREATE SCHEMA public; and redeploy."
        echo "[wrapper] ────────────────────────────────────────────────────"
        exit 1
        ;;
    *)
        # Only reached when db_installed=unreachable:... (Postgres connect failed)
        echo "[wrapper] $db_installed"
        echo "[wrapper] refusing to start — Postgres unreachable. Crashlooping until it recovers."
        exit 1
        ;;
esac

exec /entrypoint.sh "$@"
