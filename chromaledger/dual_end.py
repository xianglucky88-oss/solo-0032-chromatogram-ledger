"""可选的双端校准（首尾/中段多组标准 + 候选登记）核算引擎。

输入是已经规范化的批次文档（含可选 ``dual_end`` 段）以及与单端模式相同的峰归属
结果。双端模式下，标准样可在序列中组成多组；复核员为同一组已完成的标准进样登记
不同的标称浓度（候选记录）。本模块按组分独立：

* 每个标准组在每份登记下各拟合一条一次曲线；
* 相邻组之间按进样顺序号在两条曲线的同浓度响应间线性插值，量程取两端交集；
* 指定核查必须由整批同一套登记同时通过，用于在候选组合中筛选；
* 每个标准组每份登记只能被采用一次，按「无可用组合 / 不唯一 / 唯一」区分暂缓与放行；
* 逐针放行要求段内最近的前、后核查分别合格，不能越过失败核查或失效标准组。
"""
from .calibration import fit_line
from .blank import evaluate_blank
from .quality import evaluate_qc
from .quantification import concentration, inside_range

ENGINE_DUAL = 'chromaledger-v2-dual'

EPSILON = 1e-12


def standard_groups(injections):
    groups, current = [], []
    for row in injections:
        if row['kind'] == 'standard':
            current.append(row)
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def registered_level(registration, injection_id, target_id, fallback):
    override = registration['levels'].get(injection_id)
    if override is None or target_id not in override:
        return fallback
    return override[target_id]


def fit_group_curve(group, mapped, target_id, registration, minimum_r2):
    points, excluded = [], []
    for row in group:
        assignment = mapped[row['id']]['assignments'][target_id]
        level = registered_level(registration, row['id'], target_id, row['expected'][target_id])
        point = {'injection_id': row['id'], 'level': level, 'peak_ids': assignment['ids']}
        if assignment['status'] == 'matched':
            points.append({**point, 'area': assignment['area']})
        else:
            excluded.append({**point, 'reason': assignment['status']})
    return {**fit_line(points, minimum_r2), 'excluded': excluded}


def interpolate(fraction, left, right):
    """在两条一次曲线之间按顺序号比例线性插值斜率与截距。"""
    if left['status'] != 'ready' or right['status'] != 'ready':
        return {'status': 'curve_unavailable'}
    return {
        'status': 'ready',
        'slope': left['slope'] + (right['slope'] - left['slope']) * fraction,
        'intercept': left['intercept'] + (right['intercept'] - left['intercept']) * fraction,
        'levels': [max(left['levels'][0], right['levels'][0]),
                   min(left['levels'][-1], right['levels'][-1])],
    }


def fraction_at(order, left_group, right_group):
    last_left = max(row['order'] for row in left_group)
    first_right = min(row['order'] for row in right_group)
    return (order - last_left) / (first_right - last_left)


def segment_curve(order, left_group, right_group, left_curve, right_curve):
    fraction = fraction_at(order, left_group, right_group)
    curve = interpolate(fraction, left_curve, right_curve)
    if curve['status'] != 'ready' or curve['levels'][0] > curve['levels'][1] + EPSILON:
        return {**curve, 'status': 'curve_unavailable'} if curve['status'] == 'ready' else curve
    curve['fraction'] = fraction
    return curve


def find_segment(order, groups):
    for index in range(len(groups) - 1):
        left_group, right_group = groups[index], groups[index + 1]
        last_left = max(row['order'] for row in left_group)
        first_right = min(row['order'] for row in right_group)
        if last_left < order < first_right:
            return index, left_group, right_group
    return None


def evaluate_designated_check(row, mapped, target_id, groups, group_curves, method):
    """用候选组合对应的段插值曲线评估一针指定核查。"""
    located = find_segment(row['order'], groups)
    assignment = mapped[row['id']]['assignments'][target_id]
    if located is None:
        return {'status': 'unresolved', 'observed': None, 'expected': row['expected'][target_id],
                'tolerance': method['qc_tolerance'], 'relative_error': None,
                'reason': 'no_sandwich_segment'}
    index, left_group, right_group = located
    curve = segment_curve(row['order'], left_group, right_group,
                          group_curves[index], group_curves[index + 1])
    evidence = evaluate_qc(assignment, curve, row['expected'][target_id], method['qc_tolerance'])
    return {**evidence, 'segment_index': index}


def curve_signature(curve):
    """用于识别同一组分下完全等效的登记：曲线系数与状态一致即视为同一条有效路径。"""
    if curve['status'] != 'ready':
        return (curve['status'],)
    return (curve['status'], round(curve['slope'], 12), round(curve['intercept'], 12),
            tuple(round(level, 12) for level in curve['levels']))


