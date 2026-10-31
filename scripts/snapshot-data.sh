#!/bin/bash
# Consistent snapshot of stateful data for Backrest.
#
# Copying live SQLite/LevelDB files while an app writes to them can produce a backup that
# will not restore. This script stops the containers that own those files, APFS-clones
# their data folders into $DATA_PATH/_snapshot (copy-on-write, so it takes seconds and
# almost no disk space), and starts the containers again.
#
# Backrest backs up /userdata/_snapshot, not the live folders.
# Runs nightly from launchd/dev.fjorn.lil-homie-snapshot.plist and before `manage.sh update`.

set -euo pipefail

export PATH="$HOME/.orbstack/bin:/opt/homebrew/bin:$PATH"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
set -a
source "${REPO_DIR}/.env.shared"
set +a

# Containers that hold a database open in one of the folders below
CONTAINERS=(karakeep-web beszel syncthing beeper-discord beeper-facebook beeper-signal)
# Folders under $DATA_PATH to back up. karakeep-meilisearch is left out: Karakeep can rebuild
# the search index. backrest is left out: its config holds the repo password.
FOLDERS=(karakeep beszel_data syncthing beeper-discord beeper-facebook beeper-signal langfuse-archive)

SNAPSHOT_DIR="${DATA_PATH}/_snapshot"
TMP_DIR="${DATA_PATH}/_snapshot.tmp"

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

rm -rf "${TMP_DIR}"
mkdir -p "${TMP_DIR}"
for f in "${FOLDERS[@]}"; do
    if [[ -e "${DATA_PATH}/${f}" ]]; then
        cp -cR "${DATA_PATH}/${f}" "${TMP_DIR}/"
    fi
done

start_stopped
stopped=()

rm -rf "${SNAPSHOT_DIR}"
mv "${TMP_DIR}" "${SNAPSHOT_DIR}"
date '+%Y-%m-%dT%H:%M:%S%z' > "${SNAPSHOT_DIR}/.snapshot-time"
log "snapshot written to ${SNAPSHOT_DIR}"
