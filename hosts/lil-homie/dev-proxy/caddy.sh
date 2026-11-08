#!/bin/bash
# Launcher for the dev proxy's Caddy (launchd/dev.fjorn.dev-proxy-caddy.plist).
# The env file holds CF_API_TOKEN (1Password homielab/Cloudflare), so it stays out of the repo.
set -a
source "$HOME/.config/homielab/cloudflare-dns.env"
set +a
exec "$HOME/.local/bin/caddy" run --adapter caddyfile --config "$HOME/homielab/hosts/lil-homie/dev-proxy/Caddyfile"
