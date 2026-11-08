#!/bin/bash
# Launcher for the portless proxy (launchd/dev.fjorn.portless.plist): plain HTTP on 127.0.0.1:1355.
# Caddy in front of it does TLS. launchd has no mise in PATH, so add its shims for `node`.
export PATH="$HOME/.local/share/mise/shims:$PATH"
exec "$HOME/.local/share/portless/node_modules/.bin/portless" proxy start --no-tls -p 1355 --foreground
