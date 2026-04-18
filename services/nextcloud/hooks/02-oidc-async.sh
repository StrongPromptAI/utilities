#!/bin/bash
# Run OIDC setup synchronously.
# Hook fires after Nextcloud install is complete, so occ is usable immediately.
# Each occ call is wrapped in `timeout` so a hung DB can't block Apache forever.
# Synchronous so output goes straight to Railway logs (background process
# output was getting dropped in a previous async design).
/oidc-setup.sh
