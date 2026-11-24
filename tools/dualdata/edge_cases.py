"""双端校准边界规则核验：夹段、缺针、失败核查不可跨越、量程与输入校验。"""
from copy import deepcopy
from math import isclose

from chromaledger.analysis import analyze
from chromaledger.errors import Rejected
from chromaledger.sequence import parse_document

from .build import METHOD, base_document, scenario_two_groups, build_standard_rows, A_THREE, B_UNIFORM, blank, qc, sample


def require(condition, detail):
    if not condition:
        raise AssertionError(detail)


def analyze_doc(document):
    return analyze(parse_document(deepcopy(document)))


def test_no_sandwich_segment():
    """首组之前与末组之后的待测样没有有效夹段，不出报告。"""
    doc = scenario_two_groups('edge-no-segment', 360)
    # 在首组之前（顺序号 0 不允许，用 0.5 不行，改为插到第一组标准之前的 order 空档不存在）。
    # 标准占 1–3，因此把一个样品放到末组之后（order 16）验证无夹段。
    doc['injections'].append(sample('S-TAIL', 16, 300, 'VIAL-TAIL'))
    doc['injections'].sort(key=lambda row: row['order'])
    report = analyze_doc(doc)
    rows = {row['id']: row for row in report['injections']}
    tail = rows['S-TAIL']['decisions']['A']
    require(tail['status'] == 'held' and tail['issues'] == ['no_sandwich_segment'] and tail['reported'] is None,
            '末组之后的待测样应因无有效夹段暂缓')
    print('通过 no-sandwich：无有效夹段的待测样不出报告')


def test_missing_qc_holds():
    """段内缺后核查或前核查时，样品暂缓且原因可区分。"""
    doc = scenario_two_groups('edge-missing-qc', 360)
    # 移除 5 号前核查，保留 11 号。
    doc['injections'] = [row for row in doc['injections'] if row['id'] != 'QC-05']
    report = analyze_doc(doc)
    rows = {row['id']: row for row in report['injections']}
    decision = rows['S-07']['decisions']['A']
    require(decision['status'] == 'held' and 'qc_absent' in decision['issues'],
            '缺前核查应暂缓并给出 qc_absent')
    require(decision['qc_id'] is None and decision['qc_after_id'] == 'QC-11', '后核查引用应保留')
    print('通过 missing-qc：缺针暂缓且原因可区分')


def test_failed_qc_not_bypassed():
    """最近一针核查失败即暂缓，不能借更远的合格核查。"""
    doc = scenario_two_groups('edge-failed-qc', 360)
    # 在 7 号与 11 号之间插入一针失败核查（order 9，面积 900 -> 观测远超 2）。
    doc['injections'].append(qc('QC-09', 9, 900, 900))
    doc['injections'].sort(key=lambda row: row['order'])
    report = analyze_doc(doc)
    rows = {row['id']: row for row in report['injections']}
    decision = rows['S-07']['decisions']['A']
    require(decision['status'] == 'held' and decision['qc_after_id'] == 'QC-09'
            and 'qc_after_blocked' in decision['issues'],
            '最近的后核查失败时必须暂缓，不能越过它引用 11 号')
    print('通过 failed-qc：失败核查不可跨越')


def test_out_of_range_holds():
    """插值曲线上反算浓度超出两端共同量程时暂缓，先检量程再计稀释。"""
    doc = scenario_two_groups('edge-range', 360)
    # 末组覆盖到 4；把样品面积调到反算浓度远超 4。
    target = next(row for row in doc['injections'] if row['id'] == 'S-07')
    target['peaks'] = [p for p in target['peaks'] if p['id'] != 'p-alpha']
    target['peaks'].append({'id': 'p-alpha', 'time': 2.35, 'area': 3000})
    report = analyze_doc(doc)
    rows = {row['id']: row for row in report['injections']}
    decision = rows['S-07']['decisions']['A']
    require(decision['status'] == 'held' and 'out_of_range' in decision['issues']
            and decision['reported'] is None, '超量程不得出报告，稀释不能掩盖量程问题')
    print('通过 out-of-range：先检量程再计稀释')


def test_ambiguous_combination_holds():
    """无指定核查且两份登记在某组真正不同 → 可用组合不唯一，整组分暂缓。"""
    doc = base_document('edge-ambiguous', 300)
    doc['dual_end']['designated_checks'] = []
    report = analyze_doc(doc)
    cal_a = report['dual_calibration']['A']
    require(cal_a['selection']['status'] == 'ambiguous_combination',
            '去掉指定核查后甲的两条不同登记路径应判为不唯一')
    rows = {row['id']: row for row in report['injections']}
    require(rows['S-07']['decisions']['A']['issues'] == ['ambiguous_combination'],
            '不唯一组合下待测样应暂缓')
    # 乙两份登记等效，折叠后仍唯一，可独立放行。
    cal_b = report['dual_calibration']['B']
    require(cal_b['selection']['status'] == 'unique', '乙的等效登记不应被误判为不唯一')
    print('通过 ambiguous：不唯一暂缓，等效登记不误伤')


def test_input_validation():
    """双端输入的结构约束在规范化阶段被拒绝。"""
    doc = base_document('edge-invalid', 300)
    # 指定核查引用了不存在的进样。
    bad = deepcopy(doc)
    bad['dual_end']['designated_checks'] = ['QC-404']
    try:
        parse_document(bad)
        raise AssertionError('未知指定核查应被拒绝')
    except Rejected as exc:
        require(exc.code == 'invalid_document', '应返回 invalid_document')
    # 登记引用了非标准进样。
    bad = deepcopy(doc)
    bad['dual_end']['registrations'][0]['levels']['QC-08'] = {'A': 2}
    try:
        parse_document(bad)
        raise AssertionError('登记引用核查样应被拒绝')
    except Rejected as exc:
        require(exc.code == 'invalid_document', '应返回 invalid_document')
    # 单组标准不允许双端模式。
    bad = deepcopy(doc)
    bad['injections'] = [row for row in bad['injections'] if row['order'] < 13]
    try:
        parse_document(bad)
        raise AssertionError('单组标准不应启用双端校准')
    except Rejected as exc:
        require(exc.code == 'invalid_document', '应返回 invalid_document')
    print('通过 input-validation：双端结构约束被正确拒绝')


def test_legacy_document_unchanged():
    """没有 dual_end 段的文档仍走单端引擎，且不允许中段标准。"""
    doc = {
        'id': 'edge-legacy', 'label': '单端', 'method': METHOD,
        'injections': build_standard_rows(A_THREE, B_UNIFORM)[:3] + [
            blank('BLK-04', 4), sample('S-05', 5, 300, 'VIAL-05'),
        ],
    }
    report = analyze_doc(doc)
    require(report['engine'] == 'chromaledger-v1' and 'mode' not in report, '旧文档必须走 v1 引擎')
    print('通过 legacy：无 dual_end 文档保持单端口径')


def main():
    test_no_sandwich_segment()
    test_missing_qc_holds()
    test_failed_qc_not_bypassed()
    test_out_of_range_holds()
    test_ambiguous_combination_holds()
    test_input_validation()
    test_legacy_document_unchanged()
    print('双端校准边界规则全部核验通过。')


if __name__ == '__main__':
    main()
