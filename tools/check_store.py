"""Verify canonical sources, reports and sealed releases in a local SQLite database."""
import argparse
import json
from pathlib import Path
import sqlite3

from chromaledger.integrity import inspect_database


def main():
    parser = argparse.ArgumentParser(description='只读核验本地数据库、核算结果及封存件')
    parser.add_argument('database', type=Path)
    args = parser.parse_args()
    try:
        report = inspect_database(args.database)
    except sqlite3.Error as exc:
        report = {'ok': False, 'error': 'database_unavailable', 'detail': str(exc)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
