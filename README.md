# homielab

My home server setup, on two boxes. The repo was called `lil-homie` until 2026-11. The Mac is still called
lil-homie. The checkout is `~/homielab` on both boxes.

- **micro-homie**: a little Intel micro PC (i5-6500, 8 GB RAM, 256 GB SATA SSD) with Debian 13. It runs every
  container stack. Most of this repo is for it.
- **lil-homie**: the Mac mini in my closet, now mostly a devbox. It keeps only the things that need a Mac or its GPU:
  llama-server (the local LLM), the Beeper iMessage bridge, a Beszel agent, and a small status server for the
  dashboard. See
  [lil-homie (the Mac)](#lil-homie-the-mac).
- **gaming-homie**: the Windows 11 gaming PC. It runs only a Beszel agent (see [Beszel](#beszel)). A Comet KVM
  controls it when it is off or frozen (see [Comet (KVM)](#comet-kvm)).

## Layout

```
Caddyfile                      reverse proxy routes (caddy stack)
glance.Dockerfile              glance + tzdata (built by the homie stack)
glance-config/                 dashboard config, mounted into glance
scripts/                       snapshot, classifier, GitHub mirror, and micro-homie setup scripts
stacks/<name>/                 one docker compose project per stack (+ .env.tpl: 1Password refs)
stacks/manage.sh               start/stop/update everything
systemd/                       user timers for micro-homie (snapshot, update, classifier)
tailscale/                     tailnet policy, Services, and device tags (see Tailnet config in code)
hosts/lil-homie/               launchd agents and scripts for the Mac
hosts/micro-homie/storage/     btrbk config and scrub timer for the 8 TB drive (see Storage drive)
data/                          all service data ($DATA_PATH, gitignored)
.agents/skills/homielab/       the homielab skill for agents (see Agents)
```

## General

- everything runs on Docker Engine + compose on micro-homie
- all data for all services lives in `$DATA_PATH` (`data/`)
- secrets live in the 1Password `homielab` vault (see [Secrets](#secrets)). `.env.shared` holds the settings that
  are not secret: `DATA_PATH`, the tailnet names, and the llama-server URLs
- stacks are independent compose projects, all joined by the shared `caddy_network`
- every published port binds to `127.0.0.1`; the only way in is Tailscale Serve (tailnet only). The one exception is
  Syncthing's sync port `22000`, which binds to the tailnet IP (see [Syncthing](#syncthing))

## micro-homie setup

From a fresh Debian 13 install with Tailscale on it:

```bash
git clone https://github.com/ferntheplant/homielab.git ~/homielab   # the read-only mirror, see GitHub mirrors
sudo ~/homielab/scripts/setup-micro-homie.sh     # Docker, 1Password CLI, git/rsync, linger, sysctl, timezone
# log out and back in to get the docker group, then:
umask 077; mkdir -p ~/.config/homielab
cat > ~/.config/homielab/op-token                # paste the service account token (see Secrets), then Ctrl-D
~/homielab/stacks/manage.sh start
~/homielab/scripts/install-systemd-units.sh
```

When Forgejo runs, point the checkout at it, see [This repo on micro-homie](#this-repo-on-micro-homie).

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

`sudo ~/homielab/scripts/setup-storage.sh` does the host setup: packages, mount point, fstab, mount, subvolumes,
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
- `forgejo`: the git host + its sign-in proxy, on its own network (see [Forgejo](#forgejo))
- `kata`: the Kata issue tracker for agents + its socket gate, on its own network (see [Kata](#kata))

Two containers mount the Docker socket directly: `socket-proxy`, and `beszel-agent` for container stats. The agent
uses the host network, so it cannot reach the proxy's internal network.

## Secrets

Secrets live in the 1Password `homielab` vault, one Secure Note per stack (`beeper`, `homie`, `karakeep`, `forgejo`), one
field per variable. Each `stacks/<stack>/.env.tpl` maps variable names to `op://homielab/<stack>/<VAR>` refs and is
safe to commit. `manage.sh` wraps every compose call in `op run --env-file .env.tpl`, and each service in the compose
files lists only the variables it needs (`VAR: ${VAR}`).

`op` authenticates with a read-only service account (`lil-homie-server`). Its token is in
`~/.config/homielab/op-token` (mode 0600) on micro-homie. `manage.sh` and the classifier read it from there.

To add or change a secret:

1. Edit the stack's item in 1Password (add a field named after the variable). The service account is read-only, so
   use the 1Password app or your own `op` account, e.g. `op item edit karakeep --vault homielab 'VAR[text]=value'`.
2. Add `VAR=op://homielab/<stack>/VAR` to `stacks/<stack>/.env.tpl`.
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
`zenika/alpine-chrome:124`, the Chrome version Karakeep documents, `ghcr.io/kenn-io/kata:0.22.0`). Bump those by
hand. Forgejo is pinned to a
major version (`17-rootless`), so the update gets its patch releases only. See [Forgejo](#forgejo).

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

All access is via Tailscale, to the tailnet only. The server is `micro-homie.tail8cc0d3.ts.net`.

### Tailscale Services

Each app has its own tailnet name, a [Tailscale Service](https://tailscale.com/docs/features/tailscale-services)
with its own HTTPS certificate. micro-homie hosts them all (`tailscale/services.json`):

- `https://lab.tail8cc0d3.ts.net/` → `127.0.0.1:8080` (Caddy → Glance dashboard)
- `https://karakeep.tail8cc0d3.ts.net/` → `127.0.0.1:3000`
- `https://forgejo.tail8cc0d3.ts.net/` → `127.0.0.1:3001` (sign-in proxy), and `forgejo.tail8cc0d3.ts.net:22` (TCP)
  → `127.0.0.1:2222` (git over SSH)
- `https://syncthing.tail8cc0d3.ts.net/` → `127.0.0.1:8384`
- `https://backrest.tail8cc0d3.ts.net/` → `127.0.0.1:9898`
- `https://logs.tail8cc0d3.ts.net/` → `127.0.0.1:8082` (Dozzle)
- `https://beszel.tail8cc0d3.ts.net/` → `127.0.0.1:8090`
- `https://kata.tail8cc0d3.ts.net/` → `127.0.0.1:7777` (Kata web UI)
- `https://kata-api.tail8cc0d3.ts.net/` → `127.0.0.1:7778` (Kata API for agents, through `kata-gate`)

A Service host must be a tagged device, so micro-homie is `tag:micro-homie` and has no user identity. Serve still adds
the `Tailscale-User-Login` header for a request from your own devices, so Forgejo sign-in works (tested).

`.env.shared` holds the tailnet names (`TS_TAILNET`, `TS_HOST`, `LIL_HOMIE_HOST`). The compose files and the Glance
config read them, so a rename is a one-line change.

The box's own name, `micro-homie.tail8cc0d3.ts.net`, serves only Rex on port 7297 (see [Rex](#rex)). The ports that
it used to serve (443, 3001, 3003, and 2222) are off.

`setup-micro-homie.sh` makes `fjorn` the Tailscale operator, so `tailscale serve` works without sudo. If
`tailscale serve status` shows `No serve config`, run it once interactively to get the one-time consent flow.

### Tailnet config in code

Do not change the tailnet in the admin console. These files are the source:

- `tailscale/policy.hujson`: the whole tailnet policy (grants, tags, `autoApprovers`, tests)
- `tailscale/services.json`: the Service definitions and their endpoints on micro-homie, and the device tags

A new Service needs an entry in `services.json`, plus a grant and an `autoApprovers` entry in `policy.hujson`.

```bash
python3 -I scripts/tailscale-apply.py            # plan: validate the policy, show the diff, change nothing
python3 -I scripts/tailscale-apply.py --apply    # apply the policy, then the Services, then the device tags
./stacks/manage.sh serve                         # on micro-homie: serve the endpoints (runs `tailscale-apply.py serve`)
```

The API commands use the OAuth client in the 1Password item `homielab/Tailscale`, with the scopes `policy_file`,
`services`, and `devices:core`. `op` reads it with the service account token, as `manage.sh` does. The
Tailscale-hosted policy is replaced at each apply, so an edit in the console is lost.

Run `--apply` before `serve` for a new Service. If the box serves a Service before it exists, the tailnet does not
approve the box as its host later, and the name does not connect. To fix it, on micro-homie run
`tailscale serve drain svc:<name>`, then `tailscale serve advertise svc:<name>`.

### Tailnet guard

micro-homie is now tagged (`tag:micro-homie`), so its traffic on the tailnet no longer carries your identity.
Before the tag, Tailscale Serve on another device signed that traffic in as you: Rex on `lil-homie-rex`, for
example, which gives a shell on the Mac. Container traffic leaves through micro-homie too, and some containers run
code that is not fully trusted. `karakeep-chrome`, for example, loads every page that you bookmark. The guard below
keeps containers off the tailnet, apart from a few exceptions.

The tailnet guard is an nftables table (`hosts/micro-homie/tailnet-guard/tailnet-guard.nft`). It drops container
traffic to the tailnet, except:

- MagicDNS (`100.100.100.100`, port 53)
- karakeep to lil-homie 8080 and 8081 (llama-server), Kata to lil-homie 8081 (embeddings), and Glance to lil-homie
  8765 (agent status). The rule does not name the source container, so each container can reach these 3 ports.
- Syncthing to its peers on port 22000

It also drops container traffic to micro-homie's own tailnet addresses. The CI jobs have their own, stricter sandbox
(see [Forgejo Actions](#forgejo-actions)).

The guard does not apply to host processes or to a container with `network_mode: host` (`beszel-agent`). The tag
covers those: their traffic carries `tag:micro-homie`, not your identity. The policy still allows all traffic, so the
tag reaches lil-homie (8080, 8081, 8765) and the Syncthing peers (22000). If the policy gets narrower, keep grants for
those. `scripts/forgejo-github-mirror.sh` signs in to Forgejo on `127.0.0.1` with the header, so it does not need the
box's identity.

The rules name lil-homie by its tailnet IP (`define lil_homie`). If lil-homie gets a new IP (for example, after it
is removed and added to the tailnet again), change the IP and run the script again.

To install it, or to apply a change to the rules, run:

```bash
sudo ~/homielab/scripts/setup-tailnet-guard.sh
```

The script copies the rules to `/etc/homielab/`. The `homielab-tailnet-guard` system unit loads them at boot, before
Docker starts. The table is separate from Docker's and Tailscale's rules, so they cannot reorder or remove it.

- **Dropped traffic:** `sudo journalctl -k --grep 'tailnet-guard drop'`. The log is limited to 6 lines a minute.
- **Rule counters:** `sudo nft list table inet homielab_tailnet_guard`.
- **A new container that needs a tailnet service:** add an exception to the `from_container` chain, then run the
  script again.
- **Turn it off:** `sudo systemctl disable --now homielab-tailnet-guard`. Stopping the unit deletes the table.
- **Do not** enable Debian's `nftables.service` with its default `/etc/nftables.conf`. That file starts with
  `flush ruleset`, which removes this table and Docker's rules.

## Rex

Rex runs on micro-homie as the **login server** (`rex-login-server.service`), not the package's default shared
server. It identifies callers by their Tailscale login and runs their terminals as `fjorn`. Tailscale Serve publishes
it on port 7297. `scripts/setup-rex-login-server.sh` (run with sudo) sets this up.

In Rex for Mac, choose **Add Remote Host…** and enter the full address with the port. A bare hostname makes Rex try
443 first:

```
https://micro-homie.tail8cc0d3.ts.net:7297
```

The accounts file is `/etc/rex/accounts.jsonc` and needs `"version": 1`. Its server log is
`sudo journalctl -u rex-login-server`.

micro-homie runs no agents: its dotfiles profile is `infra`, which has no agent config. Agents run on lil-homie or
a laptop, and use Rex to open terminals here. Those terminals run as `fjorn`, who is in the `docker` group, so an
agent in one has the same access to the services as you do.

## Agents

`.agents/skills/homielab/` is a skill for coding agents: which machine is which, Rex terminals on micro-homie, SSH
and the KVM for gaming-homie, the dev proxy, Forgejo, and Kata. It points to the sections of this README, so keep the
two in step. The dotfiles link it into each agent's skills on every machine that has this checkout at `~/homielab`
(`scripts/link-skills.sh` there), so a `git pull` here updates it everywhere.

## SSH

Using a classic SSH pub/priv key setup to SSH into the boxes over the tailnet. On lil-homie I had to disable async
shell rendering (atuin, zsh-autosuggestions, fzf-tab) to get the SSH experience to not suck ass.

## Caddy

Caddy serves only the dashboard: `svc:lab` → `127.0.0.1:8080` → `glance:8080`. Every other app has its own Tailscale
Service, so no app needs a base path.

## Syncthing

I use Syncthing to keep a handful of folders in sync across my devices.

### Tailnet-only setup

The sync port (`22000`, TCP and UDP) binds to micro-homie's tailnet IP (`TS_IP` in `.env.shared`), so nothing on the
LAN can reach it. The GUI binds to `127.0.0.1` and is only reachable at `https://syncthing.tail8cc0d3.ts.net`.

The other devices point at micro-homie's tailnet IP: `tcp://100.80.5.26:22000`.

Optional (strict tailnet-only): disable Global Discovery and Relays in `Settings → Connections`.

## Backrest

[Backrest](https://github.com/garethgeorge/backrest) is a UI over restic. Backups are encrypted and stored in a
Cloudflare R2 bucket. See [Backups](#backups) for what gets backed up. The Backrest instance ID is still `lil-homie`
(it cannot change), so the snapshot history continues from the Mac.

## Karakeep

Served at `https://karakeep.tail8cc0d3.ts.net`, its own origin, for the mobile app and the browser extension. Set that
address as the server in each of them.

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

## Forgejo

[Forgejo](https://forgejo.org) is the git host. It is tailnet-only, like everything else:

- web: `https://forgejo.tail8cc0d3.ts.net/`
- git: `git@forgejo.tail8cc0d3.ts.net:ferntheplant/<repo>.git` (SSH), or `https://forgejo.tail8cc0d3.ts.net/ferntheplant/<repo>.git`
  (HTTPS, signed in by your Tailscale login)

The SSH server is Forgejo's own, on `127.0.0.1:2222`. The Service forwards port 22 to it.

Push to create is on for my user: a push to a `ferntheplant/<repo>` that does not exist creates it as a private repo.
A push from a deploy key cannot create a repo, because it needs a key on the user account.

Files are in `stacks/forgejo/`. Data is in `data/forgejo`: `custom/conf/app.ini`, the repos in `git/repositories`,
and the SQLite database in `data/gitea.db`. The compose file sets the config through `FORGEJO__<section>__<KEY>`
variables, which Forgejo writes into `app.ini` at each start. To change a setting, edit the compose file, not
`app.ini`.

### Sign-in through Tailscale

There is no password to type. Tailscale Serve adds a `Tailscale-User-Login` header to each request from a tailnet
user. The `forgejo-proxy` Caddy (`stacks/forgejo/Caddyfile`) removes any `X-WEBAUTH-*` header from the client. When
the login is `TS_LOGIN`, it sets `X-WEBAUTH-USER` to `FORGEJO_USER` (both in `.env.shared`). Forgejo signs that user
in (reverse proxy authentication). Another tailnet user gets the normal sign-in page, and registration is off.

Forgejo accepts `X-WEBAUTH-USER` from any sender. `REVERSE_PROXY_TRUSTED_PROXIES` does not limit it (tested). The
network is the guard:

- Forgejo and its proxy are alone on `forgejo_network`, not on `caddy_network`. Docker blocks traffic between
  bridge networks, so no other container can reach them (tested from `glance`).
- The proxy port binds to `127.0.0.1`. A process on micro-homie itself can still send the header.
- Tailscale gives no identity headers for tagged devices. From a tagged device, sign in with the password. Set one
  first with the reset command below.

Reverse proxy authentication does not cover the API. API calls need an access token (Settings → Applications).

### First setup

1. Make the 1Password item. The service account is read-only, so use your own `op` account:

   ```bash
   op item create --vault homielab --category 'Secure Note' --title forgejo \
     "SECRET_KEY[password]=$(openssl rand -hex 32)" \
     "INTERNAL_TOKEN[password]=$(openssl rand -hex 48)"
   ```

   `SECRET_KEY` encrypts the push-mirror SSH keys and 2FA secrets in the database. If it is lost, the mirrors stop.
   Never change it on a running instance.
2. Make the data folder first: `mkdir -p data/forgejo`. The image runs as uid 1000 (`fjorn`). If the folder does not
   exist, Docker makes it as root, and Forgejo restarts in a loop.
3. `./stacks/manage.sh start forgejo`
4. Make the admin user. Its name must be `FORGEJO_USER`. Nobody needs the random password, because sign-in uses the
   header:

   ```bash
   docker exec forgejo forgejo admin user create --admin --username ferntheplant --email me@fjorn.dev \
     --random-password --must-change-password=false
   ```

5. Add your SSH public key in Settings → SSH / GPG keys. To show your signed commits as verified, click
   **Verify** on the key, then run the command that Forgejo shows. Forgejo makes it from its current settings, with a
   new token each time, so copy it from the page. Its `-n` namespace is not `git`.

To reset the password, run `docker exec -it forgejo forgejo admin user change-password -u ferntheplant -p <new>`.

### Settings

- New repos are private in the web form (`DEFAULT_PRIVATE=private`). The API does not use this setting: a repo made
  through the API is public unless the call sends `"private": true`.
- The default merge style for pull requests is squash. crux and baseline allow squash only, as BASELINE.md in each
  repo says. The squash message comes from `.forgejo/default_merge_message/SQUASH_TEMPLATE.md` in the repo: the PR
  title and description. The web merge form uses the template. The API merge does not.
- Actions is on. See [Forgejo Actions](#forgejo-actions).
- Wiki and projects are off on every repo (`DISABLED_REPO_UNITS`). They do not show in the repo menu or settings.
- Glance reads Forgejo through `forgejo-ci`, on the internal network `forgejo_glance` (made by the homie stack).
  `forgejo-ci` removes the sign-in headers, so Glance signs in with `FORGEJO_TOKEN` only. Glance cannot reach
  `forgejo_network`, and a container cannot reach Tailscale Serve on this box.

### GitHub mirrors

The public repos on GitHub (`github.com/ferntheplant/<repo>`) are read-only mirrors of Forgejo. Forgejo is the
source. Push to Forgejo only. Mirrored now: `homielab`, `crux`, `baseline`, `dotfiles`, `fjorndotdev`.

- **Push mirror:** each repo has a Forgejo push mirror over SSH. It syncs on each push and every 8 hours. It
  force-pushes `refs/heads/*` and `refs/tags/*`, and it deletes a branch or tag on GitHub that Forgejo does not have.
  See the state in the repo's Settings > Repository > Mirror settings.
- **Keys:** Forgejo makes one SSH key for each mirror. The public key is a deploy key with write access on that one
  GitHub repo. `SECRET_KEY` encrypts the private key in the Forgejo database.
- **Read-only:** two GitHub rulesets on each repo (branches, tags) block the creation, update, and deletion of refs
  for everyone except deploy keys. A push from your own account fails with `push declined due to repository rule
  violations`, so an old clone that still points at GitHub cannot lose commits.
- **GitHub features:** issues, wiki, and projects are off. Old pull requests stay on GitHub. Forgejo did not import
  them, and it did not import GitHub's `refs/pull/*` refs, because Forgejo uses those names for its own PRs.
- **GitHub Actions:** crux and baseline keep their workflows in `.forgejo/workflows`, so GitHub runs nothing for
  them. fjorndotdev keeps its deploy workflow in `.github/workflows`, which Forgejo also reads. It deploys from
  Forgejo, with the Cloudflare secrets in the Forgejo repo. Actions is off for fjorndotdev on GitHub, so the deploy
  does not also run there.

To mirror another repo, make it public in Forgejo (public marks a mirrored repo), then run:

```bash
scripts/forgejo-github-mirror.sh <repo>
```

The script stops if GitHub has a commit that Forgejo does not have. If the GitHub repo is new or private, it scans
the full history with gitleaks first, because the mirror makes the history public. Then it makes the GitHub repo if
it does not exist, and sets up the mirror, deploy key, rulesets, and settings. At the end, it checks that the refs
match.

To move a repo from GitHub to Forgejo, import it first (New Migration > Git, from the GitHub HTTPS URL), then delete
its `refs/pull/*` refs, then run the script.

To stop a mirror, delete it in Forgejo, then delete the deploy key and the two rulesets on GitHub.

### Forgejo Actions

CI runs on Forgejo Actions, with one runner on micro-homie: the `forgejo-runner` stack (`stacks/forgejo-runner/`).
It runs one job at a time. The runner registration is in `data/forgejo-runner/.runner`.

A job runs code from the repo and from every dependency that it installs, so each job runs in a sandbox:

```text
job container --(forgejo_ci, internal: no route out)--> forgejo-ci --> forgejo:3000
                                                     --> forgejo-runner (cache)
                                                     --> forgejo-runner-egress (Squid) --> internet
```

- **Network:** each job runs in its own container on `forgejo_ci`. That network is internal, so a job reaches only
  three containers. The egress proxy refuses private, tailnet, link-local, and loopback addresses
  (`egress/squid.conf`). This is necessary because traffic that leaves micro-homie carries its tailnet identity,
  which is yours. The runner sets `HTTPS_PROXY` and the related variables in each job.
- **Forgejo:** jobs reach Forgejo through `forgejo-ci`, a Caddy that removes `X-WEBAUTH-*` headers and sets none
  (`stacks/forgejo/Caddyfile.ci`). A job signs in with its token only. The runner connects to `http://forgejo-ci`
  too, and gives that URL to its jobs as `GITHUB_SERVER_URL`. `actions/checkout` clones from there.
- **Host:** jobs get no Docker socket, no host mounts, and no privileged mode. Each job has a limit of 3 GB of
  memory, 3 CPUs, and 4096 processes (`config.yml`). A workflow cannot raise these limits or change the network.
- **Trust:** the runner itself has the Docker socket, which is root on micro-homie. Only the runner image is
  trusted, not the jobs.

Tested from a job: npm and GitHub work through the proxy. `lil-homie`, Rex, Forgejo through Serve, `100.x`
addresses, the LAN, and the host get a 403 from the proxy or have no route. A forged `X-WEBAUTH-USER` through
`forgejo-ci` does not sign in.

Settings:

- `DEFAULT_ACTIONS_URL` is `https://github.com`, so `uses: actions/checkout@v7` resolves as on GitHub.
- The `ubuntu-latest` label runs in `node:24-trixie`. It has git, curl, python3, and Node, but not `jq` or `yq`.
  `manage.sh update` does not pull it. Pull it by hand: `docker pull node:24-trixie`.
- `homielab-bot` is a Forgejo user with write access on crux and baseline. Its token (scopes `write:repository`,
  `read:user`) is the user-level Actions secret `BUMP_BOT_TOKEN`. Forgejo starts no workflow for a change made with
  a job's own token, so the weekly bump pushes and opens its PR as the bot, and the PR gets CI.
- crux and baseline protect `main`: push is off, and the PR check `CI / validate (pull_request)` must pass on an
  up-to-date branch.

To register the runner again (for example on a new box), stop it, delete `data/forgejo-runner/.runner`, then:

```bash
cd stacks/forgejo-runner
docker compose --env-file ../../.env.shared run --rm --no-deps forgejo-runner forgejo-runner register \
  --no-interactive --instance http://forgejo-ci --name micro-homie --config /config.yml \
  --token "$(docker exec forgejo forgejo actions generate-runner-token | tail -1)"
```

Delete the old runner in Site administration > Actions > Runners.

The design keeps the runner in its own stack, so it can move to a dedicated CI box later. A runner on another box
must reach Forgejo over the tailnet. Its jobs must not use that box's tailnet identity, so tag the box in Tailscale
first.

### This repo on micro-homie

The checkout at `~/homielab` pushes to Forgejo with its own key, `~/.ssh/homielab_forgejo`. That key is a deploy key
with write access on `homielab` only. The repo's git config selects the key:

```bash
git config core.sshCommand "ssh -i ~/.ssh/homielab_forgejo -o IdentitiesOnly=yes"
git remote set-url origin git@forgejo.tail8cc0d3.ts.net:ferntheplant/homielab.git
```

On a new box, make a new key and add it in Forgejo: homielab > Settings > Deploy keys, with write access.

`~/dotfiles` on micro-homie works the same way, with `~/.ssh/dotfiles_forgejo`. That is a **read-only** deploy key on
`dotfiles`. The tagged box cannot sign in over HTTPS, so every checkout on it needs a key.

The deploy key is not a security limit on micro-homie. Two things give every process that runs as `fjorn` full
access to Forgejo:

- `fjorn` is in the `docker` group, which is the same as root. It can change Forgejo's data directly.
- Any process on the box can send `Tailscale-User-Login` to the sign-in proxy on `127.0.0.1:3001`, which signs it in
  as you (`scripts/forgejo-github-mirror.sh` does this). Through Tailscale, the tagged box is not signed in.

### jj

jj works with Forgejo as a normal git remote. Tested on Forgejo 17.0.1 with jj 0.47.1: a commit with jj's `change-id`
header and an SSH signature pushed over SSH, showed as verified, and kept the header after a `git clone`. jj does not
support Git LFS, so LFS is off. A squash merge (the default) writes a new commit without the `change-id` header. A
fast-forward merge keeps it.

### Updates

The image is `codeberg.org/forgejo/forgejo:17-rootless`. The weekly update pulls patch releases of 17. Version 17 is
supported until 2027-01-28. Before that date, read the release notes for the next major version, change the tag, and
run `./stacks/manage.sh update forgejo`. The update takes a snapshot first. The next long-term support version is 19
(April 2027).

## Kata

[Kata](https://www.katatracker.com/docs/) is an issue tracker for coding agents. One daemon on micro-homie holds the
to-do lists for agents on any device in the tailnet. There are no tokens: Tailscale is the boundary.

This repo has its own Kata project: the committed `.kata.toml` at the repo root binds the checkout to it. Pending work
on homielab goes there; the README keeps the how-it-works record, and Kata keeps the broken, pending, and
half-remembered record.

- web UI: `https://kata.tail8cc0d3.ts.net/` (the actor is your Tailscale login)
- API for agents: `https://kata-api.tail8cc0d3.ts.net/` (the actor is the agent's `KATA_AUTHOR`)

Files are in `stacks/kata/`. Data is in `data/kata`: the SQLite database `kata.db`, the search index
`kata.vectors.db`, and the JSONL backups in `backups/`. The config is `stacks/kata/config.toml`, mounted read-only
at `/data/config.toml`.

### Two listeners, two kinds of actor

Kata takes the actor from a trusted proxy header or from the request, but not both on one listener. So it has two
listeners:

```text
browser: svc:kata     -> 127.0.0.1:7777 -> kata 172.30.3.10:7777        actor = Tailscale-User-Login
agents:  svc:kata-api -> 127.0.0.1:7778 -> kata-gate -> Unix socket     actor = KATA_AUTHOR
Glance:  kata_glance  -> kata-gate:8080 -> Unix socket                  GET /api/v1/health only
```

- **Browser.** The UI listener is a trusted proxy listener (`[auth.proxy]` in `config.toml`). Tailscale Serve
  removes any `Tailscale-User-Login` header that the client sends, and sets it for a request from a user's device.
  Kata makes that login the actor and opens the UI without a token login. A write without the header gets
  `400 actor_header_required`. A device with a tag gets no header, so it can read the UI but not write.
- **Agents.** Kata trusts its Unix socket as the owner and takes the actor from the request. `kata-gate` (Caddy)
  forwards `svc:kata-api` to the socket. It removes the forwarding headers, because Kata does not trust a request
  with forwarding headers as local. The socket path has the hash of the database path, so the path in
  `stacks/kata/Caddyfile` changes only if the database path changes.
- **Glance.** `kata-gate` also joins `kata_glance` (made by the homie stack, like `forgejo_glance`). There it answers
  the health check only, and its agent port refuses Glance with a 403.

Every tailnet member can write anything through `svc:kata-api`, with any actor name. Any process on micro-homie can
do the same on `127.0.0.1:7778`, or send the header on `127.0.0.1:7777`. The kata stack is not on `caddy_network`,
so other containers cannot reach Kata.

### Agents

Point an agent at the API and give it a name:

```bash
export KATA_SERVER=https://kata-api.tail8cc0d3.ts.net
export KATA_AUTHOR=opus-5-5+claude@lil-homie  # the actor: <model>+<harness>@<host>
export KATA_TEAMMATE=reviewer                # optional: a sub-agent under the actor
kata whoami
```

Do not point an agent at `kata.tail8cc0d3.ts.net`: there, the actor is your Tailscale login, not the agent. Agents
do not run on micro-homie (see [Rex](#rex)), so no client uses `127.0.0.1:7778`.

The dotfiles set up the clients: the `kata` CLI, `~/.kata/config.toml` with the daemon URL, each agent's actor, and
the agent hooks (see the Kata and "Agent identity" sections of the dotfiles README).

### Semantic search

Kata embeds each issue with `nomic-embed-text-v2-moe` on lil-homie (`LLM_EMBED_URL`, see
[AI tagging](#ai-tagging-llama-server)), with the model card's `search_document: ` and `search_query: ` prefixes.
Kata sends text in chunks of 2000 characters, and the embeddings proxy cuts each one to 512 tokens. `GET
/api/v1/health` shows the `embeddings` state: `embedded`, `skipped`, `backlog`, and the time of the last success.
What search does while lil-homie is off is not tested; check `backlog` after an outage.

### Backups

`scripts/snapshot-data.sh` stops Kata and copies `data/kata` each night, as for the other databases. Kata also writes
a full JSONL export to `data/kata/backups/` at start and every 24 hours, and keeps 30 days. To restore an export, stop
Kata, move `data/kata/kata.db*` away, then run `kata import --input <file>.jsonl --target /data/kata.db` in the
image.

### Updates

The image is pinned (`ghcr.io/kenn-io/kata:0.22.0`), because Kata migrates its database at start and cannot go
back. Read the [changelog](https://www.katatracker.com/docs/changelog/), change the tag, then run
`./stacks/manage.sh update kata` (it takes a snapshot first). Update the daemon before the agents' `kata` CLI: an
older daemon refuses some requests from a newer CLI.

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
`./stacks/manage.sh update` rebuilds it on a fresh base. Links in the config use `${TS_TAILNET}` from `.env.shared`.

Layout: the left column has weather, Syncthing, backups, the dog, and xkcd. The center column has the news
tabs above the availability tabs. The right sidebar has the services monitor, server stats, and Forgejo.
The services monitor links to each Tailscale Service, so there are no quick links.

Widgets:

- Services: Glance's built-in `monitor` widget, checking each service over `caddy_network` (Forgejo over
  `forgejo_glance`, Kata over `kata_glance`)
- [Beszel server stats](https://github.com/glanceapp/community-widgets/blob/main/widgets/beszel-server-stats/README.md):
  calls the hub over `caddy_network` (`BESZEL_URL` is `host:port`, no scheme), and links to
  `https://beszel.tail8cc0d3.ts.net` (`APP_URL`). The API token expires once a year, see [Beszel](#beszel).
- [Forgejo repos](https://github.com/glanceapp/community-widgets/blob/main/widgets/forgejo-repos/README.md): the
  repos sorted by the last update, through `forgejo-ci` (see the Forgejo settings). `FORGEJO_TOKEN` is an access
  token for `ferntheplant` with the `read:repository` scope only. To make a new one:

  ```bash
  ssh micro-homie docker exec forgejo forgejo admin user generate-access-token --username ferntheplant \
    --token-name glance --scopes read:repository --raw
  ```

  Put it in the `homie` item in 1Password as `FORGEJO_TOKEN`, then run `./stacks/manage.sh start homie`.
- [Syncthing](https://github.com/glanceapp/community-widgets/blob/main/widgets/syncthing/README.md): hard coded values swapped for env variables
- [Backrest](https://github.com/not-first/restic-glance-extension): for a remote repo you only need the env variables.
  With no snapshots the widget shows weird errors, so make sure at least one snapshot exists
- lil-homie agents: reads `launchctl-agent-monitor.py` on the Mac at `https://${LIL_HOMIE_HOST}:8765/status`.
  Services are listed at the top of the script; an entry with a `url` is checked over HTTP instead of `launchctl`
- [AQI](https://github.com/glanceapp/community-widgets/blob/main/widgets/air-quality/README.md) and
  [Weather](https://github.com/glanceapp/community-widgets/blob/main/widgets/weather-seven-day/README.md): default instructions
- [xkcd](https://github.com/glanceapp/community-widgets/blob/main/widgets/xkcd-widget/README.md): the latest comic,
  with its alt text on hover
- Built-in: `hacker-news` and `lobsters`

## Beszel

The hub runs in the homie stack. Each machine has an agent that connects to the hub over WebSocket (`HUB_URL` +
`TOKEN`). On micro-homie and lil-homie, the agent env file is `~/.config/beszel/beszel-agent.env`. It holds `KEY`
(the hub public key) and `TOKEN` (from "Add system" in the hub).

- micro-homie: the `beszel-agent` container, `HUB_URL=http://127.0.0.1:8090`
- lil-homie: the `dev.fjorn.beszel-agent` launchd agent, `HUB_URL=https://beszel.tail8cc0d3.ts.net`
- gaming-homie: the `beszel-agent` Windows service, `HUB_URL=https://beszel.tail8cc0d3.ts.net`.
  The [Windows install script](https://www.beszel.dev/guide/agent-installation#windows) installed the agent and NSSM
  with WinGet. NSSM runs the agent as a service and keeps `KEY`, `TOKEN`, `HUB_URL` and `PORT` in the service
  settings, not in an env file. The log is `C:\ProgramData\beszel-agent\logs\beszel-agent.log`. In an admin
  PowerShell, `nssm status beszel-agent` shows the state and `nssm edit beszel-agent` opens the settings. To update,
  run `winget upgrade --exact --id henrygd.beszel-agent`, then `nssm restart beszel-agent`. The service runs
  `WinGet\Links\beszel-agent.exe`, so the path stays the same after an upgrade

SSH to gaming-homie is `ssh fjorn@gaming-homie` (Windows OpenSSH; the default shell is PowerShell, so use
PowerShell syntax, not `cmd.exe` syntax such as `%USERNAME%`). The account is an
admin, so its keys are in `C:\ProgramData\ssh\administrators_authorized_keys`, not in `~\.ssh\authorized_keys`.

Forgot the login password? There is no email setup, so reset it on micro-homie. The script asks for the new
password and sets it on the PocketBase superuser and on the Beszel user (same email):

```bash
~/homielab/scripts/beszel-reset-password.sh
```

The Glance Server Stats widget reads the hub API with `BESZEL_API_TOKEN`. A normal login token expires after 7 days, so
the widget uses a token that the superuser issues for one year. The current token expires on 2027-11-07. When the
Server Stats widget is empty, make a new token:

1. On micro-homie, run `~/homielab/scripts/beszel-glance-token.sh`. It asks for the superuser password and writes
   the token to `~/.config/homielab/beszel-api-token`.
2. On a Mac where your own `op` account is signed in, copy the token into 1Password. Keep the single quotes, so
   that `~` expands on micro-homie:

   ```bash
   op item edit homie --vault homielab "BESZEL_API_TOKEN=$(ssh micro-homie 'cat ~/.config/homielab/beszel-api-token')"
   ```

3. On micro-homie, run `~/homielab/stacks/manage.sh start homie` to recreate Glance with the new token.

## Comet (KVM)

The GL.iNet Comet (GL-RM1) is an IP-KVM for gaming-homie. It captures gaming-homie's HDMI output, acts as its USB
keyboard and mouse, and controls its power through the ATX power board. Use it when gaming-homie is off, frozen, in
the BIOS, or off the network. For a PC that runs, SSH is faster and more reliable (see [Beszel](#beszel) for the
login).

- **Network:** the Comet is on the home network, not on the tailnet. The router's DHCP gives it an address, and its
  name is `glkvm.local` (mDNS, so only on the home network). Its MAC address is `94:83:c4:ca:0d:35`.
- **Dashboard:** `https://glkvm.local`, user `admin`. Firefox shows no video, and Safari works.
- **Display:** Windows sees the Comet as a second monitor. "Extend these displays" works, with the main monitor as
  the main display.
- **LED:** solid blue while it starts. Then flashing white (no internet) or solid white (internet).
- **Agents:** the kvm-pilot MCP server, read-only by default. The dotfiles install it on each agent machine and
  register it with Claude Code (see "KVM" in the dotfiles README).

The Comet connects to a TP-Link TL-SG105 switch. The cable from that switch to the router runs through the walls and
carries only 100 Mbps. In 2026-11, one switch port lost its link to the router again and again, so the Comet got no
address. Its LED was flashing white and the router did not list it. Moving the router cable to a different port fixed
it. If the Comet disappears again, look at the switch's light for the router cable first: it must stay green.

**API:** kvm-pilot uses the Comet's PiKVM-style API (`https://glkvm.local/api/...`). On GL.iNet firmware it can be off,
and a firmware update can turn it off again: then every `/api/*` call returns 404. To turn it on, SSH in as `root`,
uncomment the API block in `/etc/kvmd/nginx-kvmd.conf`, and restart the Comet. Update the firmware through the
dashboard: kvm-pilot's update command does nothing on this hardware. kvm-pilot screenshots at the native resolution
need firmware V1.9.1 or newer. With older firmware, `snapshot` returns HTTP 503.

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
- **Dev proxy** (`dev.fjorn.dev-proxy-caddy`, `dev.fjorn.portless`): any dev server on the Mac at
  `https://<name>.dev.fjorn.dev`, tailnet only. See [`hosts/lil-homie/dev-proxy/README.md`](hosts/lil-homie/dev-proxy/README.md)
- **Tailscale** (`dev.fjorn.tailscaled` LaunchDaemon)

Tailscale Serve on the Mac publishes these ports to the tailnet. Apply them once (Serve remembers them):

```bash
tailscale serve --bg --tcp=8080 tcp://127.0.0.1:8080      # llama-server
tailscale serve --bg --tcp=8081 tcp://127.0.0.1:8081      # embeddings proxy
tailscale serve --bg --https=8765 http://127.0.0.1:8765   # status server
tailscale serve --bg --tcp=443 tcp://127.0.0.1:8443       # dev proxy (Caddy)
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
nb bundle install ~/homielab/hosts/lil-homie/Nanobrewfile
```

`tailscaled` runs as the `dev.fjorn.tailscaled` LaunchDaemon from a root-owned copy of the nanobrew binary. After
`nb upgrade tailscale`, install the new binary (the tailnet drops for a few seconds; the script logs to
`/var/log/lil-homie-tailscaled-install.log`):

```bash
sudo ~/homielab/hosts/lil-homie/install-tailscaled-daemon.sh
```

If MagicDNS doesn't resolve on a CLI-only client, add a resolver file:

```bash
sudo mkdir -p /etc/resolver
echo "nameserver 100.100.100.100" | sudo tee /etc/resolver/tail8cc0d3.ts.net
sudo dscacheutil -flushcache
sudo killall -HUP mDNSResponder
```

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
