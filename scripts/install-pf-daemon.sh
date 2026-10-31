#!/bin/bash
# Install the dev.fjorn.pf-syncthing LaunchDaemon. Run with sudo; re-run after editing
# setup-pf-tailscale-syncthing.sh.
#
# The daemon runs as root, so it must not execute a file the fjorn user can edit. This copies the
# script to a root-owned path and the plist to /Library/LaunchDaemons.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run with sudo" >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="dev.fjorn.pf-syncthing"
BIN_DIR="/usr/local/libexec/lil-homie"
PLIST="/Library/LaunchDaemons/${LABEL}.plist"

install -d -o root -g wheel -m 755 "${BIN_DIR}"
install -o root -g wheel -m 755 "${REPO_DIR}/scripts/setup-pf-tailscale-syncthing.sh" "${BIN_DIR}/"
install -o root -g wheel -m 644 "${REPO_DIR}/launchd/${LABEL}.plist" "${PLIST}"

launchctl bootout "system/${LABEL}" 2>/dev/null || true
launchctl bootstrap system "${PLIST}"
sleep 2
tail -1 /var/log/lil-homie-pf.log
