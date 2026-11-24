from .fields import array, key, label, number, object_fields, require, unique


def parse_marker(value):
    path = 'method.marker'
    object_fields(value, path, ('time', 'radius', 'minimum_area'))
    marker = {
        'time': number(value['time'], f'{path}.time', 0.01, 120),
        'radius': number(value['radius'], f'{path}.radius', 0.001, 2),
        'minimum_area': number(value['minimum_area'], f'{path}.minimum_area', 0, 1e9, exclusive=True),
    }
    require(marker['time'] > marker['radius'], path, '定位窗下界须为正')
    return marker


def parse_target(value, index):
    path = f'method.targets[{index}]'
    object_fields(value, path, ('id', 'label', 'time', 'radius', 'blank_limit'))
    target = {
        'id': key(value['id'], f'{path}.id'),
        'label': label(value['label'], f'{path}.label'),
        'time': number(value['time'], f'{path}.time', 0.01, 120),
        'radius': number(value['radius'], f'{path}.radius', 0.001, 1),
        'blank_limit': number(value['blank_limit'], f'{path}.blank_limit', 0, 1e9),
    }
    require(target['time'] > target['radius'], path, '组分窗下界须为正')
    return target


def check_windows(marker, targets):
    # On the aligned axis, the selected marker is always exactly at its nominal time.
    intervals = sorted((t['time'] - t['radius'], t['time'] + t['radius']) for t in targets)
    for left, right in zip(intervals, intervals[1:]):
        require(right[0] - left[1] > 1e-9, 'method.targets', '组分保留时间窗不得相交或相接')
    require(all(not lo - 1e-9 <= marker['time'] <= hi + 1e-9 for lo, hi in intervals), 'method.targets', '定位峰标称位置与组分窗重叠')


def parse_method(value):
    object_fields(value, 'method', ('id', 'revision', 'marker', 'targets', 'qc_tolerance', 'min_r2'))
    marker = parse_marker(value['marker'])
    targets = [parse_target(row, index) for index, row in enumerate(array(value['targets'], 'method.targets', 1, 16))]
    unique([row['id'] for row in targets], 'method.targets')
    check_windows(marker, targets)
    return {
        'id': key(value['id'], 'method.id'),
        'revision': key(value['revision'], 'method.revision'),
        'marker': marker,
        'targets': sorted(targets, key=lambda row: row['id']),
        'qc_tolerance': number(value['qc_tolerance'], 'method.qc_tolerance', 0, 0.5, exclusive=True),
        'min_r2': number(value['min_r2'], 'method.min_r2', 0.8, 1),
    }
