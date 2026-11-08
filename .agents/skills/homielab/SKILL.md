---
name: homielab
description: The homielab home servers and tailnet. Use when work touches lil-homie, micro-homie, or another homielab machine; runs a command on micro-homie; serves a dev server to other devices; uses a Forgejo repo, pull request, or CI run; or needs a homielab service URL.
---

# homielab

The homielab repo is the source of truth, and its checkout is `~/homielab`. This skill names what each task needs and points to the README section that has the details: `~/homielab/README.md` unless a path says otherwise. Read that section before you change anything it covers.

## Where you are

Run `hostname -s` first. The machine decides what you can reach:

| Host | What it is | What runs there |
| --- | --- | --- |
| `lil-homie` | Mac mini devbox | agents, dev servers (the dev proxy), llama-server, the Beeper bridge |
| `micro-homie` | Debian 13 micro PC | every container stack, Forgejo, Kata, the Rex login server; no agents |
| `gaming-homie` | Windows 11 PC | a Beszel agent only |
| any other name | a laptop | agents; it reaches the rest over the tailnet |

Everything is on the tailnet `tail8cc0d3.ts.net`, and nothing is on the public internet. Each app is a Tailscale Service with its own name, such as `https://forgejo.tail8cc0d3.ts.net`. The full list is in "Tailscale Services".

## Run a command on micro-homie

Open a Rex terminal on micro-homie's login server. The address needs the port:

```bash
rex -S https://micro-homie.tail8cc0d3.ts.net:7297 run -c ~/homielab -- <command>
```

The `rex` skill covers the rest of the CLI (`capture`, `send`, `wait`). The terminal runs as `fjorn`, who is in the `docker` group, so it has full access to every service: ask before you stop, update, or delete a stack. Stacks change through `./stacks/manage.sh`, which takes a snapshot before an update ("Stacks + Manage Script").

## Serve a dev server

On lil-homie, start the dev server through portless. Any tailnet device then opens it at `https://<name>.dev.fjorn.dev`:

```bash
portless myapp pnpm dev   # https://myapp.dev.fjorn.dev
```

A server that portless did not start is at `https://<port>.dev.fjorn.dev`. The app sees `myapp.localhost` as its Host, so Vite and Astro need no host setting. A script that chains commands (`&&`, `npm run x`) must read its port from `$PORT`. The details are in `hosts/lil-homie/dev-proxy/README.md`.

## Forgejo

Forgejo at `https://forgejo.tail8cc0d3.ts.net` is the git host. The remotes are `git@forgejo.tail8cc0d3.ts.net:ferntheplant/<repo>.git`, and a push to a new repo name creates a private repo. Use `fj` for pull requests and CI: inside a checkout it finds the host from the remote.

```bash
fj pr create --base main --head <branch> --body-file <file> "<title>"
fj pr status <n>      # mergeable, and the CI result
fj actions tasks      # recent CI runs
```

Merge a pull request only when the user asks. crux and baseline protect `main`: the check `CI / validate (pull_request)` must pass on a branch that is up to date with `main`, so rebase a branch that is behind and wait for CI again. Their squash merges take the PR title as the subject and the PR description as the body. `fj pr merge` sends its own message, not the repo's squash template, so pass both:

```bash
fj pr merge <n> -M squash -d -t "<PR title> (#<n>)" -m "<PR description>"
```

CI jobs run in a sandbox that reaches only Forgejo and the internet through a proxy, not the tailnet ("Forgejo Actions").

## Kata

Kata at `https://kata-api.tail8cc0d3.ts.net` is the issue tracker for agents. The dotfiles set up the `kata` CLI and each agent's actor (`KATA_AUTHOR`), and `kata whoami` shows both. The web UI at `https://kata.tail8cc0d3.ts.net` records your Tailscale login as the actor, so an agent always uses the API ("Kata").

This repo has its own Kata project, and the checkout carries a committed `.kata.toml` that binds it. Pending work on homielab goes there: search with `kata search "<terms>" --agent` first, and reuse an open issue instead of creating a second one for the same work. Keep the two truths apart: the README records how things work, and Kata records what is broken, pending, or half-remembered. When you find pending work, open or reuse a Kata issue for it, and keep the README to what is true now.

Point an issue at stable anchors: a README section heading, a file path, a function name, or a config key. A line number moves with the next edit to the file, so the anchor is the heading or the name.
