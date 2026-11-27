#!/usr/bin/env bash
# Destroy every crocket-bench droplet + ssh key and verify zero remain.
doctl compute droplet delete --tag-name crocket-bench --force 2>&1
for id in $(doctl compute ssh-key list --format ID,Name --no-header | awk '$2=="crocket-bench"{print $1}'); do doctl compute ssh-key delete $id --force; done
for i in 1 2 3 4 5 6; do n=$(doctl compute droplet list --tag-name crocket-bench --format ID --no-header | wc -l); [ "$n" = 0 ] && break; sleep 5; done
echo "droplets left with tag crocket-bench: $n"; doctl compute droplet list --format ID,Name,Tags --no-header | grep -c crocket-bench || true
