"""Receive an offline workstation SQLite backup into the central archive.

Usage:
    python3 -m tools.intake_archive <station_id> <backup.sqlite3> [--archive-db PATH]

The station identifier is fixed by the central operator at intake time; it is
never read from the backup. The backup is opened read-only and every batch is
re-verified from its original inputs before anything is written. If any batch
fails verification or conflicts with received material, the whole intake is
rejected and the archive stays untouched.
"""
import argparse
import json
from pathlib import Path

from chromaledger.archive_database import initialize
from chromaledger.archive_intake import receive_backup
from chromaledger.encoding import canonical
from chromaledger.errors import Rejected


def main(argv=None):
    parser = argparse.ArgumentParser(description='总台接收工作站 SQLite 备份并核验入档')
    parser.add_argument('station_id', help='收件时确认的固定工作站标识')
    parser.add_argument('backup', type=Path, help='工作站 SQLite 备份文件')
    parser.add_argument('--archive-db', type=Path, default=Path('.chromaledger/archive.sqlite3'),
                        help='总台归档库路径')
    args = parser.parse_args(argv)
    args.archive_db.parent.mkdir(parents=True, exist_ok=True)
    initialize(args.archive_db)
    try:
        result = receive_backup(args.archive_db, args.station_id, args.backup)
    except Rejected as exc:
        print(json.dumps({'ok': False, 'error': exc.code, 'message': str(exc)}, ensure_ascii=False))
        return 1
    print(canonical({'ok': True, **result}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
