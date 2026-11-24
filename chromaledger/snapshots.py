from .analysis import ENGINE, analyze
from .dual_end import ENGINE_DUAL
from .encoding import canonical, digest
from .errors import Rejected
from .repository import find_run, snapshot_row

CSV_FIELDS_V1 = ('sample_id', 'injection_id', 'order', 'target_id', 'concentration', 'dilution',
                 'peak_id', 'blank_id', 'qc_id')
CSV_FIELDS_V2 = CSV_FIELDS_V1 + ('qc_after_id', 'left_registration_id', 'right_registration_id')


def reportable_rows(report):
    dual = report.get('mode') == 'dual_end'
    for injection in report['injections']:
        if injection['kind'] != 'sample':
            continue
        for target_id, entry in injection['decisions'].items():
            if entry['status'] != 'reportable':
                continue
            row = {'sample_id': injection['sample_id'], 'injection_id': injection['id'],
                   'order': injection['order'], 'target_id': target_id, 'concentration': entry['reported'],
                   'dilution': injection['dilution'],
                   'peak_id': injection['assignments'][target_id]['ids'][0],
                   'blank_id': entry['blank_id'], 'qc_id': entry['qc_id']}
            if dual:
                registration_ids = entry['registration_ids']
                segment_index = entry['segment_index']
                row.update({
                    'qc_after_id': entry['qc_after_id'],
                    'left_registration_id': registration_ids[segment_index],
                    'right_registration_id': registration_ids[segment_index + 1],
                })
            yield row


def payload(run, report):
    dual = report.get('mode') == 'dual_end'
    base = {'run_id': run['id'], 'engine': report['engine'],
            'input_sha256': run['input_hash'], 'method': run['source']['method'],
            'excluded_count': report['counts']['held'], 'results': list(reportable_rows(report))}
    if dual:
        base['schema'] = 2
        base['calibration'] = {
            'mode': 'dual_end',
            'standard_groups': report['standard_groups'],
            'designated_checks': report['designated_checks'],
            'targets': {
                target_id: {
                    'selection_status': detail['selection']['status'],
                    'registration_ids': detail['selection'].get('chosen', {}).get('registration_ids'),
                }
                for target_id, detail in report['dual_calibration'].items()
            },
        }
    else:
        base['schema'] = 1
    return base


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
        return {'hash': row['hash'], 'reused': True}
    snapshot = payload(run, run['report'])
    if not snapshot['results']:
        raise Rejected('nothing_to_release', '没有可报告的组分，无法封存。', status=409)
    content = canonical(snapshot)
    fingerprint = digest(content)
    conn.execute('INSERT INTO snapshots (run_id, hash, content) VALUES (?, ?, ?)', (run_id, fingerprint, content))
    return {'hash': fingerprint, 'reused': False}


def replay(conn, run_id):
    snapshot = get_snapshot(conn, run_id)
    run = find_run(conn, run_id)
    if run['report']['engine'] not in (ENGINE, ENGINE_DUAL):
        raise Rejected('engine_unavailable', '当前程序不含快照使用的计算版本。', status=409)
    input_hash = digest(canonical(run['source']))
    content = canonical(payload({**run, 'input_hash': input_hash}, analyze(run['source'])))
    actual = digest(content)
    return {'matches': input_hash == run['input_hash'] and actual == snapshot['hash'],
            'stored_sha256': snapshot['hash'], 'computed_sha256': actual, 'input_sha256': input_hash}
