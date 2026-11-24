"""Intervals used to read computed time-window evidence."""


def corrected_bounds(target, shift=0):
    center = target['time'] + shift
    return {'min': center - target['radius'], 'max': center + target['radius']}


def marker_bounds(marker):
    return corrected_bounds(marker)


def window_evidence(method, alignment):
    shift = alignment['shift']
    return {
        'marker': marker_bounds(method['marker']),
        'targets': [{
            'id': target['id'], 'aligned': corrected_bounds(target),
            'raw': None if shift is None else corrected_bounds(target, shift),
        } for target in method['targets']],
    }
