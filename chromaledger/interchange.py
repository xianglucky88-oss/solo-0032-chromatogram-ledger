"""Per-component interchange determination for a registered split-sample list.

A register (分样清单) ties two accounted batches together.  Each mother sample
lists the injections drawn on both instruments (left/right) and the
handover-documented correspondences that may pair them.  Because each
instrument numbers injections independently, an injection is identified by its
side and its in-batch ID.  A feasible correspondence must use every listed
injection, pair each one at most once, and stay inside one mother sample and the
register's two batches.

For each component the delivered concentrations decide whether a pair exceeds
the registered absolute difference limit (a difference equal to the limit is
acceptable).  A pair held on either side keeps its slot but leaves whether it
exceeds unknown.  The fewest and most possible exceeding pairs over all feasible
correspondences drive the verdict:

* most  <= allowed  -> the batches may be confirmed interchangeable
* least >  allowed  -> the batches are confirmed not interchangeable
* otherwise         -> insufficient evidence
"""
# Cost used for a correspondence the handover evidence does not allow.
FORBIDDEN = 10 ** 7
# Upper bound on the matchings enumerated purely for display.
MATCHING_DISPLAY_LIMIT = 500


def _hungarian(cost):
    """Minimum-cost perfect assignment on a square matrix (row -> column)."""
    n = len(cost)
    if n == 0:
        return [], 0
    infinity = float('inf')
    u = [0.0] * (n + 1)
    v = [0.0] * (n + 1)
    owner = [0] * (n + 1)
    way = [0] * (n + 1)
    for row in range(1, n + 1):
        owner[0] = row
        column = 0
        best = [infinity] * (n + 1)
        used = [False] * (n + 1)
        while True:
            used[column] = True
            current = owner[column]
            delta = infinity
            next_column = 0
            for candidate in range(1, n + 1):
                if used[candidate]:
                    continue
                value = cost[current - 1][candidate - 1] - u[current] - v[candidate]
                if value < best[candidate]:
                    best[candidate] = value
                    way[candidate] = column
                if best[candidate] < delta:
                    delta = best[candidate]
                    next_column = candidate
            for candidate in range(n + 1):
                if used[candidate]:
                    u[owner[candidate]] += delta
                    v[candidate] -= delta
                else:
                    best[candidate] -= delta
            column = next_column
            if owner[column] == 0:
                break
        while column:
            previous = way[column]
            owner[column] = owner[previous]
            column = previous
    assignment = [0] * n
    for column in range(1, n + 1):
        if owner[column]:
            assignment[owner[column] - 1] = column - 1
    total = sum(cost[row][assignment[row]] for row in range(n))
    return assignment, total


def _enumerate(left, right, relations, limit=MATCHING_DISPLAY_LIMIT):
    """All perfect matchings over allowed relations, capped for display."""
    matchings = []
    order = sorted(right)

    def walk(index, used, chosen):
        if len(matchings) > limit:
            return
        if index == len(left):
            matchings.append([{'left': l, 'right': r} for l, r in chosen])
            return
        left_id = left[index]
        for right_id in order:
            if right_id in used or (left_id, right_id) not in relations:
                continue
            used.add(right_id)
            chosen.append((left_id, right_id))
            walk(index + 1, used, chosen)
            chosen.pop()
            used.discard(right_id)

    walk(0, set(), [])
    return matchings, len(matchings) > limit


def _evidence(left_row, right_row, target_id, limit, source):
    left_entry = left_row['decisions'][target_id]
    right_entry = right_row['decisions'][target_id]
    left_held = left_entry['status'] != 'reportable' or left_entry.get('reported') is None
    right_held = right_entry['status'] != 'reportable' or right_entry.get('reported') is None
    held = left_held or right_held
    difference = None
    exceeded = None
    if not held:
        difference = abs(left_entry['reported'] - right_entry['reported'])
        exceeded = difference > limit + 1e-12
    return {
        'left': left_row['id'], 'right': right_row['id'],
        'left_value': left_entry.get('reported'), 'right_value': right_entry.get('reported'),
        'left_status': left_entry['status'], 'right_status': right_entry['status'],
        'left_issues': left_entry.get('issues', []), 'right_issues': right_entry.get('issues', []),
        'held': held, 'difference': difference, 'exceeded': exceeded, 'source': source,
    }


def _witness(left, right, relations, evidence, cost):
    """Extreme assignment: minimise the given cost, return chosen pairs."""
    matrix = [[cost[(l, r)] if (l, r) in relations else FORBIDDEN for r in right] for l in left]
    assignment, total = _hungarian(matrix)
    if total >= FORBIDDEN:
        return None, []
    pairs = [evidence[(left[row], right[assignment[row]])] for row in range(len(left))]
    return total, sorted(pairs, key=lambda pair: (pair['left'], pair['right']))


