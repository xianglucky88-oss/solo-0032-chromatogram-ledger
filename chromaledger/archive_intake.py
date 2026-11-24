"""Accept verified workstation backups into the central archive.

A record is identified by the fixed workstation identifier confirmed at
intake together with the original batch number. Every backup is one intake:
the whole backup lands only when every batch verifies against the station's
own inputs and no received material conflicts. Late, older copies can never
remove an already sealed release or another received batch.
"""
import hashlib

from .archive_database import connect
from .archive_verify import open_backup, verify_backup
from .encoding import canonical
from .errors import Rejected
from .fields import KEY


def bytes_digest(payload_bytes):
    return hashlib.sha256(payload_bytes).hexdigest()


def read_backup_file(path):
    try:
        return path.read_bytes()
    except OSError as exc:
        raise Rejected('backup_unreadable', '无法读取工作站备份文件。', status=400) from exc


def received_rows(conn, station_id):
    return {
        row['batch_id']: dict(row)
        for row in conn.execute('SELECT * FROM archive_runs WHERE station_id = ?', (station_id,))
    }


def received_snapshot(conn, station_id, batch_id):
    row = conn.execute(
        'SELECT hash, content FROM archive_snapshots WHERE station_id = ? AND batch_id = ?',
        (station_id, batch_id),
    ).fetchone()
    return dict(row) if row else None


def check_conflicts(verified, stored_runs, stored_snapshots, station_id):
    """Reject before writing anything: changed inputs or divergent seals."""
    for batch in verified:
        batch_id = batch['id']
        stored = stored_runs.get(batch_id)
        if stored is None:
            continue
        if stored['input_hash'] != batch['input_hash']:
            raise Rejected(
                'archive_input_conflict',
                f'工作站 {station_id} 的批次 {batch_id} 与已收输入不同，整份收件拒收。',
                status=409,
            )
        incoming = batch['snapshot']
        sealed = stored_snapshots.get(batch_id)
        if incoming is not None and sealed is not None and incoming['hash'] != sealed['hash']:
            raise Rejected(
                'archive_snapshot_conflict',
                f'工作站 {station_id} 的批次 {batch_id} 已入档封存出现不同内容，整份收件拒收。',
                status=409,
            )


def persist(conn, station_id, verified):
    added_batches, completed_seals = [], []
    for batch in verified:
        batch_id = batch['id']
        stored = stored_get(conn, station_id, batch_id)
        if stored is None:
            conn.execute('''
                INSERT INTO archive_runs
                    (station_id, batch_id, title, method_id, input_hash, source, report)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (station_id, batch_id, batch['title'], batch['method_id'],
                  batch['input_hash'], batch['source_text'], batch['report_text']))
            added_batches.append(batch_id)
        if batch['snapshot'] is not None and received_snapshot(conn, station_id, batch_id) is None:
            conn.execute('''
                INSERT INTO archive_snapshots (station_id, batch_id, hash, content)
                VALUES (?, ?, ?, ?)
            ''', (station_id, batch_id, batch['snapshot']['hash'], batch['snapshot']['content']))
            completed_seals.append(batch_id)
    return added_batches, completed_seals


def stored_get(conn, station_id, batch_id):
    return conn.execute(
        'SELECT batch_id FROM archive_runs WHERE station_id = ? AND batch_id = ?',
        (station_id, batch_id),
    ).fetchone()


def receive_backup(archive_db, station_id, backup_path):
    if not isinstance(station_id, str) or not KEY.fullmatch(station_id):
        raise Rejected('invalid_station',
                       '工作站标识须为 1–48 个 ASCII 字母、数字、点、横线或下划线。', status=400)
    station = station_id
    payload_bytes = read_backup_file(backup_path)
    backup_hash = bytes_digest(payload_bytes)
    source_conn = open_backup(backup_path)
    try:
        verified = verify_backup(source_conn)
    finally:
        source_conn.close()
    batch_ids = [batch['id'] for batch in verified]
    snapshot_ids = [batch['id'] for batch in verified if batch['snapshot'] is not None]
    with connect(archive_db) as conn:
        conn.execute('BEGIN IMMEDIATE')
        duplicate = conn.execute(
            'SELECT 1 FROM archive_receipts WHERE station_id = ? AND backup_hash = ?',
            (station, backup_hash),
        ).fetchone()
        stored_runs = received_rows(conn, station)
        stored_snapshots = {
            batch_id: snapshot
            for batch_id in stored_runs
            if (snapshot := received_snapshot(conn, station, batch_id)) is not None
        }
        check_conflicts(verified, stored_runs, stored_snapshots, station)
        added_batches, completed_seals = persist(conn, station, verified)
        conn.execute('''
            INSERT OR IGNORE INTO archive_receipts
                (station_id, backup_hash, batch_ids, run_count, snapshot_count)
            VALUES (?, ?, ?, ?, ?)
        ''', (station, backup_hash, canonical(batch_ids), len(batch_ids), len(snapshot_ids)))
    return {
        'station_id': station, 'backup_hash': backup_hash,
        'reused': duplicate is not None,
        'batch_ids': batch_ids, 'snapshot_ids': snapshot_ids,
        'added_batches': added_batches, 'completed_seals': completed_seals,
    }
