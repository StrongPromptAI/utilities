#!/bin/bash
# OIDC setup — runs synchronously from before-starting hook.
# Hook fires after Nextcloud install completes, so `occ status` should return
# installed:true on the first check. Each occ call is wrapped in `timeout 45`
# so a hung DB or network causes fast failure instead of blocking Apache forever.

OIDC_URL="${OIDC_LOGIN_PROVIDER_URL:-https://oidc-otp-production.up.railway.app}"
CLIENT_ID="${OIDC_LOGIN_CLIENT_ID:-nextcloud}"
CLIENT_SECRET="${OIDC_LOGIN_CLIENT_SECRET}"

log() { echo "[oidc-setup] $*"; }
OCC="timeout 45 sudo -u www-data php /var/www/html/occ"

# One-shot readiness check — by the time this hook fires, install is done.
# A few seconds of retries cover the rare case where Apache hasn't finished
# bootstrapping occ yet.
for i in 1 2 3 4 5; do
    if $OCC status --no-ansi 2>/dev/null | grep -q "installed: true"; then
        log "Nextcloud ready (check $i)."
        break
    fi
    sleep 2
done

if ! $OCC status --no-ansi 2>/dev/null | grep -q "installed: true"; then
    log "ERROR: Nextcloud not installed after 10s. Aborting oidc setup."
    exit 1
fi

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
