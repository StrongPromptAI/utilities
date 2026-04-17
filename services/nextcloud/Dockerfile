FROM nextcloud:apache

# Railway loads both mpm_event and mpm_prefork simultaneously, crashing Apache.
# Force prefork (required for mod_php) and disable the conflicting MPMs.
RUN a2dismod mpm_event mpm_worker 2>/dev/null || true && \
    a2enmod mpm_prefork

# Startup hook: waits for Nextcloud to be ready, then installs + configures oidc_login.
# Runs in the background so Apache starts immediately.
COPY oidc-setup.sh /oidc-setup.sh
RUN chmod +x /oidc-setup.sh

COPY docker-entrypoint-wrapper.sh /docker-entrypoint-wrapper.sh
RUN chmod +x /docker-entrypoint-wrapper.sh

ENTRYPOINT ["/docker-entrypoint-wrapper.sh"]
CMD ["apache2-foreground"]
