#!/bin/bash
# Let Rex clients on the tailnet (Rex for Mac, `rex -S`) use micro-homie as fjorn. Run with sudo.
# Safe to run again.
#
# The Debian package enables rex.service, a shared server that runs every terminal as the `rex`
# system account and listens only on a local socket. This switches to the login server instead:
# it identifies each caller by their Tailscale login, maps them to an account through
# /etc/rex/accounts.jsonc, and runs a Rex server as that account (here: fjorn).
#
# The login server listens on a second socket that the host's Tailscale Serve publishes on port
# 7297. Port 443 stays with Caddy (the dashboard). Clients connect to:
#
#   https://micro-homie.tail8cc0d3.ts.net:7297
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run with sudo" >&2
    exit 1
fi

TAILSCALE_LOGIN="ferntheplant@github"
ACCOUNT="fjorn"
ACCOUNTS_FILE="/etc/rex/accounts.jsonc"
DROPIN_DIR="/etc/systemd/system/rex-login-server.service.d"

# Keep the package's commented example file once, for reference
if [[ ! -e "${ACCOUNTS_FILE}.orig" ]]; then
    cp -p "${ACCOUNTS_FILE}" "${ACCOUNTS_FILE}.orig"
fi
cat > "${ACCOUNTS_FILE}" <<EOF
// Rex login server accounts. ${ACCOUNTS_FILE}.orig explains the format.
// Written by ~/lil-homie/scripts/setup-rex-login-server.sh
{
  "version": 1,
  "accounts": {
    "${ACCOUNT}": { "principals": ["tailscale:${TAILSCALE_LOGIN}"] },
  },
  "defaults": { "tailscale:${TAILSCALE_LOGIN}": "${ACCOUNT}" },
}
EOF

# The package's own example: a second socket for Tailscale Serve
mkdir -p "${DROPIN_DIR}"
cat > "${DROPIN_DIR}/tailnet-serve.conf" <<'EOF'
[Unit]
After=tailscaled.service
Requires=tailscaled.service

[Service]
ExecStart=
ExecStart=/usr/bin/rex login-server --listen unix:///run/rex/login-server.sock \
  --accounts /etc/rex/accounts.jsonc --runtime-root /run/rex --log-format text \
  --pam-service rex-login-server --extra-listen tailnet-serve:///run/rex/serve.sock
EOF

# Only one of the two unit pairs can use /run/rex. This ends any session in the shared server.
systemctl disable --now rex.service rex.socket
systemctl daemon-reload
systemctl enable rex-login-server.service
systemctl restart rex-login-server.service

for _ in $(seq 1 20); do
    [[ -S /run/rex/serve.sock ]] && break
    sleep 0.5
done
[[ -S /run/rex/serve.sock ]] || { echo "rex-login-server did not create /run/rex/serve.sock" >&2; exit 1; }

# Serve remembers this across reboots; manage.sh only adds its own ports and leaves this one
tailscale serve --bg --https=7297 unix:/run/rex/serve.sock

systemctl --no-pager --lines=5 status rex-login-server.service
echo
echo "Done. Connect from a Rex client to https://micro-homie.tail8cc0d3.ts.net:7297"
