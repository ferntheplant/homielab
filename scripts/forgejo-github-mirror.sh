#!/bin/bash
# Mirror a Forgejo repo to GitHub, read-only. See README "GitHub mirrors".
# Usage: scripts/forgejo-github-mirror.sh <repo>
#
# For ferntheplant/<repo>, this script:
#   1. stops if GitHub has a branch or tag commit that Forgejo does not have (the mirror would delete it)
#   2. scans the history with gitleaks if the history is not public yet (new or private GitHub repo)
#   3. makes the GitHub repo (public) if it does not exist
#   4. adds a Forgejo push mirror over SSH (sync on each push and every 8 h)
#   5. adds the mirror's public key to the GitHub repo as a deploy key with write access
#   6. lets deploy keys bypass any existing GitHub ruleset, then adds two rulesets (branches, tags): only
#      deploy keys may create, update, or delete refs
#   7. turns off GitHub issues, wiki, and projects
#   8. runs the first sync and compares the refs
#
# The Forgejo repo must be public: that marks it as mirrored. The script makes a short-lived Forgejo API token
# and deletes it at exit through the web UI. It goes straight to the sign-in proxy on 127.0.0.1 and sends the
# Tailscale-User-Login header itself, which the proxy accepts from a process on this box (stacks/forgejo/Caddyfile).
# So it does not depend on the box's own tailnet identity, which a tagged box does not have.

set -euo pipefail

OWNER=ferntheplant
FORGEJO=http://127.0.0.1:3001
TS_LOGIN=$(sed -n 's/^TS_LOGIN=//p' "$(dirname "$0")/../.env.shared")
GITLEAKS_IMAGE=ghcr.io/gitleaks/gitleaks:v8.30.1

repo=${1:?usage: $0 <repo>}
bare=/var/lib/gitea/git/repositories/${OWNER}/${repo}.git
fj_git() { docker exec forgejo git -C "$bare" "$@"; }
log() { echo "== $*"; }
die() { echo "ABORT: $*" >&2; exit 1; }

[[ -n $TS_LOGIN ]] || die "no TS_LOGIN in .env.shared"

# Short-lived API token. Reverse proxy authentication does not cover the API.
token_name="github-mirror-$(date +%s)"
token=$(docker exec forgejo forgejo admin user generate-access-token -u "$OWNER" -t "$token_name" \
    --scopes write:repository,read:user --raw 2>/dev/null | tail -1)
