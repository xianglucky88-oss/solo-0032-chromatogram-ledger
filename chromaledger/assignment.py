from .alignment import locate_marker, within


def assign_target(peaks, alignment, target):
    base = {'ids': [], 'area': None, 'aligned_time': None}
    if alignment['status'] != 'aligned':
        return {**base, 'status': 'unaligned'}
    shift = alignment['shift']
    candidates = [p for p in peaks if p['id'] != alignment['peak_id'] and within(p['time'] - shift, target['time'], target['radius'])]
    base['ids'] = [p['id'] for p in candidates]
    if len(candidates) > 1:
        return {**base, 'status': 'ambiguous'}
    if not candidates:
        return {**base, 'status': 'missing'}
    peak = candidates[0]
    return {
        **base, 'status': 'saturated' if peak['saturated'] else 'matched',
        'area': peak['area'], 'aligned_time': peak['time'] - shift,
    }


def map_injection(injection, method):
    peaks = injection['peaks']
    alignment = locate_marker(peaks, method['marker'])
    return {
        'alignment': alignment,
        'assignments': {target['id']: assign_target(peaks, alignment, target) for target in method['targets']},
    }
