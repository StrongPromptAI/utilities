#!/bin/bash
# Runs in background after container starts.
# Waits for Nextcloud to finish first-run setup, then installs and configures oidc_login.

OIDC_URL="${OIDC_LOGIN_PROVIDER_URL:-https://oidc-otp-production.up.railway.app}"
CLIENT_ID="${OIDC_LOGIN_CLIENT_ID:-nextcloud}"
CLIENT_SECRET="${OIDC_LOGIN_CLIENT_SECRET}"

log() { echo "[oidc-setup] $*"; }
OCC="sudo -u www-data $OCC"

# Wait for Nextcloud to be fully initialized (occ becomes usable)
log "Waiting for Nextcloud to initialize..."
for i in $(seq 1 60); do
    if $OCC status --no-ansi 2>/dev/null | grep -q "installed: true"; then
        log "Nextcloud is ready."
        break
    fi
    sleep 5
done

if ! $OCC status --no-ansi 2>/dev/null | grep -q "installed: true"; then
    log "ERROR: Nextcloud did not initialize in time. Skipping oidc setup."
    exit 1
fi

# Install oidc_login if not already present
if $OCC app:list --no-ansi 2>/dev/null | grep -q "oidc_login"; then
    log "oidc_login already installed."
else
    log "Installing oidc_login..."
    $OCC app:install oidc_login --no-ansi 2>&1 | sed 's/^/[oidc-setup] /'
fi

# Enable it
$OCC app:enable oidc_login --no-ansi 2>&1 | sed 's/^/[oidc-setup] /'

# Configure
log "Configuring oidc_login..."
$OCC config:system:set oidc_login_client_id       --value="$CLIENT_ID"
$OCC config:system:set oidc_login_client_secret    --value="$CLIENT_SECRET"
$OCC config:system:set oidc_login_provider_url     --value="$OIDC_URL"
$OCC config:system:set oidc_login_auto_redirect     --value=true  --type=boolean
$OCC config:system:set oidc_login_disable_registration --value=false --type=boolean
$OCC config:system:set oidc_login_attributes        --value='{"id":"email","name":"name","mail":"email"}' --type=json

log "oidc_login configured. Users will log in via OTP at $OIDC_URL"
