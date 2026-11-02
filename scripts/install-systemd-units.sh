#!/bin/bash
# Install the user timers from systemd/ (snapshot, update, classify). Run as fjorn, not root.
# Re-run after you edit a unit. Logs: journalctl --user -u <unit>
# Needs `loginctl enable-linger fjorn` (setup-micro-homie.sh), so the timers run without a login.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
TIMERS=(homie-snapshot.timer homie-update.timer karakeep-classify.timer)

mkdir -p "${UNIT_DIR}"
install -m 644 "${REPO_DIR}"/systemd/*.service "${REPO_DIR}"/systemd/*.timer "${UNIT_DIR}/"
systemctl --user daemon-reload
systemctl --user enable --now "${TIMERS[@]}"
systemctl --user list-timers "${TIMERS[@]}"
