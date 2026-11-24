"""Versioned split-sample registers (分样清单).

A register records, for two accounted batches, which injections came from the
same mother sample and which handover-documented correspondences are allowed.
Every version is frozen with its own digest; a later version can exclude
relations after new handover evidence is retrieved while the earlier version
stays readable and keeps the conclusions drawn from it.
"""
import json
import re

from .encoding import canonical, digest
from .errors import Rejected
from .fields import array, integer, key, label, number, object_fields, require, unique
from .repository import find_run

MAX_VERSIONS = 100
SUPERSEDES = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,47}@[1-9][0-9]{0,3}\Z')


def parse_supersedes(value):
    require(isinstance(value, str) and SUPERSEDES.fullmatch(value), 'supersedes',
            '应为「清单编号@版本」形式')
    return value


def parse_relation(value, path):
    object_fields(value, path, ('left', 'right', 'source'))
    return {'left': key(value['left'], f'{path}.left'),
            'right': key(value['right'], f'{path}.right'),
            'source': label(value['source'], f'{path}.source')}


def parse_mother(value, index):
    path = f'mother_samples[{index}]'
    object_fields(value, path, ('id', 'source', 'left', 'right', 'relations'))
    mother = {
        'id': key(value['id'], f'{path}.id'),
        'source': label(value['source'], f'{path}.source'),
        'left': [key(item, f'{path}.left') for item in array(value['left'], f'{path}.left', 1, 64)],
        'right': [key(item, f'{path}.right') for item in array(value['right'], f'{path}.right', 1, 64)],
        'relations': [parse_relation(row, f'{path}.relations[{position}]')
                      for position, row in enumerate(array(value['relations'], f'{path}.relations', 1, 4096))],
    }
    unique(mother['left'], f'{path}.left')
    unique(mother['right'], f'{path}.right')
    pairs = [(relation['left'], relation['right']) for relation in mother['relations']]
    unique(pairs, f'{path}.relations')
    left, right = set(mother['left']), set(mother['right'])
    for relation in mother['relations']:
        require(relation['left'] in left and relation['right'] in right, f'{path}.relations',
                '对应关系只能落在本母样两侧的进样内')
    return mother


def parse_register(value):
    object_fields(value, '分样清单', ('id', 'version', 'left_run', 'right_run', 'allowed_exceedances',
                                   'limits', 'mother_samples'), ('supersedes',))
    register = {
        'id': key(value['id'], 'id'),
        'version': integer(value['version'], 'version', 1, 1000),
        'left_run': key(value['left_run'], 'left_run'),
        'right_run': key(value['right_run'], 'right_run'),
        'supersedes': None if value.get('supersedes') is None else parse_supersedes(value['supersedes']),
        'allowed_exceedances': integer(value['allowed_exceedances'], 'allowed_exceedances', 0, 10000),
        'mother_samples': [parse_mother(row, index)
                           for index, row in enumerate(array(value['mother_samples'], 'mother_samples', 1, 64))],
    }
    require(register['left_run'] != register['right_run'], '分样清单', '两侧批次须为不同批次')
    unique([mother['id'] for mother in register['mother_samples']], 'mother_samples.id')

    used = {}
    for mother in register['mother_samples']:
        for side, injections in (('left', mother['left']), ('right', mother['right'])):
            for injection in injections:
                slot = (side, injection)
                require(slot not in used, 'mother_samples',
                        f'{side} 侧进样 {injection} 已被母样 {used.get(slot)} 引用，一针只能登记一次')
                used[slot] = mother['id']

    limits = value['limits']
    require(isinstance(limits, dict), 'limits', '应为对象')
    register['limits'] = {key(target, 'limits'): number(limit, f'limits.{target}', 0, 1e9, exclusive=True)
                          for target, limit in limits.items()}
    return register


def validate_scope(register, supplied_run, left_run, right_run):
    """Check the register's batches and injections against the accounted runs."""
    if {register['left_run'], register['right_run']} != {left_run['id'], right_run['id']}:
        raise Rejected('register_mismatch', '分样清单须只涉及所选两个批次。', status=409)
    if supplied_run not in (left_run['id'], right_run['id']):
        raise Rejected('register_mismatch', '分样清单不属于当前批次。', status=409)
    byteam = {left_run['id']: left_run, right_run['id']: right_run}
    method = left_run['source']['method']
    if right_run['source']['method'] != method or right_run['report']['engine'] != left_run['report']['engine']:
        raise Rejected('incompatible_runs', '分样清单要求两个批次的方法内容与计算版本相容。', status=409)
    targets = [target['id'] for target in method['targets']]
    require(set(register['limits']) == set(targets), 'limits', '差限须覆盖方法的全部组分且不得包含其他组分')

    def sample_row(run_id, injection_id, where):
        row = next((item for item in byteam[run_id]['report']['injections'] if item['id'] == injection_id), None)
        require(row is not None, where, f'批次 {run_id} 没有进样 {injection_id}')
        require(row['kind'] == 'sample', where, f'进样 {injection_id} 不是复核待测样')
        return row

    for mother in register['mother_samples']:
        for injection in mother['left']:
            sample_row(register['left_run'], injection, f'mother_samples.{mother["id"]}.left')
        for injection in mother['right']:
            sample_row(register['right_run'], injection, f'mother_samples.{mother["id"]}.right')
    return targets


