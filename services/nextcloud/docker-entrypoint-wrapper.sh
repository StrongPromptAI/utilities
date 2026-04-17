#!/bin/bash
# Start the oidc setup script in background, then hand off to the real entrypoint.
/oidc-setup.sh &
exec /entrypoint.sh "$@"
