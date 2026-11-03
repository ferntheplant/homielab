#!/bin/bash
# Consistent snapshot of stateful data for Backrest.
#
# Copying live SQLite/LevelDB files while an app writes to them can produce a backup that
# will not restore. This script stops the containers that own those files, copies
# their data folders into $DATA_PATH/_snapshot (about 1.3 GB, a few seconds on the SSD),
# and starts the containers again.
#
# Backrest backs up /userdata/_snapshot, not the live folders.
#
# The copy runs in a short-lived root container. The beeper bridges run as root and write
# root-only files (config.yaml holds the logins), so fjorn cannot read them. binaries/ is
# left out: each bridge downloads it again on start.
# Runs nightly from systemd/homie-snapshot.timer and before `manage.sh update`.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
set -a
source "${REPO_DIR}/.env.shared"
set +a

# Containers that hold a database open in one of the folders below
CONTAINERS=(karakeep-web beszel syncthing beeper-discord beeper-facebook beeper-signal)
# Folders under $DATA_PATH to back up. karakeep-meilisearch is left out: Karakeep can rebuild
# the search index. backrest is left out: its config holds the repo password.
FOLDERS=(karakeep beszel_data syncthing beeper-discord beeper-facebook beeper-signal langfuse-archive)

# Small image that is already on the box; it only needs sh, cp, rm and mv
COPY_IMAGE=alpine:3.20

# Run a shell script as root with $DATA_PATH mounted at /data
as_root() {
    docker run --rm --network none -v "${DATA_PATH}:/data" "${COPY_IMAGE}" sh -euc "$@"
}

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }

stopped=()
start_stopped() {
    if [[ ${#stopped[@]} -gt 0 ]]; then
        log "starting: ${stopped[*]}"
        docker start "${stopped[@]}" >/dev/null
    fi
}
trap start_stopped EXIT

for c in "${CONTAINERS[@]}"; do
    if [[ "$(docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null)" == "true" ]]; then
        stopped+=("$c")
    fi
done

if [[ ${#stopped[@]} -gt 0 ]]; then
    log "stopping: ${stopped[*]}"
    docker stop -t 30 "${stopped[@]}" >/dev/null
fi

as_root '
    rm -rf /data/_snapshot.tmp
    mkdir -p /data/_snapshot.tmp
    for f in "$@"; do
        if [ -e "/data/$f" ]; then cp -a "/data/$f" /data/_snapshot.tmp/; fi
    done
    rm -rf /data/_snapshot.tmp/beeper-*/binaries
' sh "${FOLDERS[@]}"

start_stopped
stopped=()

as_root '
    rm -rf /data/_snapshot
    mv /data/_snapshot.tmp /data/_snapshot
    echo "$1" > /data/_snapshot/.snapshot-time
' sh "$(date '+%Y-%m-%dT%H:%M:%S%z')"
log "snapshot written to ${DATA_PATH}/_snapshot"
