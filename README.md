# lil homie

My home server setup, on two boxes:

- **micro-homie**: a little Intel micro PC (i5-6500, 8 GB RAM, 256 GB SATA SSD) with Debian 13. It runs every
  container stack. Most of this repo is for it.
- **lil-homie**: the Mac mini in my closet, now mostly a devbox. It keeps only the things that need a Mac or its GPU:
  llama-server (the local LLM), the Beeper iMessage bridge, a Beszel agent, and a small status server for the
  dashboard. See
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
hosts/micro-homie/storage/     btrbk config and scrub timer for the 8 TB drive (see Storage drive)
data/                          all service data ($DATA_PATH, gitignored)
```

## General

- everything runs on Docker Engine + compose on micro-homie
- all data for all services lives in `$DATA_PATH` (`data/`)
- secrets live in the 1Password `lil-homie` vault (see [Secrets](#secrets)). `.env.shared` holds the settings that
  are not secret: `DATA_PATH`, the tailnet names, and the llama-server URLs
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

## Storage drive

micro-homie has an 8 TB USB hard drive (Toshiba HDWF180, serial 697SK0FZFB6G) for bulk data. The service data stays
on the SSD. The drive holds:

| Subvolume | Use | Snapshots |
| --- | --- | --- |
| `/srv/storage/archive` | file and media archive (`old-drive-archive`, the folders kept from the drive's old data) | daily |
| `/srv/storage/sync` | large Syncthing folders | daily |
| `/srv/storage/backup` | the local restic repo for Backrest | none, restic keeps its own history |
| `/srv/storage/.snapshots` | btrbk snapshots, read-only | - |

The filesystem is btrfs on one GPT partition, with the label `storage`. btrfs checks a checksum on each read, so it
finds corrupt data. On one disk it keeps two copies of the metadata but one copy of the data, so it cannot repair
bad data. The repair source is the other copy: R2 for backups, or the other Syncthing devices.

- **Mount:** fstab mounts the drive by UUID at `/srv/storage` with `noatime,compress=zstd:1,nofail,x-systemd.device-timeout=30s`.
  The box boots without the drive and waits for it for a maximum of 30 s.
- **Mount point guard:** the empty `/srv/storage` folder on the SSD has the immutable flag (`chattr +i`). When the
  drive is absent, nothing can write there, so a container gets an error instead of filling the SSD.
- **Containers:** Syncthing and Backrest mount `/srv/storage` at `/storage` with `rslave`, so a late mount still
  shows up in a running container. Docker does not wait for the drive.
- **Snapshots:** `btrbk.timer` (Debian's, daily) uses `hosts/micro-homie/storage/btrbk.conf`. It keeps 14 daily,
  8 weekly, and 6 monthly snapshots. To restore a file, copy it out of `/srv/storage/.snapshots/<subvolume>.<timestamp>/`.
- **Scrub:** `btrfs-scrub-storage.timer` reads every block on the 1st of each month at 05:00 and checks it.
- **Spin-down:** not set. The drive uses its own defaults.

`sudo ~/lil-homie/scripts/setup-storage.sh` does the host setup: packages, mount point, fstab, mount, subvolumes,
btrbk, and the scrub timer. It does not wipe or format the drive. On a new drive, do those steps by hand first.
Always use the by-id path, because `/dev/sdX` letters can change:

```bash
D=/dev/disk/by-id/ata-TOSHIBA_HDWF180_697SK0FZFB6G
lsblk -o NAME,SIZE,MODEL,SERIAL "$D"     # make sure that this is the right drive
sudo wipefs --all "$D"-part*             # old filesystem signatures, if there are partitions
sudo wipefs --all "$D"                   # old partition table
printf 'label: gpt\nname=storage, type=linux\n' | sudo sfdisk --wipe always "$D"
sudo udevadm settle
sudo mkfs.btrfs -L storage "$D-part1"
```

Check the drive:

```bash
sudo btrfs scrub status /srv/storage
sudo btrfs device stats /srv/storage      # error counters, all 0 is good
sudo smartctl -H -A /dev/disk/by-id/ata-TOSHIBA_HDWF180_697SK0FZFB6G
sudo btrbk list snapshots
```

The enclosure uses an ASMedia ASM235CM USB bridge (`174c:55aa`). `lsusb -t` must show `Driver=uas, 5000M`. If the
drive disconnects under load and `sudo dmesg` shows `uas_eh` or `reset` lines, add the kernel option
`usb-storage.quirks=174c:55aa:u` to turn off UAS for this bridge. After a disconnect, btrfs goes read-only: fix the
cause, then `sudo umount /srv/storage && sudo mount /srv/storage`.

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

## Rex

Rex runs on micro-homie as the **login server** (`rex-login-server.service`), not the package's default shared
server. It identifies callers by their Tailscale login and runs their terminals as `fjorn`. Tailscale Serve publishes
it on port 7297, because 443 is the dashboard. `scripts/setup-rex-login-server.sh` (run with sudo) sets this up.

In Rex for Mac, choose **Add Remote Host…** and enter the full address with the port. A bare hostname makes Rex try
443 first:

```
https://micro-homie.tail8cc0d3.ts.net:7297
```

The accounts file is `/etc/rex/accounts.jsonc` and needs `"version": 1`. Its server log is
`sudo journalctl -u rex-login-server`.

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

### AI tagging (llama-server)

llama-server runs Qwen3.5-4B on lil-homie (the Mac GPU), see
[`hosts/lil-homie/llm/README.md`](hosts/lil-homie/llm/README.md). Karakeep and the classifier reach it over Tailscale
Serve:

- chat: `LLM_URL=http://lil-homie:8080` (Karakeep `OPENAI_BASE_URL`, with a dummy `OPENAI_API_KEY`)
- embeddings: `LLM_EMBED_URL=http://lil-homie:8081` (Karakeep `EMBEDDING_OPENAI_BASE_URL`), a small proxy that cuts
  input to the embedding model's 512 tokens, as Ollama did. llama-server itself rejects longer input.

