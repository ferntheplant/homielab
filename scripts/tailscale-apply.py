#!/usr/bin/env python3
"""Apply the tailnet config in tailscale/ through the Tailscale API. See README "Tailscale".

Usage:
  scripts/tailscale-apply.py            # plan: show what --apply would change, change nothing
  scripts/tailscale-apply.py --apply    # replace the live policy, define the Services, set the device tags
  scripts/tailscale-apply.py serve      # on micro-homie: serve each Service's endpoints (stacks/manage.sh serve)

The API commands use the OAuth client in the 1Password item homielab/Tailscale (scopes: policy_file, services,
devices:core). `op` reads it with the service account token in ~/.config/homielab/op-token, as manage.sh does.
"""

import difflib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POLICY = ROOT / "tailscale" / "policy.hujson"
SERVICES = ROOT / "tailscale" / "services.json"
API = "https://api.tailscale.com/api/v2"
OP_TOKEN_FILE = Path.home() / ".config" / "homielab" / "op-token"


def die(msg):
    sys.exit(f"ABORT: {msg}")


def load_config():
    cfg = json.loads(SERVICES.read_text())
    return cfg["services"], cfg["devices"]


def definition_ports(svc):
    """The Service definition's ports: an `https:443` or `tcp:22` endpoint needs `tcp:443` or `tcp:22`."""
    return sorted({"tcp:" + ep.split(":", 1)[1] for ep in svc["endpoints"]})


class Api:
    def __init__(self):
        self.token = self._token()

    @staticmethod
    def _op_read(ref):
        env = dict(os.environ)
        if "OP_SERVICE_ACCOUNT_TOKEN" not in env and OP_TOKEN_FILE.is_file():
            env["OP_SERVICE_ACCOUNT_TOKEN"] = OP_TOKEN_FILE.read_text().strip()
        try:
            out = subprocess.run(["op", "read", ref], check=True, capture_output=True, text=True, env=env)
        except subprocess.CalledProcessError as e:
            die(f"op read {ref}: {e.stderr.strip()}")
        return out.stdout.strip()

    def _token(self):
        form = {
            "client_id": self._op_read("op://homielab/Tailscale/Client ID"),
            "client_secret": self._op_read("op://homielab/Tailscale/Client secret"),
        }
        status, body = self.call(
            "POST",
            "/oauth/token",
            urllib.parse.urlencode(form).encode(),
            {"Content-Type": "application/x-www-form-urlencoded"},
            auth=False,
        )
        if status != 200:
            die(f"OAuth token: HTTP {status}: {body}")
        return json.loads(body)["access_token"]

    def call(self, method, path, data=None, headers=None, auth=True):
        headers = dict(headers or {})
        if auth:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(API + path, method=method, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def json(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        status, text = self.call(method, path, data, {"Content-Type": "application/json"})
        if status >= 300:
            die(f"{method} {path}: HTTP {status}: {text}")
        return json.loads(text) if text.strip() else None


def plan_policy(api):
    status, live = api.call("GET", "/tailnet/-/acl", headers={"Accept": "application/hujson"})
    if status != 200:
        die(f"GET policy: HTTP {status}: {live}")
    want = POLICY.read_text()
    status, text = api.call("POST", "/tailnet/-/acl/validate", want.encode(), {"Content-Type": "application/hujson"})
    result = json.loads(text) if text.strip() else {}
    if status != 200 or result.get("message"):
        die(f"policy.hujson does not validate: HTTP {status}: {text}")
    diff = list(difflib.unified_diff(live.splitlines(), want.splitlines(), "live policy", "tailscale/policy.hujson", lineterm=""))
    print("== policy: valid, " + ("no change" if not diff else "will change"))
    for line in diff:
        print(line)
    return bool(diff), want


def plan_services(api, services):
    live = {s["name"]: s for s in api.json("GET", "/tailnet/-/services")["vipServices"]}
    changes = []
    for name, svc in services.items():
        body = {"name": name, "comment": svc["comment"], "ports": definition_ports(svc)}
        cur = live.get(name)
        if cur is None:
            changes.append(("create", name, body))
        elif cur.get("comment", "") != body["comment"] or sorted(cur.get("ports", [])) != body["ports"]:
            # An update must send the Service's IPv4 and IPv6 addresses, or the API refuses it.
            changes.append(("update", name, {**body, "addrs": cur["addrs"]}))
    changes += [("delete", name, None) for name in live if name not in services]
    print("== services: " + ("no change" if not changes else f"{len(changes)} change(s)"))
    for action, name, body in changes:
        print(f"  {action} {name}" + (f" ports={body['ports']}" if body else ""))
    return changes


def plan_devices(api, devices):
    live = {d["hostname"]: d for d in api.json("GET", "/tailnet/-/devices")["devices"]}
    changes = []
    for host, tags in devices.items():
        dev = live.get(host) or die(f"no device with hostname {host}")
        if sorted(dev.get("tags") or []) != sorted(tags):
            changes.append((host, dev["nodeId"], dev.get("tags") or [], tags))
    print("== device tags: " + ("no change" if not changes else f"{len(changes)} change(s)"))
    for host, _, old, new in changes:
        print(f"  {host}: {old or '(owned by user)'} -> {new}")
    return changes


def apply(api):
    services, devices = load_config()
    policy_changed, policy = plan_policy(api)
    svc_changes = plan_services(api, services)
    dev_changes = plan_devices(api, devices)
    if "--apply" not in sys.argv:
        print("\nPlan only. Run with --apply to make these changes.")
        return
    # Policy first: the tags, grants, and autoApprovers must exist before the Services and tags that use them.
    if policy_changed:
        status, text = api.call("POST", "/tailnet/-/acl", policy.encode(), {"Content-Type": "application/hujson"})
        if status != 200:
            die(f"POST policy: HTTP {status}: {text}")
        print("applied: policy")
    for action, name, body in svc_changes:
        path = "/tailnet/-/services/" + urllib.parse.quote(name, safe="")
        api.json("DELETE" if action == "delete" else "PUT", path, body)
        print(f"applied: {action} {name}")
    for host, node_id, _, tags in dev_changes:
        api.json("POST", f"/device/{node_id}/tags", {"tags": tags})
        print(f"applied: tags {host} -> {tags}")


def serve():
    services, _ = load_config()
    for name, svc in services.items():
        for endpoint, target in svc["endpoints"].items():
            proto, port = endpoint.split(":", 1)
            cmd = ["tailscale", "serve", f"--service={name}", f"--{proto}={port}", target]
            print("+ " + " ".join(cmd))
            subprocess.run(cmd, check=True, timeout=30)


def main():
    if "serve" in sys.argv[1:]:
        serve()
    else:
        apply(Api())


if __name__ == "__main__":
    main()
