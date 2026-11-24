"""Structure of the central workstation archive store."""
from contextlib import contextmanager
import sqlite3

ARCHIVE_SCHEMA = '''
CREATE TABLE IF NOT EXISTS archive_runs (
    station_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    title TEXT NOT NULL,
    method_id TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    source TEXT NOT NULL,
    report TEXT NOT NULL,
    received_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (station_id, batch_id)
);
CREATE INDEX IF NOT EXISTS archive_runs_received ON archive_runs(received_at DESC, station_id, batch_id);
CREATE TABLE IF NOT EXISTS archive_snapshots (
    station_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    hash TEXT NOT NULL,
    content TEXT NOT NULL,
    received_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (station_id, batch_id),
    FOREIGN KEY (station_id, batch_id) REFERENCES archive_runs(station_id, batch_id)
);
CREATE TABLE IF NOT EXISTS archive_receipts (
    station_id TEXT NOT NULL,
    backup_hash TEXT NOT NULL,
    batch_ids TEXT NOT NULL,
    run_count INTEGER NOT NULL,
    snapshot_count INTEGER NOT NULL,
    received_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (station_id, backup_hash)
);
'''


@contextmanager
def connect(path):
    connection = sqlite3.connect(path, timeout=5)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys = ON')
        with connection:
            yield connection
    finally:
        connection.close()


def initialize(path):
    with connect(path) as conn:
        conn.execute('PRAGMA journal_mode = WAL')
        conn.executescript(ARCHIVE_SCHEMA)