Karakeep summarizes with `qwen3.5:4b-16k` and embeds with `nomic-embed-text-v2-moe:latest`. These are the old Ollama
names, kept as aliases in `models.ini`. Its own tagging is off, see
[Tool / reading classifier](#tool--reading-classifier). Thinking is off on the server, because it makes a tagging call
~60-110s instead of ~2s and can use up the output budget; `OPENAI_REASONING_EFFORT=none` also asks for that. Each
request gets 16384 tokens of context (`INFERENCE_CONTEXT_LENGTH`); keep the two equal.

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

Forgot the login password? There is no email setup, so reset it on micro-homie. The script asks for the new
password and sets it on the PocketBase superuser and on the Beszel user (same email):

```bash
~/lil-homie/scripts/beszel-reset-password.sh
```

## lil-homie (the Mac)

Files in `hosts/lil-homie/`. The Mac keeps:

- **llama-server** (`dev.fjorn.llama-server`) with Qwen3.5-4B and the embedding model, for Karakeep, the
  classifier, and AnyJev decisions (`dev.fjorn.anyjev-decide`, `127.0.0.1:8766`). See
  [`hosts/lil-homie/llm/README.md`](hosts/lil-homie/llm/README.md). Ollama.app is still installed but stopped,
  for a rollback
- **Beeper iMessage bridge** (`dev.fjorn.bbctl-imessage`). `bbctl` needs Full Disk Access to read the iMessage
  database, so it cannot run in a container or on Linux
- **Beszel agent** (`dev.fjorn.beszel-agent`, `beszel-agent.sh` loads `~/.config/beszel/beszel-agent.env`)
- **Status server** (`dev.fjorn.launchctl-agent-monitor`) for the dashboard's lil-homie agents widget
- **Tailscale** (`dev.fjorn.tailscaled` LaunchDaemon)

Tailscale Serve on the Mac publishes these ports to the tailnet. Apply them once (Serve remembers them):

```bash
tailscale serve --bg --tcp=8080 tcp://127.0.0.1:8080      # llama-server
tailscale serve --bg --tcp=8081 tcp://127.0.0.1:8081      # embeddings proxy
tailscale serve --bg --https=8765 http://127.0.0.1:8765   # status server
```

The old Ollama forward (`--tcp=11434`) stays until the Ollama rollback window is over.

Install or reload an agent:

```bash
cp hosts/lil-homie/launchd/<label>.plist ~/Library/LaunchAgents/
launchctl bootout gui/$(id -u)/<label> 2>/dev/null; launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/<label>.plist
```

Packages come from nanobrew. Shell tools come from the dotfiles core list (`~/dotfiles/.Nanobrewfile`, server
profile); `hosts/lil-homie/Nanobrewfile` adds the rest, including `llama.cpp`.

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
