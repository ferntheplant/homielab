#!/bin/bash
# Script to manage all docker-compose stacks
# Usage: ./manage.sh [start|stop|restart|status|logs|update|serve] [stack]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "${SCRIPT_DIR}")"
STACKS=("caddy" "beeper" "homie" "karakeep" "forgejo")
SHARED_ENV_FILE="${REPO_DIR}/.env.shared"

# Tailscale Serve: tailnet HTTPS port -> local target. Every target binds to 127.0.0.1 only.
TAILSCALE_SERVE=(
    "443=http://127.0.0.1:8080"  # caddy (dashboard + subpaths)
    "3003=http://127.0.0.1:3000" # karakeep (needs its own origin for the mobile app/extension)
    "3001=http://127.0.0.1:3001" # forgejo web, through its sign-in proxy
)
# Tailscale Serve TCP forwards: tailnet port -> local target
TAILSCALE_SERVE_TCP=(
    "2222=tcp://127.0.0.1:2222" # forgejo git over SSH
)

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Secrets live in the 1Password "homielab" vault. Each stack's .env.tpl maps env vars to op:// refs,
# and `op run` resolves them for compose. The read-only service account token is in a 0600 file.
OP_TOKEN_FILE="${HOME}/.config/homielab/op-token"

load_op_token() {
    if [[ -n "${OP_SERVICE_ACCOUNT_TOKEN:-}" ]]; then
        return 0
    fi
    if [[ ! -r "${OP_TOKEN_FILE}" ]]; then
        echo -e "${RED}No 1Password service account token in ${OP_TOKEN_FILE}${NC}" >&2
        exit 1
    fi
    OP_SERVICE_ACCOUNT_TOKEN=$(<"${OP_TOKEN_FILE}")
    export OP_SERVICE_ACCOUNT_TOKEN
}

compose_cmd() {
    if [[ -f ".env.tpl" ]]; then
        load_op_token
        op run --env-file .env.tpl -- docker compose --env-file "${SHARED_ENV_FILE}" "$@"
    elif [[ -f ".env" ]]; then
        docker compose --env-file "${SHARED_ENV_FILE}" --env-file .env "$@"
    else
        docker compose --env-file "${SHARED_ENV_FILE}" "$@"
    fi
}

# tailscale serve can block on an interactive prompt if Serve is not enabled for the tailnet
run_with_timeout() {
    local seconds=$1
    shift
    timeout "$seconds" "$@" </dev/null >/dev/null 2>&1 || true
}

ensure_tailscale_serve() {
    if ! command -v tailscale >/dev/null 2>&1; then
        echo -e "${YELLOW}tailscale not found; skipping serve setup.${NC}"
        return 0
    fi
    echo -e "${YELLOW}→ tailscale serve${NC}"
    for entry in "${TAILSCALE_SERVE[@]}"; do
        run_with_timeout 5 tailscale serve --bg --https="${entry%%=*}" "${entry#*=}"
    done
    for entry in "${TAILSCALE_SERVE_TCP[@]}"; do
        run_with_timeout 5 tailscale serve --bg --tcp="${entry%%=*}" "${entry#*=}"
    done
    tailscale serve status
}

is_valid_stack() {
    local stack=$1
    for s in "${STACKS[@]}"; do
        if [[ "$s" == "$stack" ]]; then
            return 0
        fi
    done
    return 1
}

require_stack() {
    local stack=$1
    if ! is_valid_stack "$stack"; then
        echo -e "${RED}Unknown stack: ${stack}${NC}"
        echo "Valid stacks: ${STACKS[*]}"
        exit 1
    fi
}

# Resolve the target stacks: the one named, or all of them (caddy first, it owns the network)
target_stacks() {
    if [[ -n "${1:-}" ]]; then
        require_stack "$1"
        echo "$1"
    else
        echo "${STACKS[@]}"
    fi
}

start_stacks() {
    echo -e "${GREEN}Starting: $*${NC}"
    for stack in "$@"; do
        echo -e "${YELLOW}→ ${stack}${NC}"
        cd "${SCRIPT_DIR}/${stack}"
        compose_cmd up -d --remove-orphans
    done
    ensure_tailscale_serve
}

stop_stacks() {
    echo -e "${RED}Stopping: $*${NC}"
    # Stop in reverse order (caddy last to avoid network issues)
    local stacks=("$@")
    for ((idx=${#stacks[@]}-1 ; idx>=0 ; idx--)); do
        echo -e "${YELLOW}→ ${stacks[$idx]}${NC}"
        cd "${SCRIPT_DIR}/${stacks[$idx]}"
        compose_cmd down
    done
}

status_stacks() {
    for stack in "$@"; do
        echo -e "\n${YELLOW}=== ${stack} ===${NC}"
        cd "${SCRIPT_DIR}/${stack}"
        compose_cmd ps
    done
}

logs_stacks() {
    local follow=$1
    shift
    echo -e "${YELLOW}Press Ctrl+C to exit${NC}\n"
    for stack in "$@"; do
        echo -e "\n${YELLOW}=== ${stack} ===${NC}"
        cd "${SCRIPT_DIR}/${stack}"
        compose_cmd logs $follow
    done
}

# Pull new images, rebuild local images on fresh bases, recreate changed containers
update_stacks() {
    echo -e "${GREEN}Updating: $*${NC}"
    "${REPO_DIR}/scripts/snapshot-data.sh"
    for stack in "$@"; do
        echo -e "${YELLOW}→ ${stack}${NC}"
        cd "${SCRIPT_DIR}/${stack}"
        compose_cmd pull --ignore-buildable
        compose_cmd build --pull
        compose_cmd up -d --remove-orphans
    done
    docker image prune -f
}

# Main command handling
case "${1:-}" in
    start)
        start_stacks $(target_stacks "${2:-}")
        ;;
    stop)
        stop_stacks $(target_stacks "${2:-}")
        ;;
    restart)
        stop_stacks $(target_stacks "${2:-}")
        start_stacks $(target_stacks "${2:-}")
        ;;
    status)
        status_stacks $(target_stacks "${2:-}")
        ;;
    logs)
        if [[ -n "${2:-}" ]] && is_valid_stack "${2}"; then
            logs_stacks "${3:-}" "${2}"
        else
            logs_stacks "${2:-}" "${STACKS[@]}"
        fi
        ;;
    update)
        update_stacks $(target_stacks "${2:-}")
        ;;
    serve)
        ensure_tailscale_serve
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|logs [--follow]|update|serve} [stack]"
        echo ""
        echo "Commands:"
        echo "  start   - Start all stacks or a single stack"
        echo "  stop    - Stop all stacks or a single stack"
        echo "  restart - Restart all stacks or a single stack"
        echo "  status  - Show status of all stacks"
        echo "  logs    - Show logs from all stacks (add --follow for tail)"
        echo "  update  - Snapshot data, pull/rebuild images, recreate containers, prune old images"
        echo "  serve   - (Re)apply the Tailscale Serve config"
        echo ""
        echo "Stacks: ${STACKS[*]}"
        exit 1
        ;;
esac