def evaluate_target(target_id, method, injections, mapped, groups, registrations, designated_checks):
    minimum_r2 = method['min_r2']
    tolerance = method['qc_tolerance']
    # 1) 每份登记下，每个标准组的组分曲线。
    per_registration = []
    for registration in registrations:
        curves = [fit_group_curve(group, mapped, target_id, registration, minimum_r2) for group in groups]
        per_registration.append({'registration_id': registration['id'], 'curves': curves})

    group_count = len(groups)
    # 2) 每个组内把曲线完全等效的登记折叠为同一条有效路径（按登记编号取确定代表）。
    #    每个组分在同一标准组只能用一份登记；等效登记不构成「不唯一」。
    options_per_group = []
    for group_index in range(group_count):
        signatures = {}
        for item in per_registration:
            if item['curves'][group_index]['status'] != 'ready':
                continue
            signature = curve_signature(item['curves'][group_index])
            signatures.setdefault(signature, []).append(item['registration_id'])
        options = []
        for members in signatures.values():
            members.sort()
            options.append(members[0])
        options_per_group.append(sorted(options))

    # 3) 枚举整批同一套登记（每个组各选一份登记）的候选组合。
    combinations = [()]
    for options in options_per_group:
        combinations = [combo + (option,) for combo in combinations for option in options]

    # 4) 指定核查必须由同一套登记同时通过；待测样结果不能替登记作证。
    check_evidence = {check['id']: [] for check in designated_checks}
    surviving = []
    for combo in combinations:
        combo_curves = []
        for group_index, registration_id in enumerate(combo):
            item = next(entry for entry in per_registration if entry['registration_id'] == registration_id)
            combo_curves.append(item['curves'][group_index])
        all_passed = True
        for check in designated_checks:
            evidence = evaluate_designated_check(check, mapped, target_id, groups, combo_curves, method)
            check_evidence[check['id']].append({'registration_ids': list(combo), **evidence})
            if evidence['status'] != 'passed':
                all_passed = False
        if all_passed:
            surviving.append(combo)

    # 无指定核查时不再用核查筛选：所有候选组合都存活。等效登记已在第 2 步折叠，
    # 因此折叠后仅剩一条路径即「唯一可用」；真正不同的多条路径仍按「不唯一」暂缓。
    # 有指定核查时，只有全部指定核查同时通过的组合存活，再按存活数量区分。
    if not designated_checks:
        surviving = list(combinations)
    if not surviving:
        selection = {'status': 'no_usable_combination', 'combinations': [list(combo) for combo in combinations],
                     'supported': []}
    elif len(surviving) > 1:
        selection = {'status': 'ambiguous_combination', 'combinations': [list(combo) for combo in combinations],
                     'supported': [list(combo) for combo in surviving]}
    else:
        chosen = surviving[0]
        chosen_curves = []
        for group_index, registration_id in enumerate(chosen):
            item = next(entry for entry in per_registration if entry['registration_id'] == registration_id)
            chosen_curves.append(item['curves'][group_index])
        selection = {'status': 'unique', 'combinations': [list(combo) for combo in combinations],
                     'supported': [list(combo) for combo in surviving],
                     'chosen': {'registration_ids': list(chosen), 'group_curves': chosen_curves}}
    return {
        'target_id': target_id,
        'registrations': [
            {'registration_id': item['registration_id'],
             'groups': [{'group_index': index, **curve} for index, curve in enumerate(item['curves'])]}
            for item in per_registration
        ],
        'designated_checks': [
            {'injection_id': check_id, 'evaluations': evidence_list}
            for check_id, evidence_list in check_evidence.items()
        ],
        'selection': selection,
        'tolerance': tolerance,
    }


def decide_dual_sample(injection, assignment, curve, blank, before_qc, after_qc):
    issues = []
    if curve['status'] != 'ready':
        issues.append('curve_unavailable')
    if assignment['status'] != 'matched':
        issues.append(assignment['status'])
    if blank is None:
        issues.append('blank_absent')
    elif blank['status'] != 'clear':
        issues.append('blank_blocked')
    if before_qc is None:
        issues.append('qc_absent')
    elif before_qc['status'] != 'passed':
        issues.append('qc_blocked')
    if after_qc is None:
        issues.append('qc_after_absent')
    elif after_qc['status'] != 'passed':
        issues.append('qc_after_blocked')

    measured = concentration(assignment, curve)
    if measured is not None and not inside_range(measured, curve):
        issues.append('out_of_range')
    return {
        'status': 'held' if issues else 'reportable', 'issues': issues,
        'measured': measured,
        'reported': None if issues else measured * injection['dilution'],
        'blank_id': blank['injection_id'] if blank else None,
        'qc_id': before_qc['injection_id'] if before_qc else None,
        'qc_after_id': after_qc['injection_id'] if after_qc else None,
    }


