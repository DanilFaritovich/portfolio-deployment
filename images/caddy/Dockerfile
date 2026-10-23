FROM gcr.io/distroless/static-debian13@sha256:034df5c1737e8241f6ed6423a12e720ee0f6d6f088609ee172f98f6f2166ef5d
COPY --chmod=0755 caddy /usr/bin/caddy
COPY --chmod=0644 LICENSE /usr/share/licenses/caddy/LICENSE
ENV XDG_CONFIG_HOME=/config XDG_DATA_HOME=/data CADDY_VERSION=v2.11.7
USER 0:0
WORKDIR /srv
EXPOSE 80/tcp 443/tcp
ENTRYPOINT []
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
