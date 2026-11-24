"""Inspect persisted runs and their release evidence using a read-only transaction."""
from collections import Counter
import json
import sqlite3

from .analysis import ENGINE, analyze
from .deliveries import build_deliveries
from .encoding import canonical, digest
from .errors import Rejected
from .sequence import parse_document
from .snapshots import payload


def inspect_run(run, snapshot, deliveries):
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
    expected = None
    if snapshot is not None:
        try:
            expected = {doc['sample_id']: doc
                        for doc in build_deliveries(snapshot['hash'], snapshot['content'], snapshot['created_at'])}
        except (ValueError, KeyError, TypeError, RecursionError):
            expected = None
    for delivery in deliveries:
        if digest(delivery['content']) != delivery['hash'] and 'delivery_digest' not in issues:
            issues.append('delivery_digest')
        document = expected.get(delivery['sample_id']) if expected is not None else None
        mismatched = (document is None or document['content'] != delivery['content']
                      or document['credential'] != delivery['credential'])
        if mismatched and 'delivery_replay' not in issues:
            issues.append('delivery_replay')
    return {'id': run_id, 'issues': issues}


def inspect_database(path):
    uri = path.resolve().as_uri() + '?mode=ro'
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute('BEGIN')
        structural = [row[0] for row in conn.execute('PRAGMA quick_check') if row[0] != 'ok']
        if structural:
            return {'ok': False, 'structural': structural, 'orphaned': [], 'runs': 0, 'snapshots': 0,
                    'deliveries': 0, 'issues': []}
        orphaned = [dict(row) for row in conn.execute('PRAGMA foreign_key_check')]
        has_deliveries = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'deliveries'").fetchone()
        rows = conn.execute('SELECT * FROM runs ORDER BY id')
        failures = []
        counts = Counter()
        for run in rows:
            counts['runs'] += 1
            snapshot = conn.execute('SELECT hash, content, created_at FROM snapshots WHERE run_id = ?',
                                    (run['id'],)).fetchone()
            if snapshot:
                counts['snapshots'] += 1
            deliveries = []
            if has_deliveries:
                deliveries = conn.execute('SELECT sample_id, hash, content, credential FROM deliveries WHERE run_id = ?',
                                          (run['id'],)).fetchall()
                counts['deliveries'] += len(deliveries)
            result = inspect_run(run, snapshot, deliveries)
            if result['issues']:
                failures.append(result)
        return {'ok': not failures and not orphaned, 'runs': counts['runs'], 'snapshots': counts['snapshots'],
                'deliveries': counts['deliveries'],
                'structural': structural, 'orphaned': orphaned, 'issues': failures}
    finally:
        conn.close()
