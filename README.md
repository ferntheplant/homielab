# lil homie

A mac mini home server sitting in my closet.

## Layout

```
Caddyfile                      reverse proxy routes (caddy stack)
glance.Dockerfile              glance + tzdata (built by the homie stack)
glance-config/                 dashboard config, mounted into glance
launchctl-agent-monitor.py     tiny HTTP server: status of host-side agents, for the dashboard
launchd/                       launchd agents for this repo (copy into ~/Library/LaunchAgents)
scripts/                       snapshot + firewall helpers
stacks/<name>/                 one docker compose project per stack (+ .env.tpl: 1Password refs)
stacks/manage.sh               start/stop/update everything
data/                          all service data ($DATA_PATH, gitignored)
```

## General

- everything runs on docker compose via OrbStack
- all data for all services lives in `$DATA_PATH` (`data/`)
- secrets live in the 1Password `lil-homie` vault (see [Secrets](#secrets)); `.env.shared` holds only `DATA_PATH`
- stacks are independent compose projects, all joined by the shared `caddy_network`
- every published port binds to `127.0.0.1`; the only way in is Tailscale Serve (tailnet only). The one exception is Syncthing's sync port `22000` (see [Syncthing](#syncthing))

## Stacks + Manage Script

```bash
./stacks/manage.sh start            # all stacks, then apply Tailscale Serve
./stacks/manage.sh stop
./stacks/manage.sh restart
./stacks/manage.sh status
./stacks/manage.sh logs --follow
./stacks/manage.sh update           # snapshot data, pull + rebuild images, recreate, prune
./stacks/manage.sh serve            # re-apply Tailscale Serve config
./stacks/manage.sh restart homie    # any command can target one stack
```

Stacks:

- `caddy`: reverse proxy, owns `caddy_network`
- `homie`: glance dashboard + dozzle (logs), beszel (metrics), syncthing, backrest, glance helpers, and a read-only
  Docker socket proxy (dozzle and glance read the Docker API through it; nothing else mounts the socket)
- `karakeep`: bookmarker + meilisearch + headless chrome
- `beeper`: bridge-manager containers for Discord, Signal, Facebook

## Secrets

Secrets live in the 1Password `lil-homie` vault, one Secure Note per stack (`beeper`, `homie`, `karakeep`), one
field per variable. Each `stacks/<stack>/.env.tpl` maps variable names to `op://lil-homie/<stack>/<VAR>` refs and is
safe to commit. `manage.sh` wraps every compose call in `op run --env-file .env.tpl`, and each service in the compose
files lists only the variables it needs (`VAR: ${VAR}`).

`op` authenticates with a read-only service account (`lil-homie-server`). Its token is in the login keychain
(service `lil-homie-op`). launchd jobs read it without a prompt. Over SSH the keychain is locked, so `manage.sh` asks
for the login password once.

To add or change a secret:

1. Edit the stack's item in 1Password (add a field named after the variable).
2. Add `VAR=op://lil-homie/<stack>/VAR` to `stacks/<stack>/.env.tpl`.
3. Add `VAR: ${VAR}` to the service that needs it.
4. Run `./stacks/manage.sh start <stack>`.

Running containers keep their environment, so a reboot or a 1Password outage does not take anything down. Only
`manage.sh` needs 1Password.

## Updates

There is no Watchtower. `launchd/dev.fjorn.lil-homie-update.plist` runs `./stacks/manage.sh update` every
Sunday at 04:30. It takes a data snapshot first, then pulls every image, rebuilds glance on a fresh
`glanceapp/glance` base, recreates changed containers, and prunes old images.

Images with on-disk formats that need a manual migration stay pinned (`getmeili/meilisearch:v1.13.3`,
`zenika/alpine-chrome:124`, the Chrome version Karakeep documents). Bump those by hand.

Log: `~/Library/Logs/lil-homie/update.log`

## Backups

Copying live SQLite files while an app writes to them can give a backup that will not restore. So:

1. `scripts/snapshot-data.sh` runs nightly at 03:30 (`launchd/dev.fjorn.lil-homie-snapshot.plist`). It stops the
   containers that hold databases (~10 seconds), APFS-clones their folders into `data/_snapshot`, and starts them again.
   The clone is copy-on-write, so it is near-instant and costs almost no disk space.
2. Backrest backs up `/userdata/_snapshot` (= `data/_snapshot`) to Cloudflare R2, scheduled after 03:30.

Edit the `FOLDERS` list in the script to change what gets backed up. `karakeep-meilisearch` is left out on purpose:
Karakeep rebuilds the search index (Admin → Background jobs → reindex).

Log: `~/Library/Logs/lil-homie/snapshot.log`

## Host-side services (launchd)

Install or reload an agent from `launchd/`:

```bash
cp launchd/<label>.plist ~/Library/LaunchAgents/
launchctl bootout gui/$(id -u)/<label> 2>/dev/null; launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/<label>.plist
```

- `dev.fjorn.launchctl-agent-monitor`: serves `http://127.0.0.1:8765/status` for the dashboard's Launchctl widget
- `dev.fjorn.lil-homie-snapshot`: nightly data snapshot
- `dev.fjorn.lil-homie-update`: weekly image update
- `dev.fjorn.bbctl-imessage`: Beeper iMessage bridge (see below)
- `homebrew.mxcl.beszel-agent`: Beszel host agent (`brew services`)
- `dev.fjorn.pf-syncthing` (LaunchDaemon, root): Syncthing firewall rule, see [Syncthing](#syncthing)

Ollama runs from `Ollama.app` (it self-updates). Karakeep uses it for tagging and summaries via `OLLAMA_BASE_URL`.

## Tailscale

All access is via Tailscale. The host is `lil-homie.tail8cc0d3.ts.net`. Serve publishes to the tailnet only:

- `https://lil-homie.tail8cc0d3.ts.net/` → `127.0.0.1:8080` (Caddy: dashboard, `/logs`, `/beszel`, `/syncthing`, `/backrest`)
- `https://lil-homie.tail8cc0d3.ts.net:3003/` → `127.0.0.1:3000` (Karakeep, needs its own origin for the app/extension)

`./stacks/manage.sh serve` applies this (the mapping lives at the top of `manage.sh`). If
`tailscale serve status` shows `No serve config`, run it once interactively to get the one-time consent flow.

If MagicDNS doesn't resolve on a CLI-only client, add a resolver file:

```bash
sudo mkdir -p /etc/resolver
echo "nameserver 100.100.100.100" | sudo tee /etc/resolver/tail8cc0d3.ts.net
sudo dscacheutil -flushcache
sudo killall -HUP mDNSResponder
```

## SSH

Using a classic SSH pub/priv key setup to SSH into the box over the tailnet. I had to disable async shell rendering
(atuin, zsh-autosuggestions, fzf-tab) to get the SSH experience to not suck ass.

## Caddy

Caddy routes subpaths to containers on `caddy_network`. In the Caddyfile, `handle_path` strips the base path before
proxying (for apps that can't be configured with one) and `handle` forwards the path as-is (for apps configured with a
base path, like dozzle's `DOZZLE_BASE=/logs`).

## Syncthing

I use Syncthing to keep a handful of folders in sync across my devices.

### Tailnet-only setup

OrbStack can't bind container ports directly to the Tailscale IP, so Syncthing's data port (`22000`) is restricted at
the macOS firewall (pf) to the Tailscale range `100.64.0.0/10`.

1) Install the `dev.fjorn.pf-syncthing` LaunchDaemon. It applies the pf rule at boot (pf is not enabled by default) and
   again whenever a macOS update rewrites `/etc/pf.conf`. Re-run the install after editing the script; the daemon runs
   a root-owned copy in `/usr/local/libexec/lil-homie/`. Log: `/var/log/lil-homie-pf.log`

```bash
sudo ~/lil-homie/scripts/install-pf-daemon.sh
```

2) Configure Syncthing listen addresses (each device listens on its own tailnet IP)

- `tcp://<own_tailnet_ip>:22000`
- `quic://<own_tailnet_ip>:22000`

3) Configure device addresses (each device points at the other's tailnet IP): `tcp://<other_tailnet_ip>:22000`

Optional (strict tailnet-only): disable Global Discovery and Relays in `Settings → Connections`.

## Backrest

[Backrest](https://github.com/garethgeorge/backrest) is a UI over restic. Backups are encrypted and stored in a
Cloudflare R2 bucket. See [Backups](#backups) for what gets backed up.

## Karakeep

Served on a dedicated Tailscale Serve port for mobile/extension compatibility: `https://lil-homie.tail8cc0d3.ts.net:3003/`

## Beeper

Beeper provides a unified messaging experience. The beeper apps aren't that good but I don't want to configure Matrix
from scratch so they'll have to suffice. Their docs are pretty good.

### iMessage

To get iMessage to work I needed to grant the `bbctl` program full disk access so it could mess with the OS system
iMessage stuff. This is the only bridge that isn't running in a container because it needs access to the host. It runs
as the `dev.fjorn.bbctl-imessage` launchd agent.

## Glance

Glance is the server dashboard. Complex widgets have their own config file in `glance-config/widget-config`. To get the
Glance server to use my timezone, `glance.Dockerfile` extends the default image with tzdata. The homie stack builds it;
`./stacks/manage.sh update` rebuilds it on a fresh base.

Widgets:

- [Karakeep](https://github.com/glanceapp/community-widgets/blob/main/widgets/karakeep-dashboard/README.md): calls `karakeep-web:3000` directly over `caddy_network` (tailnet names do not resolve inside Docker)
- Services: Glance's built-in `monitor` widget, checking each service over `caddy_network`
- [Beszel](https://github.com/glanceapp/community-widgets/blob/main/widgets/beszel-metrics/README.md): proxied by Caddy
  behind `/beszel`, so it needs `APP_URL` and `handle_path`. TODO: find a way to notify when the API token is expired
- [Syncthing](https://github.com/glanceapp/community-widgets/blob/main/widgets/syncthing/README.md): hard coded values swapped for env variables
- [Backrest](https://github.com/not-first/restic-glance-extension): for a remote repo you only need the env variables.
  With no snapshots the widget shows weird errors, so make sure at least one snapshot exists
- `launchctl` agents: reads `launchctl-agent-monitor.py` at `host.docker.internal:8765`. Services are listed at the
  top of the script; an entry with a `url` is checked over HTTP instead of `launchctl`
- [gcal](https://github.com/AWildLeon/Glance-iCal-Events): uses `parseTimeLocal` for local timestamps
- [AQI](https://github.com/glanceapp/community-widgets/blob/main/widgets/air-quality/README.md) and
  [Weather](https://github.com/glanceapp/community-widgets/blob/main/widgets/weather-seven-day/README.md): default instructions

## Recovery

Quick debug loop for broken containers:

```bash
./stacks/manage.sh status
./stacks/manage.sh logs homie
```

Restore a folder from the local snapshot (stop its container first):

```bash
docker stop karakeep-web
mv data/karakeep data/karakeep.broken
cp -cR data/_snapshot/karakeep data/karakeep
docker start karakeep-web
```

### Backrest oplog corruption

If Backrest is crashing with an `oplog` migration error, it is safe to move the oplog sqlite file out of the way and
restart:

```bash
mv $DATA_PATH/backrest/data/oplog.sqlite \
  $DATA_PATH/backrest/data/oplog.sqlite.bak-YYYYMMDD-HHMMSS
./stacks/manage.sh restart homie
```

## Archives

- `data/langfuse-archive/`: agent traces from the removed Langfuse stack (May–Sep 2026), as Parquet + a Postgres dump.
  See its README.

## MacOS Settings

TODO I know I did something for setting it up so I could unlock it after a restart via ssh. I forgot everything else I did. I am running macOS 26.
