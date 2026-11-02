#!/bin/bash
# Launcher for the Beszel host agent on lil-homie (launchd/dev.fjorn.beszel-agent.plist).
# nanobrew installs only the agent binary; Homebrew's formula generated this wrapper.
# The env file holds the hub key and token, so it stays out of the repo.
set -a
if [ -f "$HOME/.config/beszel/beszel-agent.env" ]; then
    source "$HOME/.config/beszel/beszel-agent.env"
fi
set +a
exec /opt/nanobrew/prefix/bin/beszel-agent "$@"
