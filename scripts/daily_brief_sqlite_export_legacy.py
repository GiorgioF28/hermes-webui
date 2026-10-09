"""Prepare legacy JSON files from the private SQLite queue for rollback."""

from __future__ import annotations

import argparse
from pathlib import Path

from api.daily_brief_store import export_legacy


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True, help="legacy Daily Brief artifact directory")
    parser.add_argument("--db", type=Path, help="SQLite path; defaults to STATE_DIR/daily-brief/email-queue.sqlite3")
    args = parser.parse_args()
    db = args.db
    if db is None:
        from api.config import STATE_DIR

        db = Path(STATE_DIR) / "daily-brief" / "email-queue.sqlite3"
    result = export_legacy(db, args.data_dir)
    print(f"Rollback JSON prepared: {result['pending']} pending, {result['receipts']} receipts, {result['archiveRows']} archive rows.")
    print("SQLite was retained. Review the JSON and private rollback backups before switching code.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
