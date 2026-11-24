from .analysis import ENGINE, analyze
from .deliveries import delivery_samples, issue_deliveries
from .encoding import canonical, digest
from .errors import Rejected
from .repository import find_run, snapshot_row


def reportable_rows(report):
    for injection in report['injections']:
        if injection['kind'] != 'sample':
            continue
        for target_id, entry in injection['decisions'].items():
            if entry['status'] == 'reportable':
                yield {'sample_id': injection['sample_id'], 'injection_id': injection['id'],
                       'order': injection['order'], 'target_id': target_id, 'concentration': entry['reported'],
                       'dilution': injection['dilution'], 'peak_id': injection['assignments'][target_id]['ids'][0],
                       'blank_id': entry['blank_id'], 'qc_id': entry['qc_id']}


def payload(run, report):
    return {'schema': 1, 'run_id': run['id'], 'engine': report['engine'],
            'input_sha256': run['input_hash'], 'method': run['source']['method'],
            'excluded_count': report['counts']['held'], 'results': list(reportable_rows(report))}


def get_snapshot(conn, run_id):
    row = snapshot_row(conn, run_id)
    if row is None:
        raise Rejected('snapshot_not_found', '该批次尚无交付快照。', status=404)
    if digest(row['content']) != row['hash']:
        raise Rejected('snapshot_corrupt', '快照内容与摘要不一致。', status=409)
    return row


def freeze(conn, run_id):
    conn.execute('BEGIN IMMEDIATE')
    run = find_run(conn, run_id)
    existing = snapshot_row(conn, run_id)
    if existing:
        row = get_snapshot(conn, run_id)
        return {'hash': row['hash'], 'reused': True, 'deliveries': delivery_samples(conn, run_id)}
    snapshot = payload(run, run['report'])
    if not snapshot['results']:
        raise Rejected('nothing_to_release', '没有可报告的组分，无法封存。', status=409)
    content = canonical(snapshot)
    fingerprint = digest(content)
    conn.execute('INSERT INTO snapshots (run_id, hash, content) VALUES (?, ?, ?)', (run_id, fingerprint, content))
    issued = issue_deliveries(conn, run_id, snapshot_row(conn, run_id))
    return {'hash': fingerprint, 'reused': False, 'deliveries': issued}


def replay(conn, run_id):
    snapshot = get_snapshot(conn, run_id)
    run = find_run(conn, run_id)
    if run['report']['engine'] != ENGINE:
        raise Rejected('engine_unavailable', '当前程序不含快照使用的计算版本。', status=409)
    input_hash = digest(canonical(run['source']))
    content = canonical(payload({**run, 'input_hash': input_hash}, analyze(run['source'])))
    actual = digest(content)
    return {'matches': input_hash == run['input_hash'] and actual == snapshot['hash'],
            'stored_sha256': snapshot['hash'], 'computed_sha256': actual, 'input_sha256': input_hash}
