#!/bin/bash
# OIDC setup — runs synchronously from before-starting hook.
# Hook fires after Nextcloud install completes, so `occ status` should return
# installed:true on the first check. Each occ call is wrapped in `timeout 45`
# so a hung DB or network causes fast failure instead of blocking Apache forever.

OIDC_URL="${OIDC_LOGIN_PROVIDER_URL:-https://oidc-otp-production.up.railway.app}"
CLIENT_ID="${OIDC_LOGIN_CLIENT_ID:-nextcloud}"
CLIENT_SECRET="${OIDC_LOGIN_CLIENT_SECRET}"

log() { echo "[oidc-setup] $*"; }
# before-starting hooks already run as www-data (entrypoint switches user
# before invoking them). No sudo / runuser / su needed — would fail anyway
# since www-data can't switch users.
OCC="timeout 45 php /var/www/html/occ"

# Fail-soft: hook failures here must NOT abort the entrypoint (would crash-loop
# the container). We log diagnostics and exit 0 so Apache starts regardless.
trap 'log "hook exiting 0 so Apache can still start"; exit 0' ERR

log "occ status output ─────────"
$OCC status --no-ansi 2>&1 | sed 's/^/[oidc-setup] /' || true
log "─────────────────────────────"

# Retry up to 20s — gives occ a chance if startup bootstrapping is still running
READY=no
for i in 1 2 3 4 5 6 7 8 9 10; do
    if $OCC status --no-ansi 2>/dev/null | grep -q "installed: true"; then
        log "Nextcloud ready (check $i)."
        READY=yes
        break
    fi
    sleep 2
done

if [ "$READY" != "yes" ]; then
    log "WARN: occ status never returned installed:true. Skipping oidc setup; Apache will still start."
    exit 0
fi

# Run upgrade first — if the DB is at an older schema than the code (common
# after a fast-path redeploy where the entrypoint's upgrade check is bypassed),
# this clears the needsDbUpgrade=true state that otherwise makes /status.php
# 503 and blocks app:install ("not found on the appstore" during upgrade mode).
# Idempotent — no-op if already at latest.
log "Running occ upgrade (idempotent)..."
$OCC upgrade --no-ansi 2>&1 | sed 's/^/[oidc-setup] /' || log "upgrade returned non-zero (may be a no-op)"

# Install the app if missing. Idempotent — app:install bails out cleanly if
# already present.
if $OCC app:list --no-ansi 2>/dev/null | grep -q "oidc_login"; then
    log "oidc_login already installed."
else
    log "Installing oidc_login..."
    $OCC app:install oidc_login --no-ansi 2>&1 | sed 's/^/[oidc-setup] /'
fi

# Enable + configure (idempotent; safe to run every deploy)
$OCC app:enable oidc_login --no-ansi 2>&1 | sed 's/^/[oidc-setup] /'

log "Configuring oidc_login..."
$OCC config:system:set oidc_login_client_id           --value="$CLIENT_ID"
$OCC config:system:set oidc_login_client_secret       --value="$CLIENT_SECRET"
$OCC config:system:set oidc_login_provider_url        --value="$OIDC_URL"
$OCC config:system:set oidc_login_auto_redirect       --value=true  --type=boolean
$OCC config:system:set oidc_login_disable_registration --value=false --type=boolean
$OCC config:system:set oidc_login_attributes          --value='{"id":"email","name":"name","mail":"email"}' --type=json

log "oidc_login configured. Users will log in via OTP at $OIDC_URL"
