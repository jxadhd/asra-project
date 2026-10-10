# The public page, served by Caddy, which also passes /api/* to the API. Built by compose.yaml.
FROM caddy:2.10-alpine
COPY docker/Caddyfile /etc/caddy/Caddyfile
COPY site/ /srv/
RUN caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
