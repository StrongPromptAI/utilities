<?php
/**
 * Wrapper state machine — decide fast path vs. fresh install for Nextcloud.
 *
 * Runs as ROOT before /entrypoint.sh. Talks to the oxp-kb Postgres directly.
 *
 * Three outcomes:
 *   1. "healthy"  → write config.php from live DB values + env vars; entrypoint
 *                   skips install; occ upgrade only if version drifted.
 *   2. "orphaned" → orphan oc_admin*/oc_user* roles present (crashed prior
 *                   install). DROP OWNED BY them first, then drop all oc_*
 *                   tables/seq/views/types; entrypoint installs fresh.
 *   3. "fresh"    → no installedat sentinel. Just let entrypoint install.
 *
 * The fast-path config.php reads instanceid/secret/passwordsalt from the LIVE
 * oc_appconfig (NOT from env vars). Hand-written config.php with env-var
 * values was the root cause of the "Configuration was not read or initialized
 * correctly" wall we hit earlier — DB-stored values and env values diverge.
 */

declare(strict_types=1);

const CONFIG_DIR   = '/var/www/html/config';
const CONFIG_FILE  = CONFIG_DIR . '/config.php';
const VERSION_FILE = CONFIG_DIR . '/version.php';

// ─── Connect ────────────────────────────────────────────────────────────────

$pdo = new PDO(
    'pgsql:host=postgres.railway.internal;dbname=' . (getenv('POSTGRES_DB') ?: 'postgres'),
    getenv('POSTGRES_USER'),
    getenv('POSTGRES_PASSWORD'),
    [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION],
);

function log_line(string $msg): void { echo "[wrapper] $msg" . PHP_EOL; }

// ─── Assess state ──────────────────────────────────────────────────────────

function is_installed(PDO $pdo): bool {
    try {
        $n = $pdo->query("SELECT 1 FROM oc_appconfig WHERE appid='core' AND configkey='installedat' LIMIT 1")
                 ->fetchColumn();
        return (bool)$n;
    } catch (Throwable $e) { return false; }
}

