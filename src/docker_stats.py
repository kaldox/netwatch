"""
NetWatch – Docker per-container resource snapshot reader.

NetWatch itself never touches the Docker socket. Mounting it into a
container (even ":ro") is effectively root on the host — the mount flag
only protects the socket *file*, not the Docker API reachable through it,
which can create privileged containers regardless. That's too much trust
to hand a passive evidence-collector.

Instead, a small script on the HOST (outside any container, run by cron or
a systemd timer — see scripts/docker-stats-snapshot.sh) periodically writes
`docker stats` output to a file inside the bind-mounted data/ directory.
This module only ever reads that already-rendered text file. If the file
is missing or stale, per-container stats are simply unavailable for that
event — everything else in NetWatch keeps working.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SNAPSHOT_FILENAME = "docker_stats_snapshot.txt"

# If the snapshot is older than this, the host-side snapshotter is either
# not installed or has stopped running — treat it as unavailable rather
# than silently attaching stale numbers to a fresh event.
MAX_AGE_SECONDS = 60.0


def read_docker_stats_snapshot(data_dir: Path) -> Optional[str]:
    """
    Return the current docker-stats snapshot text, or None if it doesn't
    exist or is too old to be trustworthy. Never raises — this is
    best-effort evidence, not a required piece of the measurement.
    """
    path = data_dir / SNAPSHOT_FILENAME
    try:
        stat = path.stat()
    except OSError:
        return None

    age = time.time() - stat.st_mtime
    if age > MAX_AGE_SECONDS:
        logger.debug(
            "%s is %.0fs old (>%.0fs) — host-side snapshotter not running? skipping",
            SNAPSHOT_FILENAME, age, MAX_AGE_SECONDS,
        )
        return None

    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.debug("Could not read %s: %s", SNAPSHOT_FILENAME, exc)
        return None
