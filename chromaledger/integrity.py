"""Inspect persisted runs and their release evidence using a read-only transaction."""
from collections import Counter
import json
import sqlite3

from .analysis import ENGINE, analyze
from .encoding import canonical, digest
from .errors import Rejected
from .sequence import parse_document
from .snapshots import payload


def inspect_run(run, snapshot):
    run_id = run['id']
    issues = []
    if digest(run['source']) != run['input_hash']:
        issues.append('input_digest')
    try:
        source = parse_document(json.loads(run['source']))
        report = json.loads(run['report'])
    except (ValueError, RecursionError, Rejected):
        return {'id': run_id, 'issues': [*issues, 'document_invalid']}
    if canonical(source) != run['source']:
        issues.append('source_encoding')
    if source['id'] != run_id or source['label'] != run['title'] or source['method']['id'] != run['method_id']:
        issues.append('catalog_mismatch')
    if not isinstance(report, dict) or report.get('engine') != ENGINE:
        issues.append('engine_unavailable')
    else:
        recomputed = analyze(source)
        if canonical(recomputed) != run['report']:
            issues.append('report_mismatch')
        if snapshot is not None:
            frozen = canonical(payload({**dict(run), 'source': source}, recomputed))
            if frozen != snapshot['content']:
                issues.append('snapshot_replay')
    if snapshot is not None and digest(snapshot['content']) != snapshot['hash']:
        issues.append('snapshot_digest')
    return {'id': run_id, 'issues': issues}


def inspect_database(path):
    uri = path.resolve().as_uri() + '?mode=ro'
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute('BEGIN')
        structural = [row[0] for row in conn.execute('PRAGMA quick_check') if row[0] != 'ok']
        if structural:
            return {'ok': False, 'structural': structural, 'orphaned': [], 'runs': 0, 'snapshots': 0, 'issues': []}
        orphaned = [dict(row) for row in conn.execute('PRAGMA foreign_key_check')]
        rows = conn.execute('SELECT * FROM runs ORDER BY id')
        failures = []
        counts = Counter()
        for run in rows:
            counts['runs'] += 1
            snapshot = conn.execute('SELECT hash, content FROM snapshots WHERE run_id = ?', (run['id'],)).fetchone()
            if snapshot:
                counts['snapshots'] += 1
            result = inspect_run(run, snapshot)
            if result['issues']:
                failures.append(result)
        return {'ok': not failures and not orphaned, 'runs': counts['runs'], 'snapshots': counts['snapshots'],
                'structural': structural, 'orphaned': orphaned, 'issues': failures}
    finally:
        conn.close()


def inspect_archive(path):
    """Read-only recomputation of every archived workstation record."""
    uri = path.resolve().as_uri() + '?mode=ro'
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute('BEGIN')
        structural = [row[0] for row in conn.execute('PRAGMA quick_check') if row[0] != 'ok']
        if structural:
            return {'ok': False, 'structural': structural, 'orphaned': [],
                    'stations': 0, 'runs': 0, 'snapshots': 0, 'issues': []}
        orphaned = [dict(row) for row in conn.execute('PRAGMA foreign_key_check')]
        rows = conn.execute(
            'SELECT station_id, batch_id, title, method_id, input_hash, source, report '
            'FROM archive_runs ORDER BY station_id, batch_id'
        )
        failures = []
        counts = Counter()
        stations = set()
        for row in rows:
            counts['runs'] += 1
            stations.add(row['station_id'])
            snapshot = conn.execute(
                'SELECT hash, content FROM archive_snapshots WHERE station_id = ? AND batch_id = ?',
                (row['station_id'], row['batch_id']),
            ).fetchone()
            if snapshot:
                counts['snapshots'] += 1
            run = dict(row)
            run['id'] = row['batch_id']
            result = inspect_run(run, snapshot)
            if result['issues']:
                failures.append({'station_id': row['station_id'], 'batch_id': row['batch_id'],
                                 'issues': result['issues']})
        receipts = conn.execute(
            'SELECT station_id, backup_hash, batch_ids, run_count, snapshot_count FROM archive_receipts'
        ).fetchall()
        receipt_issues = []
        for receipt in receipts:
            try:
                batch_ids = json.loads(receipt['batch_ids'])
            except (ValueError, RecursionError):
                batch_ids = None
            if not isinstance(batch_ids, list) or len(batch_ids) != receipt['run_count']:
                receipt_issues.append({'station_id': receipt['station_id'],
                                       'backup_hash': receipt['backup_hash'], 'issues': ['receipt_batches']})
        return {'ok': not failures and not orphaned and not receipt_issues,
                'stations': len(stations), 'runs': counts['runs'], 'snapshots': counts['snapshots'],
                'receipts': len(receipts), 'structural': structural, 'orphaned': orphaned,
                'issues': failures, 'receipt_issues': receipt_issues}
    finally:
        conn.close()
