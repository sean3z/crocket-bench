#!/usr/bin/env bash
# Box-side hard cap: after $1 seconds (default 3 h) delete every crocket-bench droplet, no matter what.
# Needs DIGITALOCEAN_ACCESS_TOKEN in its environment (inherited); passes it explicitly with -t.
T="${DIGITALOCEAN_ACCESS_TOKEN:?no token}"
if doctl -t "$T" account get --format Status --no-header >/dev/null 2>&1; then echo "$(date) AUTH_OK armed for ${1:-10800}s"; else echo "$(date) AUTH_FAIL"; fi
sleep "${1:-10800}"
echo "$(date) watchdog firing"
doctl -t "$T" compute droplet delete --tag-name crocket-bench --force
sleep 10; echo "left:"; doctl -t "$T" compute droplet list --tag-name crocket-bench --format ID,Name --no-header
