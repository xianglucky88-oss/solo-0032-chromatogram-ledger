def within(value, center, radius):
    return abs(value - center) <= radius + 1e-9


def locate_marker(peaks, profile):
    candidates = [p for p in peaks if within(p['time'], profile['time'], profile['radius']) and p['area'] >= profile['minimum_area']]
    evidence = {'candidates': [p['id'] for p in candidates], 'shift': None, 'peak_id': None}
    if len(candidates) > 1:
        return {**evidence, 'status': 'marker_ambiguous'}
    if not candidates:
        return {**evidence, 'status': 'marker_missing'}
    peak = candidates[0]
    if peak['saturated']:
        return {**evidence, 'status': 'marker_saturated'}
    return {**evidence, 'status': 'aligned', 'shift': peak['time'] - profile['time'], 'peak_id': peak['id']}
