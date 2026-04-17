FROM nextcloud:apache

# Railway loads both mpm_event and mpm_prefork simultaneously, crashing Apache.
# Force prefork (required for mod_php) and disable the conflicting MPMs.
RUN a2dismod mpm_event mpm_worker 2>/dev/null || true && \
    a2enmod mpm_prefork
