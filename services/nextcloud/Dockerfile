FROM nextcloud:apache

# MPM fix must run as root (CMD), not in hooks (hooks run as www-data).
# Railway re-enables mpm_event at container start, so build-time RUN doesn't work.

RUN mkdir -p /docker-entrypoint-hooks.d/before-starting
COPY hooks/01-gen-config.sh /docker-entrypoint-hooks.d/before-starting/
COPY hooks/02-oidc-async.sh /docker-entrypoint-hooks.d/before-starting/
COPY oidc-setup.sh /oidc-setup.sh
RUN chmod +x \
    /docker-entrypoint-hooks.d/before-starting/01-gen-config.sh \
    /docker-entrypoint-hooks.d/before-starting/02-oidc-async.sh \
    /oidc-setup.sh

CMD ["bash", "-c", "a2dismod mpm_event 2>/dev/null; a2enmod mpm_prefork && apache2-foreground"]
