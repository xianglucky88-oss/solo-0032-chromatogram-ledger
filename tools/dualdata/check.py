"""双端校准场景核验：从进样证据核对登记、核查与暂缓原因。

直接调用规范化与核算引擎（不经过 HTTP），使用合成数据。运行：
    python3 -m tools.dualdata.check
"""
from math import isclose

from chromaledger.analysis import analyze
from chromaledger.sequence import parse_document

from .build import base_document, scenario_two_groups


def require(condition, detail):
    if not condition:
        raise AssertionError(detail)


def analyze_doc(document):
    return analyze(parse_document(document))


def by_id(report):
    return {row['id']: row for row in report['injections']}


def evaluation(calibration, check_id, combo):
    record = next(item for item in calibration['designated_checks'] if item['injection_id'] == check_id)
    return next(item for item in record['evaluations'] if tuple(item['registration_ids']) == tuple(combo))


# 三组场景里首末组两份登记等效，组合只在中段（group 1）分叉。
COMBO_R1 = ('reg-ledger-1', 'reg-ledger-1', 'reg-ledger-1')
COMBO_MID_R2 = ('reg-ledger-1', 'reg-ledger-2', 'reg-ledger-1')


def check_split_hold():
    """场景一：前段只支持登记一、后段只支持登记二 → 整组分无可用组合。"""
    report = analyze_doc(base_document('dual-split-hold', 400))
    rows = by_id(report)
    cal_a = report['dual_calibration']['A']
    require(cal_a['selection']['status'] == 'no_usable_combination', '甲应无可用组合')
    # 8 号：登记一合格；中段换成登记二后插值斜率 150，观测 4/3、相对误差 1/3 → 超差。
    qc8_r1 = evaluation(cal_a, 'QC-08', COMBO_R1)
    qc8_r2 = evaluation(cal_a, 'QC-08', COMBO_MID_R2)
    require(qc8_r1['status'] == 'passed' and isclose(qc8_r1['observed'], 2, abs_tol=1e-9),
            '8 号在第一份登记下应合格（观测 2）')
    require(qc8_r2['status'] == 'failed' and isclose(qc8_r2['observed'], 4 / 3, abs_tol=1e-9)
            and isclose(qc8_r2['relative_error'], 1 / 3, abs_tol=1e-9),
            '8 号在第二份登记下应超差（观测 4/3、误差 1/3）')
    # 20 号面积 400：后段中点插值斜率 150。登记一观测 8/3、相对误差 1/3 → 超差；
    # 中段换登记二后斜率也是 200，观测 2 → 合格，但该组合已被 8 号否决。
    qc20_r1 = evaluation(cal_a, 'QC-20', COMBO_R1)
    qc20_r2 = evaluation(cal_a, 'QC-20', COMBO_MID_R2)
    require(qc20_r1['status'] == 'failed'
            and isclose(qc20_r1['observed'], 8 / 3, abs_tol=1e-9)
            and isclose(qc20_r1['relative_error'], 1 / 3, abs_tol=1e-9),
            '20 号面积 400 在第一份登记下应超差（观测 8/3、误差 1/3）')
    require(qc20_r2['status'] == 'passed' and isclose(qc20_r2['observed'], 2, abs_tol=1e-9),
            '20 号在第二份登记下应合格（观测 2），但该组合已被 8 号否决')
    require(not cal_a['selection']['supported'], '不应存在两段共同支持的登记组合')
    # 不能分段各取一份登记：任何幸存组合都必须是整批同一套登记，这里应一个都没有。
    require(all('registration_ids' not in row['decisions']['A']
                for row in report['injections'] if row['kind'] == 'sample'),
            '无可用组合时样品行不得携带任何分段登记引用')
    # 待测样不得出报告，原因指向无可用组合，不能拿样品替登记作证。
    sample_a = rows['S-07']['decisions']['A']
    require(sample_a['status'] == 'held' and sample_a['reported'] is None
            and sample_a['issues'] == ['no_usable_combination'], '无可用组合时待测样应暂缓')
    print('通过 split-hold：甲无可用组合，混合分段被指定核查否决，待测样暂缓')


