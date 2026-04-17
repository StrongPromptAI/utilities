#!/bin/bash
# Launch oidc_login setup in background so it doesn't block Apache startup.
# oidc-setup.sh waits for Nextcloud to be fully ready before running occ.
/oidc-setup.sh &
echo "[oidc-async] setup launched in background (pid $!)"
