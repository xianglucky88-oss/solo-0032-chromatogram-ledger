"""Read model for archived workstation batches."""
import json

from .analysis import ENGINE, analyze
from .encoding import canonical, digest
from .errors import Rejected
from .windows import window_evidence


def _not_found(station_id, batch_id):
    return Rejected(
        'archive_record_not_found',
        f'工作站 {station_id} 没有已收档的批次 {batch_id}。',
        status=404,
    )


def find_archive_run(conn, station_id, batch_id):
    row = conn.execute(
        'SELECT * FROM archive_runs WHERE station_id = ? AND batch_id = ?',
        (station_id, batch_id),
    ).fetchone()
    if row is None:
        raise _not_found(station_id, batch_id)
    result = dict(row)
    result['source'] = json.loads(result['source'])
    result['report'] = json.loads(result['report'])
    return result


def archive_snapshot_row(conn, station_id, batch_id):
    row = conn.execute(
        'SELECT * FROM archive_snapshots WHERE station_id = ? AND batch_id = ?',
        (station_id, batch_id),
    ).fetchone()
    return dict(row) if row else None


def get_archive_snapshot(conn, station_id, batch_id):
    row = archive_snapshot_row(conn, station_id, batch_id)
    if row is None:
        raise Rejected('snapshot_not_found', '该归档批次尚无交付快照。', status=404)
    if digest(row['content']) != row['hash']:
        raise Rejected('snapshot_corrupt', '快照内容与摘要不一致。', status=409)
    return row


def archive_catalog(conn, station_id=None, limit=40, offset=0):
    where = 'WHERE r.station_id = ?' if station_id else ''
    params = [station_id] if station_id else []
    rows = conn.execute(f'''
        SELECT r.station_id, r.batch_id, r.title, r.method_id, r.received_at,
               r.input_hash, s.hash AS snapshot_hash,
               json_extract(r.report, '$.counts') AS counts
        FROM archive_runs r LEFT JOIN archive_snapshots s
             ON r.station_id = s.station_id AND r.batch_id = s.batch_id
        {where}
        ORDER BY r.received_at DESC, r.station_id, r.batch_id
        LIMIT ? OFFSET ?
    ''', [*params, limit + 1, offset]).fetchall()
    items = [{**dict(row), 'counts': json.loads(row['counts'])} for row in rows[:limit]]
    return {'items': items, 'next_offset': offset + limit if len(rows) > limit else None}


def list_stations(conn):
    rows = conn.execute('''
        SELECT r.station_id,
               COUNT(r.batch_id) AS batch_count,
               COUNT(s.batch_id) AS snapshot_count,
               MAX(r.received_at) AS last_received_at
        FROM archive_runs r
        LEFT JOIN archive_snapshots s
               ON r.station_id = s.station_id AND r.batch_id = s.batch_id
        GROUP BY r.station_id
        ORDER BY r.station_id
    ''').fetchall()
    return {'items': [dict(row) for row in rows]}


def list_receipts(conn, station_id):
    rows = conn.execute('''
        SELECT station_id, backup_hash, batch_ids, run_count, snapshot_count, received_at
        FROM archive_receipts WHERE station_id = ?
        ORDER BY received_at, backup_hash
    ''', (station_id,)).fetchall()
    if not rows:
        raise Rejected('archive_station_not_found', f'没有工作站 {station_id} 的收件记录。', status=404)
    return {'items': [{**dict(row), 'batch_ids': json.loads(row['batch_ids'])} for row in rows]}


def archive_report_view(conn, station_id, batch_id):
    run = find_archive_run(conn, station_id, batch_id)
    snapshot = archive_snapshot_row(conn, station_id, batch_id)
    return {
        'station_id': station_id, 'id': batch_id, 'title': run['title'],
        'method': run['source']['method'], 'input_hash': run['input_hash'],
        'received_at': run['received_at'],
        'snapshot': {'hash': snapshot['hash'], 'received_at': snapshot['received_at']} if snapshot else None,
        **run['report'],
    }


def archive_source_view(conn, station_id, batch_id):
    return find_archive_run(conn, station_id, batch_id)['source']


def archive_trace(conn, station_id, batch_id, injection_id):
    run = find_archive_run(conn, station_id, batch_id)
    source = next((row for row in run['source']['injections'] if row['id'] == injection_id), None)
    if source is None:
        raise Rejected('injection_not_found', '没有该进样。', status=404)
    computed = next(row for row in run['report']['injections'] if row['id'] == injection_id)
    return {'source': source, 'result': computed,
            'windows': window_evidence(run['source']['method'], computed['alignment'])}


def replay_archive(conn, station_id, batch_id):
    snapshot = get_archive_snapshot(conn, station_id, batch_id)
    run = find_archive_run(conn, station_id, batch_id)
    if run['report']['engine'] != ENGINE:
        raise Rejected('engine_unavailable', '当前程序不含快照使用的计算版本。', status=409)
    input_hash = digest(canonical(run['source']))
    from .snapshots import payload
    content = canonical(payload({**run, 'id': batch_id, 'input_hash': input_hash}, analyze(run['source'])))
    actual = digest(content)
    return {
        'station_id': station_id, 'batch_id': batch_id,
        'matches': input_hash == run['input_hash'] and actual == snapshot['hash'],
        'stored_sha256': snapshot['hash'], 'computed_sha256': actual, 'input_sha256': input_hash,
    }
