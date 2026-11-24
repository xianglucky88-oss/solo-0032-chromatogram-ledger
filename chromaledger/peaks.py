from .fields import array, key, number, object_fields, require, unique


def parse_peak(value, path):
    object_fields(value, path, ('id', 'time', 'area'), ('saturated',))
    saturated = value.get('saturated', False)
    require(type(saturated) is bool, f'{path}.saturated', '应为布尔值')
    return {
        'id': key(value['id'], f'{path}.id'),
        'time': number(value['time'], f'{path}.time', 0.001, 180),
        'area': number(value['area'], f'{path}.area', 1e-6, 1e9),
        'saturated': saturated,
    }


def parse_peaks(value, path):
    peaks = [parse_peak(peak, f'{path}[{index}]') for index, peak in enumerate(array(value, path, 0, 192))]
    unique([peak['id'] for peak in peaks], path)
    return sorted(peaks, key=lambda peak: (peak['time'], peak['id']))