def check_shared_pass():
    """场景二：20 号面积改 300，两段共同支持第一份登记。"""
    report = analyze_doc(base_document('dual-shared-pass', 300))
    rows = by_id(report)
    cal_a = report['dual_calibration']['A']
    require(cal_a['selection']['status'] == 'unique', '甲应有唯一可用组合')
    require(cal_a['selection']['chosen']['registration_ids'] == list(COMBO_R1),
            '唯一组合应为整批第一份登记')
    qc20_r1 = evaluation(cal_a, 'QC-20', COMBO_R1)
    require(qc20_r1['status'] == 'passed' and isclose(qc20_r1['observed'], 2, abs_tol=1e-9),
            '20 号面积 300 在第一份登记下应观测 2 并在 10% 内容差合格')
    qc20_r2 = evaluation(cal_a, 'QC-20', COMBO_MID_R2)
    require(qc20_r2['status'] == 'failed', '20 号在第二份登记下不应同时合格，否则组合不唯一')
    # 乙按自己的证据独立判断：两份登记对乙等效，指定核查独立合格 → 唯一可用。
    cal_b = report['dual_calibration']['B']
    require(cal_b['selection']['status'] == 'unique', '乙应独立得到唯一组合')
    # 7 号：面积 300、稀释两倍；5 号与 8 号是最近前后核查，6 号空白合格 → 报 6 mg/L。
    sample_a = rows['S-07']['decisions']['A']
    require(sample_a['status'] == 'reportable', '7 号甲应可交付')
    require(isclose(sample_a['measured'], 3, abs_tol=1e-9) and isclose(sample_a['reported'], 6, abs_tol=1e-9),
            '7 号甲应先检量程再按两倍稀释报 6 mg/L')
    require(sample_a['blank_id'] == 'BLK-06', '7 号应引用最近合格空白 6 号')
    require(sample_a['qc_id'] == 'QC-05' and sample_a['qc_after_id'] == 'QC-08',
            '7 号应引用段内最近的前核查 5 号与后核查 8 号')
    require(sample_a['registration_ids'] == list(COMBO_R1), '可交付行须指回采用的登记')
    sample_b = rows['S-07']['decisions']['B']
    require(sample_b['status'] == 'reportable' and isclose(sample_b['reported'], 6, abs_tol=1e-9),
            '乙应按自身证据独立放行并报 6 mg/L')
    print('通过 shared-pass：两段共同支持第一份登记，7 号两针核查 + 空白合格，报 6 mg/L')


def check_two_group_hold():
    """场景四（暂缓分支）：11 号面积 540 → 相对误差 50%，7 号因后核查失败而暂缓。"""
    report = analyze_doc(scenario_two_groups('dual-two-hold', 540))
    rows = by_id(report)
    qc11 = rows['QC-11']['decisions']['A']
    require(qc11['status'] == 'failed' and isclose(qc11['relative_error'], 0.5, abs_tol=1e-9),
            '11 号面积 540 应判定相对误差 50% 且超差')
    sample_a = rows['S-07']['decisions']['A']
    require(sample_a['status'] == 'held' and 'qc_after_blocked' in sample_a['issues'],
            '后核查 11 号失败时 7 号应暂缓，不能越过失败核查')
    require(sample_a['reported'] is None, '暂缓行不得产生报告值')
    print('通过 two-hold：11 号超差 50%，7 号以后核查阻断暂缓')


def check_two_group_pass():
    """场景四（放行分支）：11 号面积 360 → 合格，7 号报 6 mg/L。"""
    report = analyze_doc(scenario_two_groups('dual-two-pass', 360))
    rows = by_id(report)
    qc5 = rows['QC-05']['decisions']['A']
    require(qc5['status'] == 'passed' and isclose(qc5['observed'], 2, abs_tol=1e-9),
            '5 号面积 240 在插值曲线上应观测 2 并合格')
    qc11 = rows['QC-11']['decisions']['A']
    require(qc11['status'] == 'passed' and isclose(qc11['observed'], 2, abs_tol=1e-9),
            '11 号面积 360 应观测 2 并合格')
    sample_a = rows['S-07']['decisions']['A']
    require(sample_a['status'] == 'reportable', '两针核查合格后 7 号应可交付')
    require(isclose(sample_a['measured'], 3, abs_tol=1e-9) and isclose(sample_a['reported'], 6, abs_tol=1e-9),
            '7 号面积 420 应反算 3、稀释后报 6 mg/L')
    require(sample_a['qc_id'] == 'QC-05' and sample_a['qc_after_id'] == 'QC-11',
            '7 号应引用前后两针核查 5 号与 11 号')
    require(sample_a['blank_id'] == 'BLK-06', '7 号应引用 6 号合格空白')
    cal_a = report['dual_calibration']['A']
    require(cal_a['selection']['status'] == 'unique' and report['designated_checks'] == [],
            '单份有效登记且无指定核查时应可逐针放行')
    # 乙按自身恒定斜率 100 的证据独立判断，不受甲首尾斜率变化影响。
    sample_b = rows['S-07']['decisions']['B']
    require(sample_b['status'] == 'reportable' and isclose(sample_b['measured'], 3, abs_tol=1e-9)
            and isclose(sample_b['reported'], 6, abs_tol=1e-9),
            '乙应按自身证据独立反算 3、稀释后报 6 mg/L')
    require(rows['QC-05']['decisions']['B']['status'] == 'passed'
            and rows['QC-11']['decisions']['B']['status'] == 'passed', '乙的两针核查应独立合格')
    print('通过 two-pass：单登记无指定核查亦可放行，7 号报 6 mg/L')


def main():
    check_split_hold()
    check_shared_pass()
    check_two_group_hold()
    check_two_group_pass()
    print('双端校准全部场景核验通过。')


if __name__ == '__main__':
    main()
