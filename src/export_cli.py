"""
NetWatch – Provider export CLI.

Usage (on the Pi):
    sudo -u netwatch /opt/netwatch/venv/bin/python -m src.export_cli [days]

Generates a provider evidence PDF + CSV files under reports/ for the last
N days (default 14).
"""

from __future__ import annotations

import sys
from pathlib import Path

from src.config import load_config
from src.database import Database

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    days = 14
    if len(sys.argv) > 1:
        try:
            days = int(sys.argv[1])
        except ValueError:
            print(f"Ungültige Tagesangabe '{sys.argv[1]}', nutze 14.")

    cfg = load_config()
    db = Database(
        db_path=_PROJECT_ROOT / cfg.database.path,
        wal_mode=cfg.database.wal_mode,
    )

    # Import here so the heavy reportlab import only happens on export
    from src.export import generate_provider_report

    output_dir = _PROJECT_ROOT / cfg.reports.output_dir
    print(f"Erstelle Provider-Nachweis für die letzten {days} Tage…")
    files = generate_provider_report(db, cfg, output_dir, days=days)

    print("\nFertig. Erzeugte Dateien:")
    for name, path in files.items():
        print(f"  {name:12} {path}")


if __name__ == "__main__":
    main()