[[ ${#token} -ge 40 ]] || die "could not make a Forgejo API token"
web() { curl -fsS -H "Tailscale-User-Login: $TS_LOGIN" "$@"; }
delete_token() {
    local id
    id=$(web "$FORGEJO/user/settings/applications" | python3 -c '
import re, sys
page = sys.stdin.read()
for name, token_id in re.findall(r"flex-item-title[^>]*>\s*([^<]+?)\s*<.*?tokens/delete\" data-id=\"(\d+)\"", page, re.S):
    if name == sys.argv[1]:
        print(token_id)
' "$token_name")
    if [[ -n $id ]]; then
        web -o /dev/null -H "Origin: $FORGEJO" -H 'Sec-Fetch-Site: same-origin' -d "id=$id" \
            "$FORGEJO/user/settings/applications/tokens/delete" || true
    fi
    if web "$FORGEJO/user/settings/applications" | grep -q "$token_name"; then
        echo "WARNING: delete the Forgejo token $token_name by hand (Settings > Applications)" >&2
    fi
}
trap delete_token EXIT
api() { curl -fsS -H "Authorization: token $token" -H 'Content-Type: application/json' "$@"; }

log "$repo: Forgejo repo"
private=$(api "$FORGEJO/api/v1/repos/$OWNER/$repo" | python3 -c 'import json,sys; print(json.load(sys.stdin)["private"])')
[[ $private == False ]] || die "the Forgejo repo is private. Make it public first, to mark it as mirrored"
mirrors=$(api "$FORGEJO/api/v1/repos/$OWNER/$repo/push_mirrors" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')
[[ $mirrors == 0 ]] || die "the Forgejo repo already has a push mirror"

log "GitHub repo"
gh_visibility=$(gh repo view "$OWNER/$repo" --json visibility --jq .visibility 2>/dev/null || echo MISSING)
echo "visibility: $gh_visibility"

if [[ $gh_visibility == MISSING ]]; then
    scan=yes
else
    [[ $gh_visibility == PRIVATE ]] && scan=yes || scan=no
    log "check that GitHub has nothing that Forgejo lacks"
    while read -r sha ref; do
        [[ $ref == refs/pull/* || $ref == HEAD ]] && continue
        fsha=$(fj_git rev-parse -q --verify "$ref" 2>/dev/null || true)
        [[ -n $fsha ]] || die "$ref is on GitHub but not in Forgejo"
        if [[ $fsha != "$sha" ]] && ! fj_git merge-base --is-ancestor "$sha" "$fsha" 2>/dev/null; then
            die "GitHub $ref ($sha) is not in the Forgejo history"
        fi
    done < <(git ls-remote "https://github.com/$OWNER/$repo.git")
    echo "ok"
fi

if [[ $scan == yes ]]; then
    log "gitleaks: the history becomes public, so scan all of it"
    docker run --rm --network none -v "$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/var/lib/gitea"}}{{.Source}}{{end}}{{end}}' forgejo)/git/repositories/$OWNER/$repo.git:/repo:ro" \
        "$GITLEAKS_IMAGE" git /repo --no-banner || die "gitleaks found possible secrets"
fi

if [[ $gh_visibility == MISSING ]]; then
    log "make the GitHub repo"
    desc=$(api "$FORGEJO/api/v1/repos/$OWNER/$repo" | python3 -c 'import json,sys; print(json.load(sys.stdin)["description"])')
    gh repo create "$OWNER/$repo" --public --description "$desc"
fi

log "Forgejo push mirror"
pubkey_file=$(mktemp)
api -d "{\"remote_address\":\"git@github.com:$OWNER/$repo.git\",\"use_ssh\":true,\"sync_on_commit\":true,\"interval\":\"8h0m0s\"}" \
    "$FORGEJO/api/v1/repos/$OWNER/$repo/push_mirrors" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); open(sys.argv[1], "w").write(d["public_key"] + "\n"); print(d["remote_address"])' "$pubkey_file"

log "GitHub deploy key (write)"
gh repo deploy-key add "$pubkey_file" --allow-write --title "Forgejo push mirror (micro-homie)" -R "$OWNER/$repo"
rm -f "$pubkey_file"

log "GitHub rulesets: existing rulesets must let deploy keys through"
# An existing ruleset (for example a `main` ruleset that requires a PR) blocks the mirror's push
# unless deploy keys can bypass it.
for id in $(gh api "repos/$OWNER/$repo/rulesets" --jq '.[] | select(.name | startswith("read-only mirror") | not) | .id'); do
    gh api "repos/$OWNER/$repo/rulesets/$id" | python3 -c '
import json, sys
d = json.load(sys.stdin)
actors = d.get("bypass_actors") or []
if any(a["actor_type"] == "DeployKey" for a in actors):
    sys.exit(0)
body = {k: d[k] for k in ("name", "target", "enforcement", "conditions", "rules")}
body["bypass_actors"] = actors + [{"actor_id": None, "actor_type": "DeployKey", "bypass_mode": "always"}]
print(json.dumps(body))' > /tmp/ruleset-$$.json
    if [[ -s /tmp/ruleset-$$.json ]]; then
        gh api -X PUT "repos/$OWNER/$repo/rulesets/$id" --input /tmp/ruleset-$$.json --jq '"ruleset \(.id): \(.name): deploy keys may bypass"'
    fi
    rm -f /tmp/ruleset-$$.json
done

log "GitHub rulesets: only deploy keys may write"
for target in branch tag; do
    python3 -c '
import json, sys
target = sys.argv[1]
print(json.dumps({
    "name": "read-only mirror (branches)" if target == "branch" else "read-only mirror (tags)",
    "target": target,
    "enforcement": "active",
    "bypass_actors": [{"actor_id": None, "actor_type": "DeployKey", "bypass_mode": "always"}],
    "conditions": {"ref_name": {"include": ["~ALL"], "exclude": []}},
    "rules": [{"type": "creation"}, {"type": "update"}, {"type": "deletion"}, {"type": "non_fast_forward"}],
}))' "$target" | gh api -X POST "repos/$OWNER/$repo/rulesets" --input - --jq '"ruleset \(.id): \(.name)"'
done

log "GitHub: turn off issues, wiki, projects"
gh repo edit "$OWNER/$repo" --enable-issues=false --enable-wiki=false --enable-projects=false

log "first sync"
api -X POST "$FORGEJO/api/v1/repos/$OWNER/$repo/push_mirrors-sync"
status=""
for _ in $(seq 1 30); do
    status=$(api "$FORGEJO/api/v1/repos/$OWNER/$repo/push_mirrors" \
        | python3 -c 'import json,sys; m=json.load(sys.stdin)[0]; print((m.get("last_update") or "") + " " + (m.get("last_error") or ""))')
    [[ $status != "" && $status != 0001-* ]] && break
    sleep 2
done
echo "last update, last error: $status"

log "compare refs"
gh_refs=$(git ls-remote "https://github.com/$OWNER/$repo.git" | grep -v -E 'refs/pull/|HEAD$' | sort)
fj_refs=$(fj_git for-each-ref --format='%(objectname)	%(refname)' refs/heads refs/tags | sort)
if [[ $gh_refs == "$fj_refs" ]]; then
    echo "MATCH: $(grep -c . <<<"$gh_refs") refs"
else
    diff <(echo "$gh_refs") <(echo "$fj_refs") || true
    die "the refs differ. See the mirror in the repo settings (Settings > Repository > Mirror settings)"
fi
