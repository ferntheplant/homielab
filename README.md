# lil homie

My home server setup, on two boxes:

- **micro-homie**: a little Intel micro PC (i5-6500, 8 GB RAM, 256 GB SATA SSD) with Debian 13. It runs every
  container stack. Most of this repo is for it.
- **lil-homie**: the Mac mini in my closet, now mostly a devbox. It keeps only the things that need a Mac or its GPU:
  Ollama, the Beeper iMessage bridge, a Beszel agent, and a small status server for the dashboard. See
  [lil-homie (the Mac)](#lil-homie-the-mac).

## Layout

```
Caddyfile                      reverse proxy routes (caddy stack)
glance.Dockerfile              glance + tzdata (built by the homie stack)
glance-config/                 dashboard config, mounted into glance
scripts/                       snapshot, classifier, and micro-homie setup scripts
stacks/<name>/                 one docker compose project per stack (+ .env.tpl: 1Password refs)
stacks/manage.sh               start/stop/update everything
systemd/                       user timers for micro-homie (snapshot, update, classifier)
hosts/lil-homie/               launchd agents and scripts for the Mac
data/                          all service data ($DATA_PATH, gitignored)
```

## General

- everything runs on Docker Engine + compose on micro-homie
- all data for all services lives in `$DATA_PATH` (`data/`)
- secrets live in the 1Password `lil-homie` vault (see [Secrets](#secrets)). `.env.shared` holds the settings that
  are not secret: `DATA_PATH`, the tailnet names, and the Ollama URL
- stacks are independent compose projects, all joined by the shared `caddy_network`
- every published port binds to `127.0.0.1`; the only way in is Tailscale Serve (tailnet only). The one exception is
  Syncthing's sync port `22000`, which binds to the tailnet IP (see [Syncthing](#syncthing))

## micro-homie setup

From a fresh Debian 13 install with Tailscale on it:

```bash
git clone https://github.com/ferntheplant/lil-homie.git ~/lil-homie
sudo ~/lil-homie/scripts/setup-micro-homie.sh     # Docker, 1Password CLI, git/rsync, linger, sysctl, timezone
# log out and back in to get the docker group, then:
umask 077; mkdir -p ~/.config/lil-homie
cat > ~/.config/lil-homie/op-token                # paste the service account token (see Secrets), then Ctrl-D
~/lil-homie/stacks/manage.sh start
~/lil-homie/scripts/install-systemd-units.sh
```

`setup-micro-homie.sh` also sets `net.ipv4.ip_nonlocal_bind=1`. Docker binds Syncthing to the tailnet IP, and at boot
Docker can start before `tailscaled` has that IP. The disk is not encrypted, so the box boots without a person at the
keyboard. In the BIOS, set it to power on after a power loss.

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
- `homie`: glance dashboard + dozzle (logs), beszel (metrics) + its agent for this box, syncthing, backrest, glance
  helpers, and a read-only Docker socket proxy (dozzle and glance read the Docker API through it)
- `karakeep`: bookmarker + meilisearch + headless chrome
- `beeper`: bridge-manager containers for Discord, Signal, Facebook

Two containers mount the Docker socket directly: `socket-proxy`, and `beszel-agent` for container stats. The agent
uses the host network, so it cannot reach the proxy's internal network.

## Secrets

Secrets live in the 1Password `lil-homie` vault, one Secure Note per stack (`beeper`, `homie`, `karakeep`), one
field per variable. Each `stacks/<stack>/.env.tpl` maps variable names to `op://lil-homie/<stack>/<VAR>` refs and is
safe to commit. `manage.sh` wraps every compose call in `op run --env-file .env.tpl`, and each service in the compose
files lists only the variables it needs (`VAR: ${VAR}`).

`op` authenticates with a read-only service account (`lil-homie-server`). Its token is in
`~/.config/lil-homie/op-token` (mode 0600) on micro-homie. `manage.sh` and the classifier read it from there.

To add or change a secret:

1. Edit the stack's item in 1Password (add a field named after the variable). The service account is read-only, so
   use the 1Password app or your own `op` account, e.g. `op item edit karakeep --vault lil-homie 'VAR[text]=value'`.
2. Add `VAR=op://lil-homie/<stack>/VAR` to `stacks/<stack>/.env.tpl`.
3. Add `VAR: ${VAR}` to the service that needs it.
4. Run `./stacks/manage.sh start <stack>`.

Running containers keep their environment, so a reboot or a 1Password outage does not take anything down. Only
`manage.sh` needs 1Password.

## Timers (systemd)

Scheduled jobs are systemd **user** units in `systemd/`. Install or update them with
`scripts/install-systemd-units.sh`. They run without a login because linger is on for `fjorn`.

| Timer | When | What |
| --- | --- | --- |
| `homie-snapshot.timer` | daily 03:30 | `scripts/snapshot-data.sh`, see [Backups](#backups) |
| `homie-update.timer` | Sunday 04:30 | `stacks/manage.sh update`, see [Updates](#updates) |
| `karakeep-classify.timer` | every 5 min | `scripts/karakeep-classify.py`, see [Karakeep](#tool--reading-classifier) |

```bash
systemctl --user list-timers
journalctl --user -u homie-update.service      # logs
systemctl --user start homie-snapshot.service  # run one now
```

## Updates

There is no Watchtower. `homie-update.timer` runs `./stacks/manage.sh update` every Sunday at 04:30. It takes a data
snapshot first, then pulls every image, rebuilds glance on a fresh `glanceapp/glance` base, recreates changed
containers, and prunes old images. The unit stops the job after 2 hours, so a stuck pull or `op` call does not block
the next week.

Images with on-disk formats that need a manual migration stay pinned (`getmeili/meilisearch:v1.13.3`,
`zenika/alpine-chrome:124`, the Chrome version Karakeep documents). Bump those by hand.

Debian packages (Docker, Tailscale, 1Password CLI) come from apt. `unattended-upgrades` installs Debian security
updates only; run `sudo apt update && sudo apt upgrade` for the rest.

## Backups

Copying live SQLite files while an app writes to them can give a backup that will not restore. So:

1. `scripts/snapshot-data.sh` runs nightly at 03:30 (`homie-snapshot.timer`). It stops the containers that hold
   databases (~10-20 seconds), copies their folders into `data/_snapshot`, and starts them again.
2. Backrest backs up `/userdata/_snapshot` (= `data/_snapshot`) to Cloudflare R2, scheduled at 04:00.

Edit the `FOLDERS` list in the script to change what gets backed up. `karakeep-meilisearch` is left out on purpose:
Karakeep rebuilds the search index (Admin → Background jobs → reindex).

## Tailscale

All access is via Tailscale. The server is `micro-homie.tail8cc0d3.ts.net`. Serve publishes to the tailnet only:

- `https://micro-homie.tail8cc0d3.ts.net/` → `127.0.0.1:8080` (Caddy: dashboard, `/logs`, `/beszel`, `/syncthing`, `/backrest`)
- `https://micro-homie.tail8cc0d3.ts.net:3003/` → `127.0.0.1:3000` (Karakeep, needs its own origin for the app/extension)

`./stacks/manage.sh serve` applies this (the mapping lives at the top of `manage.sh`). `setup-micro-homie.sh` makes
`fjorn` the Tailscale operator, so `tailscale serve` works without sudo. If `tailscale serve status` shows
`No serve config`, run it once interactively to get the one-time consent flow.

`.env.shared` holds the tailnet names (`TS_HOST`, `LIL_HOMIE_HOST`). The compose files and the Glance config read
them, so a rename is a one-line change.

## SSH

Using a classic SSH pub/priv key setup to SSH into the boxes over the tailnet. On lil-homie I had to disable async
shell rendering (atuin, zsh-autosuggestions, fzf-tab) to get the SSH experience to not suck ass.

## Caddy

Caddy routes subpaths to containers on `caddy_network`. In the Caddyfile, `handle_path` strips the base path before
proxying (for apps that can't be configured with one) and `handle` forwards the path as-is (for apps configured with a
base path, like dozzle's `DOZZLE_BASE=/logs`).

## Syncthing

I use Syncthing to keep a handful of folders in sync across my devices.

### Tailnet-only setup

The sync port (`22000`, TCP and UDP) binds to micro-homie's tailnet IP (`TS_IP` in `.env.shared`), so nothing on the
LAN can reach it. The GUI binds to `127.0.0.1` and is only reachable through Caddy at `/syncthing`.

The other devices point at micro-homie's tailnet IP: `tcp://100.80.5.26:22000`.

Optional (strict tailnet-only): disable Global Discovery and Relays in `Settings → Connections`.

## Backrest

[Backrest](https://github.com/garethgeorge/backrest) is a UI over restic. Backups are encrypted and stored in a
Cloudflare R2 bucket. See [Backups](#backups) for what gets backed up. The Backrest instance ID is still `lil-homie`
(it cannot change), so the snapshot history continues from the Mac.

## Karakeep

Served on a dedicated Tailscale Serve port for mobile/extension compatibility: `https://micro-homie.tail8cc0d3.ts.net:3003/`

### AI tagging (Ollama)

Ollama runs on lil-homie (the Mac GPU), see [lil-homie (the Mac)](#lil-homie-the-mac). Karakeep and the classifier
reach it at `OLLAMA_URL=http://lil-homie:11434`. Use the short name: Ollama answers `403` when the Host header is a
name it does not know, and it rejects `lil-homie.tail8cc0d3.ts.net`.

Karakeep summarizes with `qwen3.5:4b-16k` and embeds with `nomic-embed-text-v2-moe`. Its own tagging is off, see
[Tool / reading classifier](#tool--reading-classifier). Two non-obvious choices:

1. **Karakeep talks to Ollama through its OpenAI-compatible API** (`OPENAI_BASE_URL=<ollama>/v1`, a dummy
   `OPENAI_API_KEY`), not `OLLAMA_BASE_URL`. qwen3.5 thinks by default, which makes a tagging call ~60-110s instead of
   ~2s and can use up the 2048-token output budget (empty response). Karakeep can only turn thinking off through
   `OPENAI_REASONING_EFFORT=none`, which its Ollama client does not send.
2. **`qwen3.5:4b-16k` is a local alias** with `num_ctx 16384`. The OpenAI API has no context-length field, so without
   it Ollama loads the model at 4096 tokens and cuts long articles. Keep it equal to `INFERENCE_CONTEXT_LENGTH`.
   Recreate it on lil-homie after pulling a new `qwen3.5:4b`:

```bash
printf "FROM qwen3.5:4b\nPARAMETER num_ctx 16384\n" > /tmp/Modelfile
ollama create qwen3.5:4b-16k -f /tmp/Modelfile
```

### Semantic search

`EMBEDDING_ENABLE_AUTO_INDEXING=true` and `EMBEDDING_DIMENSIONS=768` (the size `nomic-embed-text-v2-moe` returns)
turn on the `semantic` and `hybrid` search modes. Karakeep only turns embeddings on by itself for the default OpenAI
setup. Hybrid search finds a bookmark from a vague description, for example "self-hosted tool for bookmarking sites"
finds Hister, whose page never says "bookmark". If the embedding model changes, change the dimension to match and
regenerate the embeddings (Admin → Background jobs).

### Tool / reading classifier

Karakeep's own auto-tagging is **off** for my account (Settings → AI). With a 4B model it ignores curated tags and
the custom prompt: it invents 4-5 tags per bookmark, so near-duplicate tags pile up (`AI Agents`, `AI Agent`,
`agent systems`, ...). Instead, `scripts/karakeep-classify.py` (`karakeep-classify.timer`, every 5 min)
asks the same model one question with two answers and attaches `tool` or `reading` through the API. It skips
bookmarks that already have one of those tags and links the crawler has not fetched yet. About 1.5 s per bookmark.

Smart lists built on the tags: **Tools** (`#tool`), **Reading** (`#reading`), **Read later** (`#reading -is:archived`,
archive a bookmark when it is read). To fix a wrong kind, swap the tag by hand; the worker never changes a tag.

```bash
python3 scripts/karakeep-classify.py --dry-run --limit 10   # classify, print, write nothing
journalctl --user -u karakeep-classify.service              # log
```

## Beeper

Beeper provides a unified messaging experience. The beeper apps aren't that good but I don't want to configure Matrix
from scratch so they'll have to suffice. Their docs are pretty good.

Each bridge-manager container downloads its bridge program into `data/beeper-*/binaries/` for the CPU it runs on.
If you move the data to a machine with a different CPU type (for example ARM to Intel), delete `binaries/` first.
The logins are in `db/` and `config.yaml`.

Never run the same bridge on two machines at once.

### iMessage

The iMessage bridge runs on lil-homie, see below.

## Glance

Glance is the server dashboard. Complex widgets have their own config file in `glance-config/widget-config`. To get the
Glance server to use my timezone, `glance.Dockerfile` extends the default image with tzdata. The homie stack builds it;
`./stacks/manage.sh update` rebuilds it on a fresh base. Links in the config use `${TS_HOST}` from `.env.shared`.

Widgets:

- [Karakeep](https://github.com/glanceapp/community-widgets/blob/main/widgets/karakeep-dashboard/README.md): calls `karakeep-web:3000` directly over `caddy_network`
- Services: Glance's built-in `monitor` widget, checking each service over `caddy_network`
- [Beszel](https://github.com/glanceapp/community-widgets/blob/main/widgets/beszel-metrics/README.md): proxied by Caddy
  behind `/beszel`, so it needs `APP_URL` and `handle_path`. TODO: find a way to notify when the API token is expired
- [Syncthing](https://github.com/glanceapp/community-widgets/blob/main/widgets/syncthing/README.md): hard coded values swapped for env variables
- [Backrest](https://github.com/not-first/restic-glance-extension): for a remote repo you only need the env variables.
  With no snapshots the widget shows weird errors, so make sure at least one snapshot exists
- lil-homie agents: reads `launchctl-agent-monitor.py` on the Mac at `https://${LIL_HOMIE_HOST}:8765/status`.
  Services are listed at the top of the script; an entry with a `url` is checked over HTTP instead of `launchctl`
- [gcal](https://github.com/AWildLeon/Glance-iCal-Events): uses `parseTimeLocal` for local timestamps
- [AQI](https://github.com/glanceapp/community-widgets/blob/main/widgets/air-quality/README.md) and
  [Weather](https://github.com/glanceapp/community-widgets/blob/main/widgets/weather-seven-day/README.md): default instructions

## Beszel

The hub runs in the homie stack. Each machine has an agent that connects to the hub over WebSocket (`HUB_URL` +
`TOKEN`). The agent env file is `~/.config/beszel/beszel-agent.env` on each machine and holds `KEY` (the hub public
key) and `TOKEN` (from "Add system" in the hub).

- micro-homie: the `beszel-agent` container, `HUB_URL=http://127.0.0.1:8090`
- lil-homie: the `dev.fjorn.beszel-agent` launchd agent, `HUB_URL=https://micro-homie.tail8cc0d3.ts.net/beszel`

## lil-homie (the Mac)

Files in `hosts/lil-homie/`. The Mac keeps:

- **Ollama** (`Ollama.app`, it self-updates), for Karakeep and the classifier
- **Beeper iMessage bridge** (`dev.fjorn.bbctl-imessage`). `bbctl` needs Full Disk Access to read the iMessage
  database, so it cannot run in a container or on Linux
- **Beszel agent** (`dev.fjorn.beszel-agent`, `beszel-agent.sh` loads `~/.config/beszel/beszel-agent.env`)
- **Status server** (`dev.fjorn.launchctl-agent-monitor`) for the dashboard's lil-homie agents widget
- **Tailscale** (`dev.fjorn.tailscaled` LaunchDaemon)

Tailscale Serve on the Mac publishes two ports to the tailnet. Apply them once (Serve remembers them):

```bash
tailscale serve --bg --tcp=11434 tcp://127.0.0.1:11434    # Ollama (TCP: see AI tagging for the Host header)
tailscale serve --bg --https=8765 http://127.0.0.1:8765   # status server
```

Install or reload an agent:

```bash
cp hosts/lil-homie/launchd/<label>.plist ~/Library/LaunchAgents/
launchctl bootout gui/$(id -u)/<label> 2>/dev/null; launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/<label>.plist
```

Packages come from nanobrew. Shell tools come from the dotfiles core list (`~/dotfiles/.Nanobrewfile`, server
profile); `hosts/lil-homie/Nanobrewfile` adds the rest. Ollama is not in a package manager, it updates itself.

```bash
nb bundle install ~/dotfiles/.Nanobrewfile
nb bundle install ~/lil-homie/hosts/lil-homie/Nanobrewfile
```

`tailscaled` runs as the `dev.fjorn.tailscaled` LaunchDaemon from a root-owned copy of the nanobrew binary. After
`nb upgrade tailscale`, install the new binary (the tailnet drops for a few seconds; the script logs to
`/var/log/lil-homie-tailscaled-install.log`):

```bash
sudo ~/lil-homie/hosts/lil-homie/install-tailscaled-daemon.sh
```

If MagicDNS doesn't resolve on a CLI-only client, add a resolver file:

```bash
sudo mkdir -p /etc/resolver
echo "nameserver 100.100.100.100" | sudo tee /etc/resolver/tail8cc0d3.ts.net
sudo dscacheutil -flushcache
sudo killall -HUP mDNSResponder
```

TODO I know I did something for setting it up so I could unlock it after a restart via ssh. I forgot everything else
I did. I am running macOS 26.

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
cp -a data/_snapshot/karakeep data/karakeep
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