function orphan_roles(PDO $pdo): array {
    try {
        return $pdo->query("SELECT rolname FROM pg_roles
                            WHERE rolname LIKE 'oc_admin%' OR rolname LIKE 'oc_user%'
                            ORDER BY rolname")
                   ->fetchAll(PDO::FETCH_COLUMN);
    } catch (Throwable $e) { return []; }
}

// ─── Fresh-install cleanup ─────────────────────────────────────────────────

function cleanup(PDO $pdo): array {
    $counts = ['roles' => 0, 'tables' => 0, 'sequences' => 0, 'views' => 0, 'types' => 0];

    // Drop orphan roles first. DROP OWNED BY also clears everything they own
    // AND every privilege granted to them, which is the reason DROP ROLE ever
    // fails on a "some objects depend on it" error.
    foreach ($pdo->query("SELECT rolname FROM pg_roles
                          WHERE rolname LIKE 'oc_admin%' OR rolname LIKE 'oc_user%'")
             as $r) {
        $ident = '"' . str_replace('"', '""', $r['rolname']) . '"';
        try { $pdo->exec('DROP OWNED BY ' . $ident . ' CASCADE'); } catch (Throwable $e) {}
        try { $pdo->exec('DROP ROLE IF EXISTS ' . $ident);         } catch (Throwable $e) {}
        $counts['roles']++;
    }

    foreach ($pdo->query("SELECT schemaname, tablename FROM pg_tables WHERE tablename LIKE 'oc_%'") as $r) {
        $pdo->exec('DROP TABLE IF EXISTS "' . $r['schemaname'] . '"."' . $r['tablename'] . '" CASCADE');
        $counts['tables']++;
    }
    foreach ($pdo->query("SELECT sequence_schema, sequence_name
                          FROM information_schema.sequences
                          WHERE sequence_name LIKE 'oc_%'") as $r) {
        $pdo->exec('DROP SEQUENCE IF EXISTS "' . $r['sequence_schema'] . '"."' . $r['sequence_name'] . '" CASCADE');
        $counts['sequences']++;
    }
    foreach ($pdo->query("SELECT table_schema, table_name
                          FROM information_schema.views
                          WHERE table_name LIKE 'oc_%'") as $r) {
        $pdo->exec('DROP VIEW IF EXISTS "' . $r['table_schema'] . '"."' . $r['table_name'] . '" CASCADE');
        $counts['views']++;
    }
    foreach ($pdo->query("SELECT n.nspname schema, t.typname name
                          FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
                          WHERE t.typname LIKE 'oc_%' AND t.typtype='c'") as $r) {
        $pdo->exec('DROP TYPE IF EXISTS "' . $r['schema'] . '"."' . $r['name'] . '" CASCADE');
        $counts['types']++;
    }
    return $counts;
}

// ─── Fast-path config.php writer ───────────────────────────────────────────

function read_core_config(PDO $pdo): array {
    $stmt = $pdo->query("SELECT configkey, configvalue FROM oc_appconfig
                         WHERE appid='core' AND configkey IN
                         ('instanceid','secret','passwordsalt','installedversion')");
    $out = [];
    foreach ($stmt as $r) $out[$r['configkey']] = $r['configvalue'];
    return $out;
}

/**
 * Write config.php with LIVE instanceid/secret/salt from the DB plus env-var
 * values for everything else. PHP export for safety — no manual escaping of
 * password specials.
 */
function write_config_php(array $dbcfg): void {
    $conf = [
        // Identity: read from DB, NOT env (env values drift → line 273 error)
        'instanceid'             => $dbcfg['instanceid']   ?? '',
        'secret'                 => $dbcfg['secret']       ?? '',
        'passwordsalt'           => $dbcfg['passwordsalt'] ?? '',

        'installed'              => true,
        'dbtype'                 => 'pgsql',
        'dbname'                 => getenv('POSTGRES_DB') ?: 'postgres',
        'dbhost'                 => getenv('POSTGRES_HOST') ?: 'postgres.railway.internal',
        'dbport'                 => '',
        'dbtableprefix'          => 'oc_',
        'dbuser'                 => getenv('POSTGRES_USER'),
        'dbpassword'             => getenv('POSTGRES_PASSWORD'),
        'trusted_domains'        => ['*'],
        'datadirectory'          => '/var/www/html/data',
        'overwriteprotocol'      => getenv('OVERWRITEPROTOCOL') ?: 'https',
        'overwritehost'          => getenv('OVERWRITEHOST')      ?: '',
        'overwrite.cli.url'      => getenv('OVERWRITECLIURL')    ?: '',
        'htaccess.RewriteBase'   => '/',
        'loglevel'               => 2,
        'maintenance'            => false,
    ];

    // S3 object store — only if the env tells us to.
    if ($bucket = getenv('OBJECTSTORE_S3_BUCKET')) {
        $conf['objectstore'] = [
            'class'     => '\\OC\\Files\\ObjectStore\\S3',
            'arguments' => [
                'bucket'         => $bucket,
                'hostname'       => getenv('OBJECTSTORE_S3_HOST'),
                'port'           => (int)(getenv('OBJECTSTORE_S3_PORT') ?: 443),
                'key'            => getenv('OBJECTSTORE_S3_KEY'),
                'secret'         => getenv('OBJECTSTORE_S3_SECRET'),
                'use_ssl'        => filter_var(getenv('OBJECTSTORE_S3_SSL'), FILTER_VALIDATE_BOOLEAN),
                'use_path_style' => filter_var(getenv('OBJECTSTORE_S3_USEPATH_STYLE'), FILTER_VALIDATE_BOOLEAN),
                'region'         => getenv('OBJECTSTORE_S3_REGION') ?: 'auto',
                'autocreate'     => filter_var(getenv('OBJECTSTORE_S3_AUTOCREATE'), FILTER_VALIDATE_BOOLEAN),
            ],
        ];
    }

    $body = "<?php\n\$CONFIG = " . var_export($conf, true) . ";\n";
    file_put_contents(CONFIG_FILE, $body);
    chmod(CONFIG_FILE, 0640);
    @chown(CONFIG_FILE, 'www-data');
    @chgrp(CONFIG_FILE, 'www-data');
}

function sync_version_file(): void {
    // version.php presence + match tells the entrypoint "no upgrade needed".
    // Always pull from /usr/src/nextcloud — that's the CURRENT image version.
    // If the DB says installedversion differs, entrypoint's occ upgrade will
    // handle migration.
    $src = '/usr/src/nextcloud/version.php';
    if (file_exists($src)) {
        copy($src, VERSION_FILE);
        @chown(VERSION_FILE, 'www-data');
    }
}

// ─── Main decision ─────────────────────────────────────────────────────────

$installed = is_installed($pdo);
$orphans   = orphan_roles($pdo);

if ($installed && empty($orphans)) {
    // Fast path. Healthy install; just rebuild the ephemeral filesystem state.
    log_line('state=healthy — fast path (reuse DB; regen config.php from live values)');
    $dbcfg = read_core_config($pdo);
    if (empty($dbcfg['instanceid']) || empty($dbcfg['passwordsalt'])) {
        log_line('WARN: installedat set but instanceid/passwordsalt missing — falling through to cleanup');
        $installed = false;   // fall through
    } else {
        write_config_php($dbcfg);
        sync_version_file();
        log_line('config.php + version.php written from DB values');
        return;  // exit cleanly; bash continues to /entrypoint.sh
    }
}

if ($orphans) {
    log_line('state=orphaned — found ' . count($orphans) . ' orphan role(s): ' . implode(', ', $orphans));
}
if (!$installed) {
    log_line('state=' . ($orphans ? 'orphaned' : 'fresh') . ' — cleaning DB for fresh install');
}

$counts = cleanup($pdo);
log_line('dropped: ' . json_encode($counts));
log_line('entrypoint will install fresh using NEXTCLOUD_ADMIN_USER env var');
