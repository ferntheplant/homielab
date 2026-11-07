#!/bin/bash
# Install the tailnet guard on micro-homie: an nftables table that keeps Docker containers off the
# tailnet (README "Tailnet guard"). Run with sudo. Safe to run again: it reloads the rules.
#
# The rules are copied to /etc/homielab/, owned by root, and a system unit loads them at boot,
# before Docker starts.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run with sudo" >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${REPO_DIR}/hosts/micro-homie/tailnet-guard"
UNIT=homielab-tailnet-guard.service

nft -c -f "${SRC}/tailnet-guard.nft"
install -d -m 0755 /etc/homielab
install -m 0644 "${SRC}/tailnet-guard.nft" /etc/homielab/tailnet-guard.nft
install -m 0644 "${SRC}/${UNIT}" "/etc/systemd/system/${UNIT}"
systemctl daemon-reload
systemctl enable "${UNIT}"
systemctl restart "${UNIT}"
nft list table inet homielab_tailnet_guard
