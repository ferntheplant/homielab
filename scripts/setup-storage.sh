#!/bin/bash
# Host setup for the 8 TB btrfs storage drive on micro-homie. Run with sudo. Safe to run again.
#
# It does not wipe, partition, or format anything. Those steps are manual, see the README
# ("Storage drive"). The script expects a btrfs filesystem with the label `storage`, and it:
#   - installs btrfs-progs and btrbk
#   - makes the /srv/storage mount point and adds the fstab line (nofail, 30 s device timeout)
#   - mounts the drive and makes the archive, sync, backup and .snapshots subvolumes
#   - installs the btrbk config (daily snapshots) and the monthly scrub timer
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run with sudo" >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${REPO_DIR}/hosts/micro-homie/storage"
USER_NAME="${SUDO_USER:-fjorn}"
MOUNT=/srv/storage
OPTS=noatime,compress=zstd:1,nofail,x-systemd.device-timeout=30s
SUBVOLUMES=(archive sync backup .snapshots)

export DEBIAN_FRONTEND=noninteractive
apt-get install -y btrfs-progs btrbk

if ! DEV="$(blkid -L storage)"; then
    echo "No filesystem with the label 'storage'. Format the drive first (README, Storage drive)." >&2
    exit 1
fi
UUID="$(blkid -s UUID -o value "${DEV}")"

# While the drive is absent, the immutable flag stops writes into the empty folder on the SSD.
# Containers then fail with an error instead of filling the SSD.
if ! mountpoint -q "${MOUNT}"; then
    mkdir -p "${MOUNT}"
    chattr +i "${MOUNT}"
fi

if ! grep -q "UUID=${UUID}" /etc/fstab; then
    cp /etc/fstab "/etc/fstab.bak-$(date +%F)"
    echo "UUID=${UUID} ${MOUNT} btrfs ${OPTS} 0 0" >> /etc/fstab
fi
findmnt --verify
systemctl daemon-reload

mountpoint -q "${MOUNT}" || mount "${MOUNT}"
chown "${USER_NAME}:${USER_NAME}" "${MOUNT}"
for sv in "${SUBVOLUMES[@]}"; do
    if [[ ! -e "${MOUNT}/${sv}" ]]; then
        btrfs subvolume create "${MOUNT}/${sv}"
        chown "${USER_NAME}:${USER_NAME}" "${MOUNT}/${sv}"
        chmod 755 "${MOUNT}/${sv}"
    fi
done

install -m 644 "${SRC}/btrbk.conf" /etc/btrbk/btrbk.conf
install -m 644 "${SRC}/btrfs-scrub-storage.service" "${SRC}/btrfs-scrub-storage.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now btrbk.timer btrfs-scrub-storage.timer

btrbk dryrun
systemctl --no-pager list-timers btrbk.timer btrfs-scrub-storage.timer
