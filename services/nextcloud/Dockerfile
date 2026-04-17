FROM nextcloud:apache

# Copy hooks into Nextcloud's built-in before-starting hooks directory.
# These run right before Apache starts, after entrypoint initialization.
RUN mkdir -p /docker-entrypoint-hooks.d/before-starting
COPY hooks/00-fix-mpm.sh    /docker-entrypoint-hooks.d/before-starting/
COPY hooks/01-gen-config.sh /docker-entrypoint-hooks.d/before-starting/
COPY hooks/02-oidc-async.sh /docker-entrypoint-hooks.d/before-starting/
COPY oidc-setup.sh /oidc-setup.sh
RUN chmod +x \
    /docker-entrypoint-hooks.d/before-starting/00-fix-mpm.sh \
    /docker-entrypoint-hooks.d/before-starting/01-gen-config.sh \
    /docker-entrypoint-hooks.d/before-starting/02-oidc-async.sh \
    /oidc-setup.sh
