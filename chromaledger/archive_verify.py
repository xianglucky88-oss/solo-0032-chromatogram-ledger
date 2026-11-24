"""Independent verification of an offline workstation SQLite backup.

Reports and sealed releases are recomputed from the workstation's original
inputs. Internal digests stored inside the backup are only compared after the
recomputation succeeds, so a file whose hashes merely match each other can
never pass on its own.
"""
import json
import sqlite3

from .analysis import ENGINE, analyze
from .encoding import canonical, digest
from .errors import Rejected
from .sequence import parse_document
from .snapshots import payload


def open_backup(path):
    """Open a workstation backup read-only; nothing here touches its pages."""
    try:
        uri = str(path.resolve().as_uri()) + '?mode=ro'
        conn = sqlite3.connect(uri, uri=True, timeout=5)
    except (OSError, sqlite3.Error) as exc:
        raise Rejected('backup_unreadable', '无法只读打开工作站备份。', status=400) from exc
    conn.row_factory = sqlite3.Row
    return conn


def check_backup_structure(conn):
    try:
        structural = [row[0] for row in conn.execute('PRAGMA quick_check') if row[0] != 'ok']
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        orphaned = conn.execute('PRAGMA foreign_key_check').fetchall()
    except sqlite3.DatabaseError as exc:
        raise Rejected('backup_unreadable', '备份不是可读取的 SQLite 数据库。', status=400) from exc
    if structural:
        raise Rejected('backup_corrupt', '备份数据库未通过完整性检查。', status=422)
    missing = {'runs', 'snapshots'} - tables
    if missing:
        raise Rejected('backup_unreadable', '备份缺少 runs 或 snapshots 表。', status=422)
    expected = {
        'runs': {'id', 'title', 'method_id', 'input_hash', 'source', 'report'},
        'snapshots': {'run_id', 'hash', 'content'},
    }
    for table, columns in expected.items():
        actual = {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}
        if not columns <= actual:
            raise Rejected('backup_unreadable', f'备份的 {table} 表结构不完整。', status=422)
    if orphaned:
        raise Rejected('backup_verification_failed', '备份中存在失去批次的封存件。', status=422)


def load_backup(conn):
    runs = conn.execute('SELECT id, title, method_id, input_hash, source, report FROM runs ORDER BY id').fetchall()
    if not runs:
        raise Rejected('backup_empty', '备份中没有任何批次。', status=422)
    ids = [row['id'] for row in runs]
    if len(set(ids)) != len(ids):
        raise Rejected('backup_verification_failed', '备份内批次编号重复。', status=422)
    snapshots = {
        row['run_id']: dict(row)
        for row in conn.execute('SELECT run_id, hash, content FROM snapshots ORDER BY run_id')
    }
    unknown_snapshot = set(snapshots) - set(ids)
    if unknown_snapshot:
        raise Rejected('backup_verification_failed', '备份中存在失去批次的封存件。', status=422)
    return [dict(row) for row in runs], snapshots


def verify_run(run, stored_snapshot):
    """Recompute one workstation batch from its original input."""
    run_id = run['id']
    try:
        source = parse_document(json.loads(run['source']))
        report = json.loads(run['report'])
    except (ValueError, RecursionError, Rejected) as exc:
        raise Rejected('backup_verification_failed', f'批次 {run_id} 的输入或报告无法解析。', status=422) from exc
    if canonical(source) != run['source']:
        raise Rejected('backup_verification_failed', f'批次 {run_id} 的输入不是规范化编码。', status=422)
    if source['id'] != run_id or source['label'] != run['title'] or source['method']['id'] != run['method_id']:
        raise Rejected('backup_verification_failed', f'批次 {run_id} 的目录信息与输入不一致。', status=422)
    input_hash = digest(canonical(source))
    if input_hash != run['input_hash']:
        raise Rejected('backup_verification_failed', f'批次 {run_id} 的输入摘要无法由原输入复算。', status=422)
    if not isinstance(report, dict) or report.get('engine') != ENGINE:
        raise Rejected('backup_verification_failed', f'批次 {run_id} 使用当前程序不具备的计算版本。', status=422)
    recomputed = analyze(source)
    if canonical(recomputed) != run['report']:
        raise Rejected('backup_verification_failed', f'批次 {run_id} 的报告无法由原输入复算。', status=422)
    snapshot = None
    if stored_snapshot is not None:
        snapshot = verify_snapshot(run, source, recomputed, stored_snapshot)
    return {
        'id': run_id, 'title': run['title'], 'method_id': run['method_id'],
        'input_hash': input_hash, 'source_text': run['source'], 'report_text': run['report'],
        'snapshot': snapshot,
    }


def verify_snapshot(run, source, report, stored):
    run_id = run['id']
    try:
        content = json.loads(stored['content'])
    except (ValueError, RecursionError) as exc:
        raise Rejected('backup_verification_failed', f'批次 {run_id} 的封存件无法解析。', status=422) from exc
    if not isinstance(content, dict) or content.get('run_id') != run_id:
        raise Rejected('backup_verification_failed', f'批次 {run_id} 的封存件归属错误。', status=422)
    if digest(stored['content']) != stored['hash']:
        raise Rejected('backup_verification_failed', f'批次 {run_id} 的封存摘要无法由封存件复算。', status=422)
    expected = canonical(payload({**run, 'source': source}, report))
    if expected != stored['content']:
        raise Rejected('backup_verification_failed', f'批次 {run_id} 的封存件无法由原输入与报告复算。', status=422)
    return {'hash': stored['hash'], 'content': stored['content']}


def verify_backup(conn):
    check_backup_structure(conn)
    runs, snapshots = load_backup(conn)
    verified = []
    for run in runs:
        verified.append(verify_run(run, snapshots.get(run['id'])))
    return verified
