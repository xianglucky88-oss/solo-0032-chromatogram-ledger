"""Inspect persisted runs and their release evidence using a read-only transaction."""
from collections import Counter
import json
import sqlite3

from .analysis import ENGINE, analyze
from .encoding import canonical, digest
from .errors import Rejected
from .register import parse_register, validate_scope
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


def inspect_register(row, runs, prior):
    """Re-verify one frozen register version against its digest and its batches."""
    issues = []
    if digest(row['content']) != row['hash']:
        issues.append('register_digest')
    try:
        register = parse_register(json.loads(row['content']))
    except (ValueError, RecursionError, Rejected):
        return {'id': row['id'], 'issues': [*issues, 'register_invalid']}
    if canonical(register) != row['content']:
        issues.append('register_encoding')
    if register['id'] != row['register_id'] or register['version'] != row['version']:
        issues.append('register_identity')
    if {register['left_run'], register['right_run']} != {row['left_run'], row['right_run']}:
        issues.append('register_scope')
    if row['left_run'] not in runs or row['right_run'] not in runs:
        return {'id': row['id'], 'issues': [*issues, 'register_run_missing']}
    left, right = runs[row['left_run']], runs[row['right_run']]
    try:
        validate_scope(register, row['left_run'], left, right)
    except Rejected:
        issues.append('register_scope')
    if row['supersedes'] is not None:
        if prior is None or prior['id'] != row['supersedes'] or prior['version'] != row['version'] - 1:
            issues.append('register_version')
    elif row['version'] != 1:
        issues.append('register_version')
    return {'id': row['id'], 'issues': issues}


def inspect_database(path):
    uri = path.resolve().as_uri() + '?mode=ro'
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute('BEGIN')
        structural = [row[0] for row in conn.execute('PRAGMA quick_check') if row[0] != 'ok']
        if structural:
            return {'ok': False, 'structural': structural, 'orphaned': [], 'runs': 0, 'snapshots': 0,
                    'registers': 0, 'issues': []}
        orphaned = [dict(row) for row in conn.execute('PRAGMA foreign_key_check')]
        failures = []
        counts = Counter()
        runs = {}
        for run in conn.execute('SELECT * FROM runs ORDER BY id'):
            counts['runs'] += 1
            snapshot = conn.execute('SELECT hash, content FROM snapshots WHERE run_id = ?', (run['id'],)).fetchone()
            if snapshot:
                counts['snapshots'] += 1
            result = inspect_run(run, snapshot)
            if result['issues']:
                failures.append(result)
            runs[run['id']] = {'id': run['id'], 'source': json.loads(run['source']),
                               'report': json.loads(run['report'])}
        previous = {}
        for row in conn.execute('SELECT * FROM registers ORDER BY register_id, version'):
            counts['registers'] += 1
            prior = previous.get(row['register_id'])
            result = inspect_register(row, runs, prior)
            if result['issues']:
                failures.append({**result, 'kind': 'register'})
            previous[row['register_id']] = dict(row)
        return {'ok': not failures and not orphaned, 'runs': counts['runs'], 'snapshots': counts['snapshots'],
                'registers': counts['registers'], 'structural': structural, 'orphaned': orphaned,
                'issues': failures}
    finally:
        conn.close()
