#!/usr/bin/env python3
"""Point *.dev.fjorn.dev at lil-homie's tailnet IP in Cloudflare DNS. See README.md here.

Usage: hosts/lil-homie/dev-proxy/dns.py [--apply]

Without --apply it shows the record it would write. It reads CF_API_TOKEN from
~/.config/homielab/cloudflare-dns.env, the same token Caddy uses (Zone > Zone > Read and Zone > DNS > Edit on
fjorn.dev). The address is a tailnet address, so the name connects only on the tailnet. The record is DNS only:
Cloudflare's proxy cannot reach a tailnet address.
"""

import json
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ZONE = "fjorn.dev"
NAME = "*.dev.fjorn.dev"
API = "https://api.cloudflare.com/client/v4"
ENV_FILE = Path.home() / ".config" / "homielab" / "cloudflare-dns.env"


def token():
    for line in ENV_FILE.read_text().splitlines():
        if line.startswith("CF_API_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"')
    sys.exit(f"ABORT: no CF_API_TOKEN in {ENV_FILE}")


def call(method, path, tok, body=None):
    req = urllib.request.Request(
        API + path,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())["result"]
    except urllib.error.HTTPError as e:
        sys.exit(f"ABORT: {method} {path}: HTTP {e.code}: {e.read().decode()}")


def main():
    tok = token()
    ip = subprocess.run(["tailscale", "ip", "-4"], check=True, capture_output=True, text=True).stdout.split()[0]
    zone_id = call("GET", "/zones?" + urllib.parse.urlencode({"name": ZONE}), tok)[0]["id"]
    want = {"type": "A", "name": NAME, "content": ip, "ttl": 300, "proxied": False,
            "comment": "lil-homie dev proxy (homielab hosts/lil-homie/dev-proxy)"}
    found = call("GET", f"/zones/{zone_id}/dns_records?" + urllib.parse.urlencode({"type": "A", "name": NAME}), tok)
    if found and all(found[0].get(k) == v for k, v in want.items()):
        print(f"no change: {NAME} A {ip}")
        return
    action = "update" if found else "create"
    print(f"{action}: {NAME} A {ip}" + (f" (was {found[0]['content']})" if found else ""))
    if "--apply" not in sys.argv:
        print("Plan only. Run with --apply to write it.")
        return
    if found:
        call("PUT", f"/zones/{zone_id}/dns_records/{found[0]['id']}", tok, want)
    else:
        call("POST", f"/zones/{zone_id}/dns_records", tok, want)
    print("applied")


if __name__ == "__main__":
    main()
