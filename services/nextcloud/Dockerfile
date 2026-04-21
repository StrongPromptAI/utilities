FROM nextcloud:apache

# entrypoint-wrapper.sh runs as root BEFORE the nextcloud entrypoint.
# It writes config.php (so the install check is skipped) and fixes MPM.
# Hooks run AFTER the install check — too late for config.php or MPM.
COPY entrypoint-wrapper.sh /entrypoint-wrapper.sh
RUN chmod +x /entrypoint-wrapper.sh

RUN mkdir -p /docker-entrypoint-hooks.d/before-starting
COPY hooks/02-oidc-async.sh /docker-entrypoint-hooks.d/before-starting/
COPY oidc-setup.sh /oidc-setup.sh
RUN chmod +x \
    /docker-entrypoint-hooks.d/before-starting/02-oidc-async.sh \
    /oidc-setup.sh

ENTRYPOINT ["/entrypoint-wrapper.sh"]
CMD ["apache2-foreground"]
