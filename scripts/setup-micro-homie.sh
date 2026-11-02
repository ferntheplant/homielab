#!/bin/bash
# One-time host setup for micro-homie (Debian 13). Run with sudo. Safe to run again.
#
# Installs Docker Engine (Docker's apt repo), the 1Password CLI (1Password's apt repo) and the
# tools the scripts use, and sets the host options the stacks depend on.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run with sudo" >&2
    exit 1
fi

USER_NAME="${SUDO_USER:-fjorn}"
export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y ca-certificates curl gnupg git rsync sqlite3 python3

# Docker Engine + compose plugin
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<SRC
Types: deb
URIs: https://download.docker.com/linux/debian
Suites: $(. /etc/os-release && echo "${VERSION_CODENAME}")
Components: stable
Signed-By: /etc/apt/keyrings/docker.asc
SRC

# Rotate container logs (the default json-file driver never deletes them)
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<JSON
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
JSON

# 1Password CLI, with the package signature policy 1Password documents
curl -fsSL https://downloads.1password.com/linux/keys/1password.asc \
    | gpg --dearmor --yes --output /usr/share/keyrings/1password-archive-keyring.gpg
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/1password-archive-keyring.gpg] https://downloads.1password.com/linux/debian/amd64 stable main" \
    > /etc/apt/sources.list.d/1password.list
mkdir -p /etc/debsig/policies/AC2D62742012EA22 /usr/share/debsig/keyrings/AC2D62742012EA22
curl -fsSL https://downloads.1password.com/linux/debian/debsig/1password.pol \
    -o /etc/debsig/policies/AC2D62742012EA22/1password.pol
curl -fsSL https://downloads.1password.com/linux/keys/1password.asc \
    | gpg --dearmor --yes --output /usr/share/debsig/keyrings/AC2D62742012EA22/debsig.gpg

apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin 1password-cli
systemctl enable --now docker

# fjorn runs compose without sudo (the docker group is root-equivalent)
usermod -aG docker "${USER_NAME}"
# fjorn runs `tailscale serve` without sudo
tailscale set --operator="${USER_NAME}"
# User timers (systemd/) run without a login session
loginctl enable-linger "${USER_NAME}"
# Docker binds Syncthing's port to the tailnet IP, which can come up after Docker at boot
echo "net.ipv4.ip_nonlocal_bind = 1" > /etc/sysctl.d/90-homie.conf
sysctl --system >/dev/null
timedatectl set-timezone America/New_York

# The user manager must restart to pick up the docker group
systemctl restart "user@$(id -u "${USER_NAME}").service"

echo "Done. Log out and back in (or reconnect SSH) so your shell gets the docker group."
