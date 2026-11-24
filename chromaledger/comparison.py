import json

from .errors import Rejected
from .interchange import evaluate
from .repository import find_run


def sample_results(run):
    return {
        (row['sample_id'], row['id'], target): {'status': entry['status'], 'value': entry['reported'],
                                               'issues': entry['issues']}
        for row in run['report']['injections'] if row['kind'] == 'sample'
        for target, entry in row['decisions'].items()
    }


def routine(left, right):
    """Regular sample-component difference between two comparable batches."""
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
    return changes, unchanged


def compare(conn, left_id, right_id, register=None):
    left, right = find_run(conn, left_id), find_run(conn, right_id)
    if left['source']['method'] != right['source']['method'] or left['report']['engine'] != right['report']['engine']:
        raise Rejected('incompatible_runs', '批次对比要求相同的方法内容及计算版本。', status=409)
    changes, unchanged = routine(left, right)
    result = {'left': left_id, 'right': right_id, 'changes': changes, 'unchanged': unchanged}
    if register is not None:
        metadata = json.loads(register['content'])
        if {register['left_run'], register['right_run']} != {left_id, right_id}:
            raise Rejected('register_mismatch', '所选分样清单不属于这两个批次。', status=409)
        runs = {left['id']: left, right['id']: right}
        left_run, right_run = runs[metadata['left_run']], runs[metadata['right_run']]
        result['register'] = {'id': metadata['id'], 'version': metadata['version'],
                              'hash': register['hash'], 'supersedes': metadata.get('supersedes')}
        result['interchange'] = evaluate(metadata, left_run, right_run)
    return result
