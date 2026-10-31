#!/bin/bash
# Restrict Syncthing's data port (22000) to tailnet peers with a pf anchor.
#
# The rule matches the Tailscale CGNAT range (100.64.0.0/10), not a utun interface,
# because the utun number changes across reboots and Tailscale updates.
#
# macOS updates rewrite /etc/pf.conf (dropping the anchor) and pf is not enabled at boot, so the
# dev.fjorn.pf-syncthing LaunchDaemon runs this at boot and whenever /etc/pf.conf changes.
# Install it with: sudo ~/lil-homie/scripts/install-pf-daemon.sh
set -euo pipefail

ANCHOR_FILE="/private/etc/pf.anchors/tailscale-syncthing"
PF_CONF="/private/etc/pf.conf"

cat > "${ANCHOR_FILE}" <<RULES
pass in quick proto tcp from 100.64.0.0/10 to any port 22000
pass in quick proto udp from 100.64.0.0/10 to any port 22000
block drop in quick proto tcp from any to any port 22000
block drop in quick proto udp from any to any port 22000
RULES

if ! grep -q "tailscale-syncthing" "${PF_CONF}"; then
  printf '\nanchor "tailscale-syncthing"\nload anchor "tailscale-syncthing" from "/private/etc/pf.anchors/tailscale-syncthing"\n' >> "${PF_CONF}"
fi

# pfctl -f always prints a flush warning and ALTQ notices; show its output only on failure
if ! out=$(/sbin/pfctl -f "${PF_CONF}" 2>&1); then
  echo "${out}"
  exit 1
fi
/sbin/pfctl -e >/dev/null 2>&1 || true # non-zero when pf is already enabled

echo "$(date '+%Y-%m-%d %H:%M:%S') pf rules loaded. Syncthing port 22000 accepts tailnet peers only."
