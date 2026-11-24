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


def parse_document(value):
    object_fields(value, '批次', ('id', 'label', 'method', 'injections'))
    method = parse_method(value['method'])
    ids = [target['id'] for target in method['targets']]
    seq = [parse_injection(row, index, ids) for index, row in enumerate(array(value['injections'], 'injections', 1, 128))]
    unique([row['id'] for row in seq], 'injections.id')
    unique([row['order'] for row in seq], 'injections.order')
    seq.sort(key=lambda row: row['order'])
    first_unknown = next((i for i, row in enumerate(seq) if row['kind'] != 'standard'), len(seq))
    require(all(row['kind'] != 'standard' for row in seq[first_unknown:]), 'injections', '标准样须组成序列的开头区段')
    return {'id': key(value['id'], 'id'), 'label': label(value['label'], 'label'), 'method': method, 'injections': seq}
