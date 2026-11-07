#!/bin/bash
# Make a new Beszel API token for the Glance Metrics widget (BESZEL_API_TOKEN). Run on micro-homie
# with the homie stack up:
#
#   ~/homielab/scripts/beszel-glance-token.sh [email] [days]     (default: me@fjorn.dev, 365)
#
# A normal Beszel login token expires after 7 days. A PocketBase superuser can issue a token for
# a user with a longer life (the "impersonate" API). The script signs in as the superuser, issues
# the token for the Beszel user with the same email, and writes it to ~/.config/homielab/beszel-api-token
# (mode 0600). It does not print the token. The token cannot be refreshed, so run the script again
# before it expires. The README section "Beszel" has the steps to put the token in 1Password.
set -euo pipefail

EMAIL="${1:-me@fjorn.dev}"
DAYS="${2:-365}"
HUB="http://127.0.0.1:8090"
OUT="${HOME}/.config/homielab/beszel-api-token"

read -rsp "Beszel superuser password for ${EMAIL}: " P; echo
umask 077
mkdir -p "$(dirname "${OUT}")"

EMAIL="${EMAIL}" P="${P}" HUB="${HUB}" DAYS="${DAYS}" OUT="${OUT}" python3 - <<'PY'
import datetime, json, os, urllib.error, urllib.parse, urllib.request

hub, email, pw = os.environ["HUB"], os.environ["EMAIL"], os.environ["P"]
seconds = int(os.environ["DAYS"]) * 86400

def call(method, path, body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = token
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(hub + path, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path.split('?')[0]} failed: HTTP {e.code}")

su = call("POST", "/api/collections/_superusers/auth-with-password",
          {"identity": email, "password": pw})["token"]
query = urllib.parse.quote(f"email='{email}'")
users = call("GET", f"/api/collections/users/records?filter=({query})", token=su)["items"]
if not users:
    raise SystemExit(f"No Beszel user with email {email}")
token = call("POST", f"/api/collections/users/impersonate/{users[0]['id']}",
             {"duration": seconds}, token=su)["token"]
with open(os.environ["OUT"], "w") as f:
    f.write(token)
expires = datetime.date.today() + datetime.timedelta(seconds=seconds)
print(f"Token written to {os.environ['OUT']}. It expires about {expires}.")
PY
