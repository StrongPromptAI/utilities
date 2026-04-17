#!/bin/bash
# Disable conflicting Apache MPMs before Apache starts.
# Railway loads mpm_event and mpm_prefork simultaneously, crashing Apache.
a2dismod mpm_event mpm_worker 2>/dev/null || true
a2enmod mpm_prefork 2>/dev/null || true
echo "[mpm-fix] prefork enforced"
