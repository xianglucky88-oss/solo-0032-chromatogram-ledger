from .errors import Rejected
from .repository import find_run, snapshot_row
from .windows import window_evidence


def report_view(conn, run_id):
    run = find_run(conn, run_id)
    snapshot = snapshot_row(conn, run_id)
    return {
        'id': run['id'], 'title': run['title'], 'method': run['source']['method'],
        'input_hash': run['input_hash'], 'created_at': run['created_at'],
        'snapshot': {'hash': snapshot['hash'], 'created_at': snapshot['created_at']} if snapshot else None,
        **run['report'],
    }


def injection_trace(conn, run_id, injection_id):
    run = find_run(conn, run_id)
    source = next((row for row in run['source']['injections'] if row['id'] == injection_id), None)
    if source is None:
        raise Rejected('injection_not_found', '没有该进样。', status=404)
    computed = next(row for row in run['report']['injections'] if row['id'] == injection_id)
    return {'source': source, 'result': computed, 'windows': window_evidence(run['source']['method'], computed['alignment'])}
