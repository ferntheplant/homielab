#!/bin/bash
# Build Caddy with the Cloudflare DNS plugin into ~/.local/bin/caddy. The dev proxy needs the plugin for its
# Let's Encrypt wildcard certificate (DNS challenge). See README.md here.
# The versions are pinned. Pick a release that is at least two weeks old when you bump them.
set -euo pipefail
CADDY_VERSION=v2.11.7
CLOUDFLARE_VERSION=v0.2.4
XCADDY_VERSION=v0.4.7
OUT="${HOME}/.local/bin/caddy"
mkdir -p "$(dirname "$OUT")"
cd "$(mktemp -d)"
go run "github.com/caddyserver/xcaddy/cmd/xcaddy@${XCADDY_VERSION}" build "$CADDY_VERSION" \
    --with "github.com/caddy-dns/cloudflare@${CLOUDFLARE_VERSION}" --output "$OUT"
"$OUT" version
"$OUT" list-modules | grep dns.providers.cloudflare