def _mother_plan(mother):
    left = list(mother['left'])
    right = list(mother['right'])
    relations = {(relation['left'], relation['right']) for relation in mother['relations']}
    report = {
        'id': mother['id'], 'source': mother['source'],
        'left': sorted(left), 'right': sorted(right),
        'relations': sorted(({'left': relation['left'], 'right': relation['right'],
                              'source': relation['source']} for relation in mother['relations']),
                            key=lambda relation: (relation['left'], relation['right'])),
    }
    if len(left) != len(right):
        report.update(complete=False, reason='count_mismatch')
        return report, None
    assignment, total = _hungarian([[0 if (l, r) in relations else FORBIDDEN for r in right] for l in left])
    if total >= FORBIDDEN or any((left[row], right[assignment[row]]) not in relations for row in range(len(left))):
        report.update(complete=False, reason='no_complete_matching')
        return report, None
    matchings, truncated = _enumerate(left, right, relations)
    report.update(complete=True, matchings=matchings, matching_count=len(matchings))
    if truncated:
        report['matchings_truncated'] = True
    return report, (left, right, relations)


def _mother_costs(mother, left_index, right_index, target_id, limit):
    evidence = {}
    cost_min = {}
    cost_max = {}
    for relation in mother['relations']:
        key = (relation['left'], relation['right'])
        item = _evidence(left_index[relation['left']], right_index[relation['right']],
                         target_id, limit, relation['source'])
        evidence[key] = item
        cost_min[key] = 0 if item['held'] or not item['exceeded'] else 1
        cost_max[key] = 0 if item['held'] or item['exceeded'] else 1
    return evidence, cost_min, cost_max


def evaluate(register, left_run, right_run):
    """Return the registered pairing audit together with per-component ranges."""
    left_index = {row['id']: row for row in left_run['report']['injections']}
    right_index = {row['id']: row for row in right_run['report']['injections']}

    mothers = []
    plans = {}
    for mother in register['mother_samples']:
        report, plan = _mother_plan(mother)
        mothers.append(report)
        plans[mother['id']] = (mother, plan)
    complete = all(report['complete'] for report in mothers)

    result = {
        'register': {'id': register['id'], 'version': register['version'],
                     'supersedes': register.get('supersedes'),
                     'left_run': register['left_run'], 'right_run': register['right_run']},
        'batches': {'left': left_run['id'], 'right': right_run['id']},
        'allowed_exceedances': register['allowed_exceedances'],
        'pairing': {'complete': complete, 'mother_samples': mothers},
        'targets': {},
    }
    if not complete:
        result['pairing']['insufficient'] = True

    for target in left_run['source']['method']['targets']:
        target_id = target['id']
        limit = register['limits'][target_id]
        entry = {'id': target_id, 'label': target['label'], 'limit': limit,
                 'allowed_exceedances': register['allowed_exceedances']}
        if not complete:
            result['targets'][target_id] = {
                **entry, 'min': None, 'max': None, 'conclusion': 'pairing_insufficient',
                'minimum': None, 'maximum': None,
            }
            continue
        minimum = maximum = 0
        minimum_pairs, maximum_pairs = [], []
        for mother in register['mother_samples']:
            _, plan = plans[mother['id']]
            left, right, relations = plan
            evidence, cost_min, cost_max = _mother_costs(mother, left_index, right_index, target_id, limit)
            low, low_pairs = _witness(left, right, relations, evidence, cost_min)
            high_cost, high_pairs = _witness(left, right, relations, evidence, cost_max)
            # cost_max flags non-exceeding pairs, so its minimum is the complement.
            minimum += low
            maximum += len(left) - high_cost
            minimum_pairs.extend(low_pairs)
            maximum_pairs.extend(high_pairs)
        if maximum <= register['allowed_exceedances']:
            conclusion = 'interchangeable'
        elif minimum > register['allowed_exceedances']:
            conclusion = 'not_interchangeable'
        else:
            conclusion = 'insufficient_evidence'
        result['targets'][target_id] = {
            **entry, 'min': minimum, 'max': maximum, 'conclusion': conclusion,
            'minimum': {'count': minimum,
                        'pairs': sorted(minimum_pairs, key=lambda pair: (pair['left'], pair['right']))},
            'maximum': {'count': maximum,
                        'pairs': sorted(maximum_pairs, key=lambda pair: (pair['left'], pair['right']))},
        }
    return result