def _prior(conn, register_id):
    return conn.execute('''
        SELECT * FROM registers WHERE register_id = ? ORDER BY version DESC LIMIT 1
    ''', (register_id,)).fetchone()


def create(conn, supplied_run, value):
    register = parse_register(value)
    left_run = find_run(conn, register['left_run'])
    right_run = find_run(conn, register['right_run'])
    validate_scope(register, supplied_run, left_run, right_run)

    conn.execute('BEGIN IMMEDIATE')
    prior = _prior(conn, register['id'])
    if register['version'] == 1:
        if prior is not None:
            raise Rejected('register_conflict', '该分样清单已有登记的版本。', status=409)
        require(register['supersedes'] is None, 'supersedes', '首版清单不得声明前版')
    else:
        if prior is None:
            raise Rejected('register_conflict', '没有可续版的分样清单。', status=409)
        require(register['version'] == prior['version'] + 1, 'version', '新版本须紧接上一版')
        require(register['supersedes'] == prior['id'], 'supersedes', '新版本须声明所取代的前一版')
        require((register['left_run'], register['right_run']) == (prior['left_run'], prior['right_run']),
                '分样清单', '续版不得更换两侧批次')
        prior_register = json.loads(prior['content'])
        require(register['limits'] == prior_register['limits']
                and register['allowed_exceedances'] == prior_register['allowed_exceedances'],
                '分样清单', '续版不得改变差限或容许超差对数')
        require({mother['id'] for mother in register['mother_samples']}
                == {mother['id'] for mother in prior_register['mother_samples']},
                'mother_samples', '续版不得增删母样')
        for mother in register['mother_samples']:
            previous = next(item for item in prior_register['mother_samples'] if item['id'] == mother['id'])
            require(set(mother['left']) == set(previous['left']) and set(mother['right']) == set(previous['right']),
                    'mother_samples', f'母样 {mother["id"]} 的进样不得在续版中改变')
            allowed = {(relation['left'], relation['right']) for relation in previous['relations']}
            kept = {(relation['left'], relation['right']) for relation in mother['relations']}
            require(kept <= allowed, 'mother_samples',
                    f'续版只能依据新凭据排除母样 {mother["id"]} 的对应关系，不得新增')
    existing = conn.execute('SELECT id FROM registers WHERE id = ?', (f'{register["id"]}@{register["version"]}',)).fetchone()
    if existing is not None:
        raise Rejected('register_conflict', '该版本已经登记。', status=409)

    content = canonical(register)
    row_id = f'{register["id"]}@{register["version"]}'
    fingerprint = digest(content)
    conn.execute('''
        INSERT INTO registers (id, register_id, version, left_run, right_run, supersedes, hash, content)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (row_id, register['id'], register['version'], register['left_run'], register['right_run'],
          prior['id'] if prior is not None else None, fingerprint, content))
    stored = conn.execute('SELECT created_at FROM registers WHERE id = ?', (row_id,)).fetchone()
    return {'id': register['id'], 'version': register['version'], 'hash': fingerprint,
            'supersedes': prior['id'] if prior is not None else None, 'created_at': stored['created_at']}


def fetch(conn, register_id, version, run_id=None):
    if version is None:
        row = conn.execute('''
            SELECT * FROM registers WHERE register_id = ? ORDER BY version DESC LIMIT 1
        ''', (register_id,)).fetchone()
    else:
        row = conn.execute('SELECT * FROM registers WHERE register_id = ? AND version = ?',
                           (register_id, version)).fetchone()
    if row is None:
        raise Rejected('register_not_found', '没有该分样清单版本。', status=404)
    if run_id is not None and run_id not in (row['left_run'], row['right_run']):
        raise Rejected('register_mismatch', '分样清单不属于该批次。', status=409)
    return row


def list_for_run(conn, run_id):
    rows = conn.execute('''
        SELECT * FROM registers WHERE left_run = ? OR right_run = ?
        ORDER BY register_id, version
    ''', (run_id, run_id)).fetchall()
    grouped = {}
    for row in rows:
        item = grouped.setdefault(row['register_id'], {
            'register_id': row['register_id'], 'left_run': row['left_run'], 'right_run': row['right_run'],
            'versions': []})
        item['versions'].append({'version': row['version'], 'hash': row['hash'], 'created_at': row['created_at'],
                                 'supersedes': row['supersedes'],
                                 'allowed_exceedances': json.loads(row['content'])['allowed_exceedances'],
                                 'mother_count': len(json.loads(row['content'])['mother_samples'])})
    return {'items': list(grouped.values())}
