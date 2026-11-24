from math import fsum
from statistics import mean


def fit_line(points, minimum_r2):
    levels = sorted({row['level'] for row in points})
    outcome = {'points': points, 'levels': levels, 'slope': None, 'intercept': None, 'r2': None}
    if len(levels) < 3:
        return {**outcome, 'status': 'insufficient_levels'}
    x = mean(row['level'] for row in points)
    y = mean(row['area'] for row in points)
    spread = fsum((row['level'] - x) ** 2 for row in points)
    covariance = fsum((row['level'] - x) * (row['area'] - y) for row in points)
    slope = covariance / spread
    intercept = y - slope * x
    outcome.update(slope=slope, intercept=intercept)
    if slope <= 0:
        return {**outcome, 'status': 'non_positive_slope'}
    variance = fsum((row['area'] - y) ** 2 for row in points)
    residual = fsum((row['area'] - (slope * row['level'] + intercept)) ** 2 for row in points)
    r2 = 1 - residual / variance
    outcome['r2'] = r2
    return {**outcome, 'status': 'ready' if r2 >= minimum_r2 else 'poor_fit'}


def fit_target(standards, mapped, target_id, minimum_r2):
    points = []
    excluded = []
    for row in standards:
        assignment = mapped[row['id']]['assignments'][target_id]
        point = {'injection_id': row['id'], 'level': row['expected'][target_id], 'peak_ids': assignment['ids']}
        if assignment['status'] == 'matched':
            points.append({**point, 'area': assignment['area']})
        else:
            excluded.append({**point, 'reason': assignment['status']})
    return {**fit_line(points, minimum_r2), 'excluded': excluded}


def fit_sequence(injections, mapped, method):
    standards = [row for row in injections if row['kind'] == 'standard']
    return {target['id']: fit_target(standards, mapped, target['id'], method['min_r2']) for target in method['targets']}
