# Dev proxy

A dev server on lil-homie is reachable from any tailnet device at `https://<name>.dev.fjorn.dev`, with a real
certificate and no per-project setting. It is for apps under development. A stable app moves to micro-homie and
gets a Tailscale Service instead (`tailscale/services.json`).

```text
browser --> *.dev.fjorn.dev (Cloudflare DNS: lil-homie's tailnet IP)
        --> lil-homie :443 (Tailscale Serve, TCP)
        --> Caddy on 127.0.0.1:8443 (TLS: Let's Encrypt wildcard)
            - 4321.dev.fjorn.dev  --> localhost:4321
            - myapp.dev.fjorn.dev --> portless on 127.0.0.1:1355 as myapp.localhost --> the app's port
```

## Use it

Start a dev server through portless. It picks a free port and gives the server a name:

```bash
portless myapp pnpm dev     # https://myapp.dev.fjorn.dev
portless pnpm dev           # name from package.json, or the directory
```

A server that portless did not start is reachable by its port: `https://4321.dev.fjorn.dev`.

portless adds `--port` and `--host` for Vite, Astro, and similar tools. It cannot add them to a script that chains
commands (`&&`, `|`) or delegates (`npm run x`). Then set the port from `$PORT` in the script.

## What the app sees

- **Host:** Caddy sends `myapp.localhost` (or `localhost:4321`), so a dev server that accepts only localhost names
  (Vite's `allowedHosts`, Astro, webpack) needs no setting. Sending the real name gives a 403 from Vite (tested).
- **X-Forwarded-Host** is the real name, and **X-Forwarded-Proto** is `https`.
- **Redirects:** Caddy rewrites a `Location` header that points at `localhost` or `*.localhost` back to the real
  name (tested).
- **Live reload:** Vite's HMR WebSocket works through the proxy (tested with Astro 7).

An app that builds absolute URLs from the Host header in its page body (not a redirect) shows `localhost` links.
Next.js checks the browser's Origin too, so a Next app may need `allowedDevOrigins: ['*.dev.fjorn.dev']`.

## Parts

| Part | File | Runs as |
| --- | --- | --- |
| Caddy, with the Cloudflare DNS plugin | `Caddyfile`, `caddy.sh`, `build-caddy.sh` | `dev.fjorn.dev-proxy-caddy` |
| portless proxy, plain HTTP on 127.0.0.1:1355 | `portless.sh` | `dev.fjorn.portless` |
| DNS record `*.dev.fjorn.dev` | `dns.py` | by hand |
| Tailnet port 443 → 127.0.0.1:8443 | Tailscale Serve | `tailscaled` |

Caddy cannot listen on the tailnet IP itself: macOS lets only root bind port 443 on one address. Tailscale Serve
forwards the TCP connection instead, which also keeps the proxy off the LAN.

The Cloudflare token needs **Zone → Zone → Read** and **Zone → DNS → Edit**, on `fjorn.dev` only. It is an
account-owned token, in the 1Password item `homielab/Cloudflare DNS`, field `CLOUDFLARE_API_TOKEN`. Caddy uses it
for the DNS challenge, and `dns.py` for the record.

## Install

```bash
# 1. Caddy (pinned versions in the script) and portless (in its own prefix, so a Node upgrade keeps it)
hosts/lil-homie/dev-proxy/build-caddy.sh
npm install --prefix ~/.local/share/portless --ignore-scripts portless@0.15.11
ln -sf ~/.local/share/portless/node_modules/.bin/portless ~/.local/bin/portless

# 2. The token, from 1Password
mkdir -p ~/.config/homielab && (umask 077; printf 'CF_API_TOKEN=%s\n' \
  "$(op read 'op://homielab/Cloudflare DNS/CLOUDFLARE_API_TOKEN')" > ~/.config/homielab/cloudflare-dns.env)

# 3. DNS (plan, then apply)
hosts/lil-homie/dev-proxy/dns.py
hosts/lil-homie/dev-proxy/dns.py --apply

# 4. The agents (see "lil-homie (the Mac)" in the main README), then the tailnet port
mkdir -p ~/Library/Logs/dev-proxy
for l in dev-proxy-caddy portless; do
  cp hosts/lil-homie/launchd/dev.fjorn.$l.plist ~/Library/LaunchAgents/
  launchctl bootout gui/$(id -u)/dev.fjorn.$l 2>/dev/null
  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/dev.fjorn.$l.plist
done
tailscale serve --bg --tcp=443 tcp://127.0.0.1:8443
```

Logs are in `~/Library/Logs/dev-proxy/`. Caddy keeps its certificate in `~/.local/share/caddy-dev-proxy`.

If lil-homie gets a new tailnet IP, run `dns.py --apply` again.