def analyze_dual(document, mapped):
    method = document['method']
    injections = document['injections']
    registrations = document['dual_end']['registrations']
    designated_ids = document['dual_end']['designated_checks']
    designated_set = set(designated_ids)
    groups = standard_groups(injections)
    designated_checks = [row for row in injections if row['id'] in designated_set]
    designated_checks.sort(key=lambda row: row['order'])

    calibration = {}
    counts = {'reportable': 0, 'held': 0}
    for target in method['targets']:
        calibration[target['id']] = evaluate_target(
            target['id'], method, injections, mapped, groups, registrations, designated_checks)

    # 第一遍：对每个组分先评估全部核查（后核查是未来进样，须在逐针放行前算完），
    # 并按段归档；同时顺序维护最近空白，并按进样快照当时的最近空白。
    blanks = {target['id']: None for target in method['targets']}
    blank_at = {}
    qc_by_segment = {target['id']: {} for target in method['targets']}
    qc_decisions = {target['id']: {} for target in method['targets']}
    for row in injections:
        for target in method['targets']:
            target_id = target['id']
            assignment = mapped[row['id']]['assignments'][target_id]
            if row['kind'] == 'blank':
                # 每个组分独立维护最近空白，避免共享字典互相覆盖编号。
                blanks[target_id] = dict(evaluate_blank(assignment, target['blank_limit']),
                                         injection_id=row['id'])
                continue
            if row['kind'] != 'qc':
                continue
            target_cal = calibration[target_id]
            chosen = target_cal['selection'].get('chosen')
            located = find_segment(row['order'], groups)
            if chosen is None or located is None:
                reason = (target_cal['selection']['status'] if chosen is None
                          else 'no_sandwich_segment')
                evidence = {'status': 'unresolved', 'observed': None,
                            'expected': row['expected'][target_id],
                            'tolerance': method['qc_tolerance'], 'relative_error': None, 'reason': reason}
            else:
                segment_index, left_group, right_group = located
                curve = segment_curve(row['order'], left_group, right_group,
                                      chosen['group_curves'][segment_index],
                                      chosen['group_curves'][segment_index + 1])
                evidence = evaluate_qc(assignment, curve, row['expected'][target_id], method['qc_tolerance'])
                evidence['segment_index'] = segment_index
                qc_by_segment[target_id].setdefault(segment_index, []).append(
                    {**evidence, 'injection_id': row['id'], 'order': row['order'],
                     'designated': row['id'] in designated_set})
            qc_decisions[target_id][row['id']] = evidence
        blank_at[row['id']] = {target_id: (dict(blanks[target_id]) if blanks[target_id] else None)
                               for target_id in blanks}

    # 第二遍：逐针生成判定，样品引用段内最近的前、后核查（均须合格）。
    results = []
    for row in injections:
        result = {**{k: v for k, v in row.items() if k != 'peaks'},
                  **mapped[row['id']], 'decisions': {}}
        located = find_segment(row['order'], groups) if row['kind'] != 'standard' else None
        row_blanks = blank_at[row['id']]
        for target in method['targets']:
            target_id = target['id']
            assignment = mapped[row['id']]['assignments'][target_id]
            target_cal = calibration[target_id]
            chosen = target_cal['selection'].get('chosen')
            row_blank = row_blanks[target_id]

            if row['kind'] == 'blank':
                result['decisions'][target_id] = dict(row_blank)
                continue
            if row['kind'] == 'qc':
                result['decisions'][target_id] = qc_decisions[target_id][row['id']]
                continue
            if row['kind'] == 'standard':
                result['decisions'][target_id] = {
                    'status': 'used' if assignment['status'] == 'matched' else 'excluded'}
                continue

            # 待测样
            if chosen is None:
                result['decisions'][target_id] = {
                    'status': 'held', 'issues': [target_cal['selection']['status']],
                    'measured': None, 'reported': None,
                    'blank_id': row_blank['injection_id'] if row_blank else None,
                    'qc_id': None, 'qc_after_id': None}
                counts['held'] += 1
                continue
            if located is None:
                result['decisions'][target_id] = {
                    'status': 'held', 'issues': ['no_sandwich_segment'],
                    'measured': None, 'reported': None,
                    'blank_id': row_blank['injection_id'] if row_blank else None,
                    'qc_id': None, 'qc_after_id': None}
                counts['held'] += 1
                continue

            segment_index, left_group, right_group = located
            curve = segment_curve(row['order'], left_group, right_group,
                                  chosen['group_curves'][segment_index],
                                  chosen['group_curves'][segment_index + 1])
            history = qc_by_segment[target_id].get(segment_index, [])
            before = next((item for item in reversed(history) if item['order'] < row['order']), None)
            after = next((item for item in history if item['order'] > row['order']), None)
            evidence = decide_dual_sample(row, assignment, curve, row_blank, before, after)
            evidence['segment_index'] = segment_index
            evidence['curve'] = {k: curve[k] for k in ('slope', 'intercept', 'levels', 'fraction') if k in curve}
            evidence['registration_ids'] = chosen['registration_ids']
            counts[evidence['status']] += 1
            result['decisions'][target_id] = evidence
        results.append(result)

    return {
        'engine': ENGINE_DUAL,
        'mode': 'dual_end',
        'standard_groups': [{'group_index': index,
                             'orders': [row['order'] for row in group],
                             'injection_ids': [row['id'] for row in group]}
                            for index, group in enumerate(groups)],
        'designated_checks': designated_ids,
        'dual_calibration': calibration,
        'injections': results,
        'counts': counts,
    }
