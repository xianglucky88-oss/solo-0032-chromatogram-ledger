from contextlib import contextmanager
import sqlite3

SCHEMA = '''
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    method_id TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    source TEXT NOT NULL,
    report TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS runs_created ON runs(created_at DESC, id);
CREATE TABLE IF NOT EXISTS snapshots (
    run_id TEXT PRIMARY KEY REFERENCES runs(id),
    hash TEXT NOT NULL UNIQUE,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE TABLE IF NOT EXISTS registers (
    id TEXT PRIMARY KEY,
    register_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    left_run TEXT NOT NULL REFERENCES runs(id),
    right_run TEXT NOT NULL REFERENCES runs(id),
    supersedes TEXT REFERENCES registers(id),
    hash TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (register_id, version)
);
CREATE INDEX IF NOT EXISTS registers_runs ON registers(left_run, right_run, register_id, version);
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
        conn.executescript(SCHEMA)
