#!/bin/bash
# Install the dev.fjorn.tailscaled LaunchDaemon. Run with sudo; re-run after `nb upgrade tailscale`.
#
# The daemon runs as root, so it must not execute a file the fjorn user can edit. This copies
# the nanobrew tailscaled binary to a root-owned path (like install-pf-daemon.sh does).
# tailscaled keeps its state in /Library/Tailscale, so the node stays logged in.
#
# Restarting tailscaled drops the tailnet for a few seconds, and an SSH session over the tailnet
# can drop with it. The script ignores SIGHUP and writes only to its log, so it finishes anyway.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run with sudo" >&2
    exit 1
fi

HOST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="dev.fjorn.tailscaled"
OLD_LABEL="homebrew.mxcl.tailscale"
SOURCE="/opt/nanobrew/prefix/bin/tailscaled"
BIN_DIR="/usr/local/libexec/lil-homie"
PLIST="/Library/LaunchDaemons/${LABEL}.plist"
LOG="/var/log/lil-homie-tailscaled-install.log"

[[ -x "${SOURCE}" ]] || { echo "${SOURCE} not found: run nb install tailscale" >&2; exit 1; }

echo "Installing ${LABEL}. Log: ${LOG}"
trap '' HUP
exec >>"${LOG}" 2>&1
echo "$(date '+%Y-%m-%d %H:%M:%S') installing $("${SOURCE}" --version | head -1)"

install -d -o root -g wheel -m 755 "${BIN_DIR}"
install -o root -g wheel -m 755 "${SOURCE}" "${BIN_DIR}/tailscaled"
install -o root -g wheel -m 644 "${HOST_DIR}/launchd/${LABEL}.plist" "${PLIST}"

launchctl bootout "system/${OLD_LABEL}" 2>/dev/null || true
# Keep the Homebrew plist for a rollback until Homebrew is gone:
#   sudo launchctl bootout system/dev.fjorn.tailscaled
#   sudo launchctl bootstrap system /usr/local/libexec/lil-homie/homebrew.mxcl.tailscale.plist
if [[ -f "/Library/LaunchDaemons/${OLD_LABEL}.plist" ]]; then
    mv "/Library/LaunchDaemons/${OLD_LABEL}.plist" "${BIN_DIR}/${OLD_LABEL}.plist"
fi
launchctl bootout "system/${LABEL}" 2>/dev/null || true
launchctl bootstrap system "${PLIST}"

sleep 5
echo "$(date '+%Y-%m-%d %H:%M:%S') $(/opt/nanobrew/prefix/bin/tailscale status --self --peers=false 2>&1 | head -1)"
