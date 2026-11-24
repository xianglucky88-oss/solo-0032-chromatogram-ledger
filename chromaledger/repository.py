import json

from .errors import Rejected


def find_run(conn, run_id):
    row = conn.execute('SELECT * FROM runs WHERE id = ?', (run_id,)).fetchone()
    if row is None:
        raise Rejected('run_not_found', '没有该批次。', status=404)
    result = dict(row)
    result['source'] = json.loads(result['source'])
    result['report'] = json.loads(result['report'])
    return result


def catalog(conn, limit, offset):
    records = conn.execute('''
        SELECT r.id, r.title, r.method_id, r.created_at, r.input_hash, s.hash AS snapshot_hash,
               json_extract(r.report, '$.counts') AS counts
        FROM runs r LEFT JOIN snapshots s ON r.id = s.run_id
        ORDER BY r.created_at DESC, r.id LIMIT ? OFFSET ?
    ''', (limit + 1, offset)).fetchall()
    return {
        'items': [{**dict(record), 'counts': json.loads(record['counts'])} for record in records[:limit]],
        'next_offset': offset + limit if len(records) > limit else None,
    }


def snapshot_row(conn, run_id):
    row = conn.execute('SELECT * FROM snapshots WHERE run_id = ?', (run_id,)).fetchone()
    return dict(row) if row else None


def delivery_rows(conn, run_id):
    rows = conn.execute('SELECT * FROM deliveries WHERE run_id = ? ORDER BY sample_id', (run_id,)).fetchall()
    return [dict(row) for row in rows]


def delivery_row(conn, run_id, sample_id):
    row = conn.execute('SELECT * FROM deliveries WHERE run_id = ? AND sample_id = ?', (run_id, sample_id)).fetchone()
    return dict(row) if row else None
