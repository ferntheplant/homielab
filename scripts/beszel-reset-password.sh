#!/bin/bash
# Reset the Beszel login password without email. Run on micro-homie with the homie stack up:
#
#   ~/homielab/scripts/beszel-reset-password.sh [email]     (default: me@fjorn.dev)
#
# Beszel is PocketBase. The `beszel superuser upsert` CLI sets the superuser (PocketBase admin)
# password. The script then uses the superuser API to set the same password on the Beszel user
# account, which is the account the Beszel login page checks.
set -euo pipefail

EMAIL="${1:-me@fjorn.dev}"
HUB="http://127.0.0.1:8090"

read -rsp "New Beszel password for ${EMAIL}: " P; echo
read -rsp "Type it again: " P2; echo
[[ "${P}" == "${P2}" ]] || { echo "The passwords do not match" >&2; exit 1; }
(( ${#P} >= 8 )) || { echo "Use 8 or more characters" >&2; exit 1; }

# The password is a process argument for a moment here (visible in `ps` on this host only)
docker exec beszel /beszel superuser upsert "${EMAIL}" "${P}" >/dev/null
echo "Superuser password set"

EMAIL="${EMAIL}" P="${P}" HUB="${HUB}" python3 - <<'PY'
import json, os, urllib.parse, urllib.request

hub, email, pw = os.environ["HUB"], os.environ["EMAIL"], os.environ["P"]

def call(method, path, body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = token
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(hub + path, method=method, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)

token = call("POST", "/api/collections/_superusers/auth-with-password",
             {"identity": email, "password": pw})["token"]
query = urllib.parse.quote(f"email='{email}'")
users = call("GET", f"/api/collections/users/records?filter=({query})", token=token)["items"]
if not users:
    raise SystemExit(f"No Beszel user with email {email}")
call("PATCH", f"/api/collections/users/records/{users[0]['id']}",
     {"password": pw, "passwordConfirm": pw}, token=token)
print(f"User password set. Log in at the Beszel page as {email}.")
PY
