"""按需求数字构造双端校准合成批次。

标定几何（组分甲）：
* 首组（1–3）面积 100、200、400，标称 1、2、4 → 斜率 100，两份登记一致；
* 末组（25–27）面积 200、400、800，标称 1、2、4 → 斜率 200，两份登记一致；
* 中段（13–15）面积同为 100、200、400，第一份登记标称 1、2、4（斜率 100），
  第二份登记标称 0.5、1、2（斜率 200），截距均为 0；首末组覆盖 1–4 mg/L。

双组场景去掉中段：标准在 1–3、13–15，首尾斜率 100、200，登记明确（单份）。

组分乙所有标准组都保持斜率 100（面积恒为标称的 100 倍），两份登记对乙等效，
用于验证乙按自身证据独立判断。所有进样带合格定位峰。
"""
import argparse
import json

MARKER = {'id': 'locator', 'time': 1.28, 'area': 5000}
LEVELS = [1, 2, 4]
METHOD = {
    'id': 'dual-pair', 'revision': 'r1',
    'marker': {'time': 1.28, 'radius': 0.12, 'minimum_area': 2500},
    'targets': [
        {'id': 'A', 'label': '组分甲', 'time': 2.35, 'radius': 0.045, 'blank_limit': 140},
        {'id': 'B', 'label': '组分乙', 'time': 4.18, 'radius': 0.055, 'blank_limit': 120},
    ],
    'qc_tolerance': 0.1, 'min_r2': 0.995,
}
GROUP_ORDERS = {1: (1, 2, 3), 2: (13, 14, 15), 3: (25, 26, 27)}
# 甲：首组斜率 100、中段同面积、末组斜率 200。
A_THREE = {1: [100, 200, 400], 2: [100, 200, 400], 3: [200, 400, 800]}
A_TWO = {1: [100, 200, 400], 2: [200, 400, 800]}
# 乙：所有组斜率恒为 100。
B_UNIFORM = {1: [100, 200, 400], 2: [100, 200, 400], 3: [100, 200, 400]}


def peak(peak_id, time, area, saturated=False):
    row = {'id': peak_id, 'time': time, 'area': area}
    if saturated:
        row['saturated'] = True
    return row


def injection(injection_id, order, kind, area_a, *, expected=None, sample_id=None, dilution=None,
              area_b=None, include_a=True, include_b=True):
    peaks = [dict(MARKER)]
    if include_a:
        peaks.append(peak('p-alpha', 2.35, area_a))
    if include_b and area_b is not None:
        peaks.append(peak('p-beta', 4.18, area_b))
    row = {'id': injection_id, 'order': order, 'kind': kind, 'peaks': peaks}
    if expected is not None:
        row['expected'] = expected
    if sample_id is not None:
        row['sample_id'] = sample_id
    if dilution is not None:
        row['dilution'] = dilution
    return row


def standard(injection_id, order, area_a, area_b, level):
    return injection(injection_id, order, 'standard', area_a, area_b=area_b,
                     expected={'A': level, 'B': level})


def blank(injection_id, order):
    return injection(injection_id, order, 'blank', 0, include_a=False, include_b=False)


def qc(injection_id, order, area_a, area_b, expected=2):
    return injection(injection_id, order, 'qc', area_a, area_b=area_b,
                     expected={'A': expected, 'B': expected})


def sample(injection_id, order, area_a, sample_id, dilution=1, area_b=None):
    return injection(injection_id, order, 'sample', area_a, sample_id=sample_id, dilution=dilution,
                     area_b=area_b)


def build_standard_rows(areas_a, areas_b):
    rows = []
    for group_index in areas_a:
        for index, order in enumerate(GROUP_ORDERS[group_index]):
            rows.append(standard(f'STD-{order:02d}', order,
                                 areas_a[group_index][index], areas_b[group_index][index], LEVELS[index]))
    return rows


def build_two_registrations():
    """只覆盖中段（13–15）的甲；首末组与乙都回退到进样自带标称。"""
    middle_ids = ['STD-13', 'STD-14', 'STD-15']
    reg1, reg2 = {}, {}
    for index, injection_id in enumerate(middle_ids):
        reg1[injection_id] = {'A': [1, 2, 4][index]}
        reg2[injection_id] = {'A': [0.5, 1, 2][index]}
    return [{'id': 'reg-ledger-1', 'levels': reg1}, {'id': 'reg-ledger-2', 'levels': reg2}]


def base_document(run_id, qc20_area):
    """场景一/二：三组标准、8 号与 20 号指定核查（标称均为 2，容许误差 10%）。

    甲的指定核查面积随场景变化；乙的指定核查面积固定 200，在乙恒定斜率 100 的
    插值曲线上始终合格，用于验证乙按自身证据独立判断。
    """
    injections = build_standard_rows(A_THREE, B_UNIFORM)
    injections += [
        blank('BLK-04', 4),
        qc('QC-05', 5, 200, 200),
        blank('BLK-06', 6),
        sample('S-07', 7, 300, 'VIAL-07', dilution=2, area_b=300),
        qc('QC-08', 8, 200, 200),
        blank('BLK-16', 16),
        qc('QC-20', 20, qc20_area, 200),
    ]
    injections.sort(key=lambda row: row['order'])
    return {
        'id': run_id, 'label': f'双端校准合成批 · {run_id}', 'method': METHOD,
        'injections': injections,
        'dual_end': {'registrations': build_two_registrations(),
                     'designated_checks': ['QC-08', 'QC-20']},
    }


def scenario_two_groups(run_id, qc11_area):
    """场景四：首尾各一组标准（1–3、13–15），登记明确（单份登记），无指定核查。

    甲的首尾斜率为 100、200，核查面积按甲的中点插值斜率 150 设置；乙保持恒定
    斜率 100，核查与样品面积独立设置为在乙曲线上合格，验证乙独立判断。
    """
    standards = build_standard_rows(A_TWO, {1: B_UNIFORM[1], 2: B_UNIFORM[1]})
    injections = standards + [
        qc('QC-05', 5, 240, 200),
        blank('BLK-06', 6),
        sample('S-07', 7, 420, 'VIAL-07', dilution=2, area_b=300),
        qc('QC-11', 11, qc11_area, 200),
    ]
    injections.sort(key=lambda row: row['order'])
    return {
        'id': run_id, 'label': f'双端两组合成批 · {run_id}', 'method': METHOD,
        'injections': injections,
        'dual_end': {'registrations': [{'id': 'reg-only', 'levels': {}}], 'designated_checks': []},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scenario', choices=('split-hold', 'shared-pass', 'two-pass', 'two-hold'))
    args = parser.parse_args()
    if args.scenario == 'split-hold':
        document = base_document('dual-split-hold', 400)
    elif args.scenario == 'shared-pass':
        document = base_document('dual-shared-pass', 300)
    else:
        document = scenario_two_groups('dual-two-' + args.scenario.split('-')[1],
                                       360 if args.scenario == 'two-pass' else 540)
    print(json.dumps(document, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
