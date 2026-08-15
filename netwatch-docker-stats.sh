#!/bin/bash
# NetWatch – host-side Docker per-container stats snapshotter.
#
# Runs OUTSIDE any container, on the Docker host itself, and writes a plain
# text snapshot of `docker stats` into NetWatch's bind-mounted data/
# directory. NetWatch's own container never touches the Docker socket (see
# src/docker_stats.py for why) — it only ever reads this already-rendered
# file.
#
# Written via a temp file + atomic rename so NetWatch never reads a
# half-written snapshot mid-update.
#
# Installed as a systemd timer (see netwatch-docker-stats.service/.timer);
# run install with:
#   sudo cp netwatch-docker-stats.sh /usr/local/bin/
#   sudo chmod +x /usr/local/bin/netwatch-docker-stats.sh
#   sudo cp netwatch-docker-stats.service netwatch-docker-stats.timer /etc/systemd/system/
#   sudo systemctl daemon-reload
#   sudo systemctl enable --now netwatch-docker-stats.timer
#
# Adjust NETWATCH_DATA_DIR below if your deployment path differs from the
# DEPLOY-DOCKER.md default.

set -euo pipefail

NETWATCH_DATA_DIR="${NETWATCH_DATA_DIR:-/opt/netwatch-docker/data}"
OUT="${NETWATCH_DATA_DIR}/docker_stats_snapshot.txt"
TMP="${OUT}.tmp.$$"

trap 'rm -f "$TMP"' EXIT

{
    echo "# snapshot_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    docker stats --no-stream --format \
        'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.NetIO}}\t{{.BlockIO}}'
} > "$TMP"

mv "$TMP" "$OUT"
