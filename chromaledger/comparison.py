from .errors import Rejected
from .repository import find_run


def sample_results(run):
    return {
        (row['sample_id'], row['id'], target): {'status': entry['status'], 'value': entry['reported'],
                                               'issues': entry['issues']}
        for row in run['report']['injections'] if row['kind'] == 'sample'
        for target, entry in row['decisions'].items()
    }


def compare(conn, left_id, right_id):
    left, right = find_run(conn, left_id), find_run(conn, right_id)
    if left['source']['method'] != right['source']['method'] or left['report']['engine'] != right['report']['engine']:
        raise Rejected('incompatible_runs', '批次对比要求相同的方法内容及计算版本。', status=409)
    lrows, rrows = sample_results(left), sample_results(right)
    changes = []
    unchanged = 0
    for identity in sorted(lrows.keys() | rrows.keys()):
        before, after = lrows.get(identity), rrows.get(identity)
        if before == after:
            unchanged += 1
            continue
        sample, injection, target = identity
        changes.append({'sample_id': sample, 'injection_id': injection, 'target_id': target,
                        'before': before, 'after': after})
    return {'left': left_id, 'right': right_id, 'changes': changes, 'unchanged': unchanged}
