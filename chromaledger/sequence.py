from .fields import array, integer, key, label, number, object_fields, require, unique
from .method import parse_method
from .peaks import parse_peaks

KINDS = ('standard', 'blank', 'qc', 'sample')


def parse_expected(value, path, target_ids):
    object_fields(value, path, target_ids)
    return {target: number(value[target], f'{path}.{target}', 1e-6, 1e6) for target in sorted(target_ids)}


def parse_injection(value, index, target_ids):
    path = f'injections[{index}]'
    object_fields(value, path, ('id', 'order', 'kind', 'peaks'), ('expected', 'sample_id', 'dilution'))
    kind = value['kind']
    require(kind in KINDS, f'{path}.kind', '无法识别进样类型')
    row = {
        'id': key(value['id'], f'{path}.id'),
        'order': integer(value['order'], f'{path}.order', 1, 10000),
        'kind': kind,
        'peaks': parse_peaks(value['peaks'], f'{path}.peaks'),
    }
    measured_fields = value.keys() & {'expected', 'sample_id', 'dilution'}
    if kind in ('standard', 'qc'):
        require(measured_fields == {'expected'}, path, '标准样和核查样须且只允许提供组分标称值')
        row['expected'] = parse_expected(value['expected'], f'{path}.expected', target_ids)
    elif kind == 'sample':
        require('sample_id' in measured_fields and 'expected' not in measured_fields, path, '待测样须提供样品编号，可提供稀释倍数')
        row['sample_id'] = key(value['sample_id'], f'{path}.sample_id')
        row['dilution'] = number(value.get('dilution', 1), f'{path}.dilution', 1, 1000)
    else:
        require(not measured_fields, path, '空白不得提供标称值或样品属性')
    return row


def split_standard_groups(seq):
    """把按 order 排好的进样序列切成连续的标准样组，返回每组的标准进样列表。"""
    groups = []
    current = []
    saw_non_standard = False
    for row in seq:
        if row['kind'] == 'standard':
            require(bool(current) or not groups or saw_non_standard, 'injections', '标准组之间须由非标准进样分隔')
            current.append(row)
            saw_non_standard = False
        else:
            if current:
                groups.append(current)
                current = []
            saw_non_standard = True
    if current:
        groups.append(current)
    return groups


def parse_registration_levels(value, path, target_ids):
    object_fields(value, path, (), target_ids)
    require(set(value) <= target_ids, path, '登记标称只能引用方法组分')
    return {target: number(value[target], f'{path}.{target}', 1e-6, 1e6) for target in sorted(value)}


def parse_registration(value, index, target_ids, standard_ids, path_name):
    path = f'{path_name}[{index}]'
    object_fields(value, path, ('id', 'levels'))
    registration_id = key(value['id'], f'{path}.id')
    levels_path = f'{path}.levels'
    object_fields(value['levels'], levels_path, (), standard_ids)
    levels = {}
    for injection_id in sorted(value['levels']):
        levels[injection_id] = parse_registration_levels(
            value['levels'][injection_id], f'{levels_path}.{injection_id}', target_ids)
    return {'id': registration_id, 'levels': levels}


def parse_dual_end(value, target_ids, groups):
    path = 'dual_end'
    object_fields(value, path, ('registrations', 'designated_checks'))
    standard_ids = {row['id'] for group in groups for row in group}
    registrations = [parse_registration(row, index, target_ids, standard_ids, f'{path}.registrations')
                     for index, row in enumerate(array(value['registrations'], f'{path}.registrations', 1, 32))]
    registration_ids = [row['id'] for row in registrations]
    unique(registration_ids, f'{path}.registrations')
    checks_value = array(value['designated_checks'], f'{path}.designated_checks', 0, 64)
    designated_checks = [key(item, f'{path}.designated_checks[{index}]') for index, item in enumerate(checks_value)]
    unique(designated_checks, f'{path}.designated_checks')
    return {'registrations': registrations, 'designated_checks': designated_checks}


def parse_document(value):
    object_fields(value, '批次', ('id', 'label', 'method', 'injections'), ('dual_end',))
    method = parse_method(value['method'])
    ids = [target['id'] for target in method['targets']]
    seq = [parse_injection(row, index, ids) for index, row in enumerate(array(value['injections'], 'injections', 1, 128))]
    unique([row['id'] for row in seq], 'injections.id')
    unique([row['order'] for row in seq], 'injections.order')
    seq.sort(key=lambda row: row['order'])
    dual_end = None
    if 'dual_end' in value:
        groups = split_standard_groups(seq)
        require(len(groups) >= 2, 'injections', '双端校准至少需要两组标准样')
        require(all(len(group) >= 3 for group in groups), 'injections', '每个标准组至少需要三次标准进样')
        dual_end = parse_dual_end(value['dual_end'], set(ids), groups)
        qc_by_id = {row['id']: row for row in seq if row['kind'] == 'qc'}
        standard_positions = [row['order'] for group in groups for row in group]
        first_order = min(standard_positions)
        last_order = max(standard_positions)
        for check_id in dual_end['designated_checks']:
            require(check_id in qc_by_id, 'dual_end.designated_checks', f'{check_id} 不是核查样进样')
            order = qc_by_id[check_id]['order']
            require(first_order < order < last_order, 'dual_end.designated_checks',
                    '指定核查须落在首末标准组之间')
    else:
        first_unknown = next((i for i, row in enumerate(seq) if row['kind'] != 'standard'), len(seq))
        require(all(row['kind'] != 'standard' for row in seq[first_unknown:]), 'injections', '标准样须组成序列的开头区段')
    document = {'id': key(value['id'], 'id'), 'label': label(value['label'], 'label'),
                'method': method, 'injections': seq}
    if dual_end is not None:
        document['dual_end'] = dual_end
    return document
