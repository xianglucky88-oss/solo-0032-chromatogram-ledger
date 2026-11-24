"""Exercise the public workbench against a disposable local database."""
import argparse
from copy import deepcopy
import csv
import hashlib
import io
import json
from math import isclose
from pathlib import Path
import selectors
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.request import Request, urlopen

PREFIX = '/chromaledger/api/'
EXAMPLES = Path('examples')


def require(condition, detail):
    if not condition:
        raise RuntimeError(detail)


class Client:
    def __init__(self, url):
        self.url = url

    def request(self, path, value=None, *, status=200, headers=None):
        data = None if value is None else json.dumps(value).encode('utf-8')
        req = Request(self.url + path, data, headers=headers or {'Content-Type': 'application/json'})
        try:
            response = urlopen(req, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            result = response.read()
            require(response.code == status, f'{path} 要求 HTTP {status}，实际 {response.code} {result[:250]!r}')
            return result

    def api(self, path, value=None, *, status=200):
        return json.loads(self.request(PREFIX + path, value, status=status))


class Example:
    def __init__(self, client, database):
        self.client = client
        self.database = database
        self.document = client.api('example')
        self.run = 'runs/' + self.document['id']
        self.inserted = client.api('runs', self.document, status=201)
        self.report = client.api(self.run)
        self.injections = {row['id']: row for row in self.report['injections']}

    def decision(self, injection, target):
        return self.injections[injection]['decisions'][target]

    def trace(self, injection):
        return self.client.api(f'{self.run}/injections/{injection}')


def check_preview(example):
    client = example.client
    document = deepcopy(example.document)
    document['id'] = 'preview-only'
    preview = client.api('preview', document)
    require(preview['counts'] == {'reportable': 4, 'held': 4}, '预检结果有误')
    client.api('runs/preview-only', status=404)
    document['method']['targets'][1]['time'] = document['method']['targets'][0]['time']
    require(client.api('preview', document, status=422)['error'] == 'invalid_document', '重叠方法窗必须拒绝')


def check_ingest(example):
    client = example.client
    document = deepcopy(example.document)
    document['injections'].reverse()
    for row in document['injections']:
        row['peaks'].reverse()
    require(client.api('runs', document)['reused'], '无关排序改变了幂等键')
    document['label'] += '修改'
    require(client.api('runs', document, status=409)['error'] == 'run_conflict', '应拒绝同编号异内容')
    require(client.api(example.run)['input_hash'] == example.inserted['input_hash'], '初始批次被覆盖')
    catalog = client.api('runs')
    require(any(row['id'] == example.document['id'] for row in catalog['items']), '目录缺少存入的批次')
    client.api('runs?offset=-1', status=400)


def check_csv(example):
    packaged = {'format': 'peak-csv-v1', 'metadata_json': (EXAMPLES / 'instrument-sequence.json').read_text(),
                'peaks_csv': (EXAMPLES / 'instrument-peaks.csv').read_text()}
    expected = example.inserted['input_hash']
    require(example.client.api('preview', packaged)['input_hash'] == expected, '分文件导入生成了不同的领域输入')
    require(example.client.api('runs', packaged)['reused'], 'CSV 和 JSON 的同事实重放应合并')
    packaged['peaks_csv'] += 'orphan,extra,1.5,450,0\n'
    require(example.client.api('preview', packaged, status=422)['error'] == 'invalid_document', '必须拒绝孤儿峰')


def check_alignment(example):
    aligned = example.trace('S-02')['result']['alignment']
    require(aligned['peak_id'] == 'locator' and isclose(aligned['shift'], -0.06), '负向漂移没有保留')
    document = deepcopy(example.document)
    document['id'] = 'missing-marker'
    sample = next(row for row in document['injections'] if row['id'] == 'S-01')
    sample['peaks'] = [peak for peak in sample['peaks'] if peak['id'] != 'locator']
    example.client.api('runs', document, status=201)
    actual = example.client.api('runs/missing-marker/injections/S-01')['result']
    require(actual['alignment']['status'] == 'marker_missing' and actual['decisions']['A']['reported'] is None, '定位失败却产生交付值')


def check_assignment(example):
    matched = example.injections['S-02']['assignments']['A']
    require(isclose(matched['aligned_time'], 2.35) and matched['ids'] == ['p-alpha'], '校正后的时间未用于归属')
    ambiguous = example.injections['S-04']['assignments']['B']
    require(ambiguous['status'] == 'ambiguous' and len(ambiguous['ids']) == 2, '歧义峰被错误采纳')


def check_calibration(example):
    curves = example.report['curves']
    require(curves['A']['levels'] == [1, 4, 8] and isclose(curves['A']['slope'], 900), '组分甲曲线错误')
    require(isclose(curves['B']['intercept'], 50) and curves['B']['status'] == 'ready', '组分乙曲线错误')
    document = deepcopy(example.document)
    document['id'] = 'saturated-standard'
    peak = next(p for p in document['injections'][0]['peaks'] if p['id'] == 'p-alpha')
    peak['saturated'] = True
    example.client.api('runs', document, status=201)
    partial = example.client.api('runs/saturated-standard')['curves']
    require(partial['A']['status'] == 'insufficient_levels' and partial['B']['status'] == 'ready', '饱和影响应限于单组分')


def check_blank(example):
    require(example.decision('BLK-01', 'B')['status'] == 'clear', '正常空白错误')
    require(example.decision('BLK-02', 'A')['status'] == 'contaminated', '污染应阻断组分甲')
    require(example.decision('S-02', 'A')['blank_id'] == 'BLK-02', '空白证据引用错误')
    require('blank_blocked' in example.decision('S-02', 'A')['issues'], '后续恢复空白覆盖了历史结果')
    require(example.decision('S-02', 'B')['status'] == 'reportable', '组分甲的污染不应扩散到组分乙')


def check_qc(example):
    failed = example.decision('QC-02', 'A')
    require(failed['status'] == 'failed' and isclose(failed['relative_error'], .25), '核查超差错误')
    require(example.decision('S-03', 'A')['issues'] == ['qc_blocked'], '应保留独立的核查失败原因')
    require(example.decision('S-04', 'A')['qc_id'] == 'QC-03', '恢复的核查未用于后续样品')


def check_reporting(example):
    accepted = example.decision('S-01', 'A')
    require(accepted['status'] == 'reportable' and isclose(accepted['measured'], 3) and isclose(accepted['reported'], 6), '稀释计算错误')
    held = example.decision('S-04', 'A')
    require(held['reported'] is None and held['issues'] == ['out_of_range'], '超量程值泄漏到可交付浓度')


def check_snapshot(example):
    client = example.client
    document = deepcopy(example.document)
    document['id'] = 'held-only'
    document['injections'] = [row for row in document['injections'] if row['kind'] == 'sample']
    client.api('runs', document, status=201)
    require(client.api('runs/held-only/snapshot', {}, status=409)['error'] == 'nothing_to_release', '全暂缓批次被封存')
    created = client.api(example.run + '/snapshot', {}, status=201)
    restored = client.api(example.run + '/snapshot', {})
    require(created['hash'] == restored['hash'] and restored['reused'], '重试未复用交付快照')


def check_export(example):
    client = example.client
    if not client.api(example.run)['snapshot']:
        client.api(example.run + '/snapshot', {}, status=201)
    report = client.api(example.run)
    artifact = client.request(PREFIX + example.run + '/snapshot.json')
    rows = json.loads(artifact)['results']
    require(hashlib.sha256(artifact).hexdigest() == report['snapshot']['hash'], '下载件摘要与封存记录不同')
    require(len(rows) == 4 and not any(row['injection_id'] == 'S-04' for row in rows), '下载含有被暂缓的组分')
    text = client.request(PREFIX + example.run + '/snapshot.csv').decode('utf-8')
    csv_rows = list(csv.DictReader(io.StringIO(text)))
    require([row['peak_id'] for row in csv_rows] == [row['peak_id'] for row in rows], 'CSV 丢失峰来源')
    require(client.api(example.run + '/snapshot/replay')['matches'], '冻结后重算摘要不同')


def check_compare(example):
    client = example.client
    alternate = deepcopy(example.document)
    alternate['id'] = 'solvent-alt'
    sample = next(row for row in alternate['injections'] if row['id'] == 'S-01')
    sample['dilution'] = 3
    client.api('runs', alternate, status=201)
    result = client.api(example.run + '/compare?against=solvent-alt')
    require(len(result['changes']) == 2 and result['unchanged'] == 6, '对比未隔离样品级改变')
    alternate['id'] = 'method-alt'
    alternate['method']['revision'] = 'r2'
    client.api('runs', alternate, status=201)
    require(client.api(example.run + '/compare?against=method-alt', status=409)['error'] == 'incompatible_runs', '不同方法被当成可比')


def register_document(run_id, samples):
    """Build a minimal single-component batch whose samples hit chosen concentrations.

    Calibration is area = 900 * concentration + 100 over levels 1, 4, 8 mg/L, so a
    sample peak at 8200 measures 9 mg/L and is held out of range.
    """
    def peak(pid, time, area):
        return {'id': pid, 'time': time, 'area': area}

    def injection(order, kind, target_area, **extra):
        return {'id': extra.pop('id'), 'order': order, 'kind': kind,
                'peaks': [peak('locator', 1.28, 5100), peak('pa', 2.35, target_area)], **extra}

    injections = [
        injection(1, 'standard', 1000, id='CAL-01', expected={'A': 1}),
        injection(2, 'standard', 3700, id='CAL-02', expected={'A': 4}),
        injection(3, 'standard', 7300, id='CAL-03', expected={'A': 8}),
        injection(4, 'blank', 70, id='BLK-01'),
        injection(5, 'qc', 3700, id='QC-01', expected={'A': 4}),
    ]
    for offset, (sample_id, concentration) in enumerate(samples, start=6):
        injections.append(injection(offset, 'sample', round(900 * concentration + 100, 6),
                                    id=sample_id, sample_id=sample_id))
    return {
        'id': run_id, 'label': f'复核 {run_id}',
        'method': {
            'id': 'single', 'revision': 'r1',
            'marker': {'time': 1.28, 'radius': .12, 'minimum_area': 2500},
            'targets': [{'id': 'A', 'label': '组分甲', 'time': 2.35, 'radius': .045, 'blank_limit': 140}],
            'qc_tolerance': .1, 'min_r2': .995,
        },
        'injections': injections,
    }


def check_register(example):
    client = example.client
    # Both instruments number injections independently, so both runs reuse L1/R1 etc.
    left_doc = register_document('recheck-left', [('L1', 3), ('L2', 7), ('L3', 3)])
    right_doc = register_document('recheck-right', [('R1', 3), ('R2', 7), ('R3', 9)])
    client.api('runs', left_doc, status=201)
    client.api('runs', right_doc, status=201)

    mother_one = {
        'id': 'M-1', 'source': '交接记载 2026-11-20',
        'left': ['L1', 'L2'], 'right': ['R1', 'R2'],
        'relations': [{'left': l, 'right': r, 'source': '交接单 1'} for l in ('L1', 'L2') for r in ('R1', 'R2')],
    }
    mother_two = {
        'id': 'M-2', 'source': '交接记载 2026-11-21',
        'left': ['L3'], 'right': ['R3'],
        'relations': [{'left': 'L3', 'right': 'R3', 'source': '称量记录'}],
    }
    register = {'id': 'split-a', 'version': 1, 'left_run': 'recheck-left', 'right_run': 'recheck-right',
                'allowed_exceedances': 1, 'limits': {'A': 1}, 'mother_samples': [mother_one, mother_two]}

    other_run = register_document('spare-side', [('X1', 3)])
    client.api('runs', other_run, status=201)
    require(client.api('runs/spare-side/registers', register, status=409)['error'] == 'register_mismatch',
            '清单未落在所属两批被接受')

    created = client.api('runs/recheck-left/registers', register, status=201)
    require(created['version'] == 1 and created['supersedes'] is None, '分样清单首版登记错误')
    listed = client.api('runs/recheck-left/registers')
    require(listed['items'][0]['register_id'] == 'split-a' and listed['items'][0]['versions'][0]['version'] == 1,
            '分样清单目录缺失')

    first = client.api('runs/recheck-left/compare?against=recheck-right&register=split-a')
    require(first['register']['version'] == 1 and first['interchange']['pairing']['complete'], '选清单后未给出配对审核')
    require(first['changes'] is not None and 'unchanged' in first, '选清单后应照常提供常规差异')
    component = first['interchange']['targets']['A']
    require((component['min'], component['max']) == (0, 3) and component['conclusion'] == 'insufficient_evidence',
            f'首次清单超差对数应为 0 到 3，实际 {component["min"]} 到 {component["max"]}')

    # New handover evidence excludes both cross correspondences; old version stays intact.
    revised = {**register, 'version': 2, 'supersedes': 'split-a@1',
               'mother_samples': [
                   {**mother_one, 'relations': [{'left': 'L1', 'right': 'R1', 'source': '新凭据'},
                                                {'left': 'L2', 'right': 'R2', 'source': '新凭据'}]},
                   mother_two]}
    client.api('runs/recheck-left/registers', revised, status=201)
    fresh = client.api('runs/recheck-left/compare?against=recheck-right&register=split-a')
    require(fresh['register']['version'] == 2 and fresh['register']['supersedes'] == 'split-a@1', '续版未生效')
    narrowed = fresh['interchange']['targets']['A']
    require((narrowed['min'], narrowed['max']) == (0, 1) and narrowed['conclusion'] == 'interchangeable',
            f'排除交叉后应为 0 到 1 且可确认互换，实际 {narrowed["min"]} 到 {narrowed["max"]}')
    old = client.api('runs/recheck-left/compare?against=recheck-right&register=split-a&version=1')
    require(old['register']['version'] == 1 and (old['interchange']['targets']['A']['min'],
                                                 old['interchange']['targets']['A']['max']) == (0, 3),
            '旧版清单结论被改写')

    detail = client.api('runs/recheck-left/registers/split-a?version=1')
    require(detail['register']['version'] == 1 and detail['comparison']['register']['version'] == 1, '清单回查失败')
    require(detail['comparison']['interchange']['targets']['A']['conclusion'] == 'insufficient_evidence', '回查结论有误')

    # Duplicate registration of the same version is refused.
    require(client.api('runs/recheck-left/registers', register, status=409)['error'] == 'register_conflict', '重复登记未被拒绝')

    # A mother with no complete matching is reported as insufficient pairing data.
    gap = {**register, 'id': 'split-gap', 'version': 1,
           'mother_samples': [{**mother_one, 'relations': [{'left': 'L1', 'right': 'R2', 'source': '仅一条'}]}]}
    client.api('runs/recheck-left/registers', gap, status=201)
    gap_result = client.api('runs/recheck-left/compare?against=recheck-right&register=split-gap')
    require(gap_result['interchange']['pairing']['complete'] is False, '无完整配对未单列')
    require(gap_result['interchange']['targets']['A']['conclusion'] == 'pairing_insufficient', '配对资料不足未体现')
    require(gap_result['interchange']['targets']['A']['min'] is None, '配对资料不足时不应报告上下界')

    # Injections may only be registered once and must be samples from the right side.
    crossed = {**register, 'id': 'split-crossed', 'version': 1,
               'mother_samples': [{**mother_one, 'left': ['L1', 'L1']}]}
    require(client.api('runs/recheck-left/registers', crossed, status=422)['error'] == 'invalid_document',
            '同一进样被重复登记')
    duplicate = {**register, 'id': 'split-dup', 'version': 1,
                 'mother_samples': [mother_one, {**mother_two, 'left': ['L1']}]}
    require(client.api('runs/recheck-left/registers', duplicate, status=422)['error'] == 'invalid_document',
            '跨母样重复登记未被拒绝')
    bad_side = {**register, 'id': 'split-side', 'version': 1,
                'mother_samples': [{**mother_one, 'left': ['CAL-01']}]}
    require(client.api('runs/recheck-left/registers', bad_side, status=422)['error'] == 'invalid_document',
            '非样品进样混入清单')

    # Edge semantics: a difference exactly equal to the limit is acceptable.
    client.api('runs', register_document('edge-left', [('L1', 3)]), status=201)
    client.api('runs', register_document('edge-equal', [('R1', 4)]), status=201)
    client.api('runs', register_document('edge-far', [('R1', 7)]), status=201)

    def pair_register(name, right, allowed, limit=1):
        return {'id': name, 'version': 1, 'left_run': 'edge-left', 'right_run': right,
                'allowed_exceedances': allowed, 'limits': {'A': limit},
                'mother_samples': [{'id': 'M', 'source': '交接', 'left': ['L1'], 'right': ['R1'],
                                    'relations': [{'left': 'L1', 'right': 'R1', 'source': '交接单'}]}]}

    client.api('runs/edge-left/registers', pair_register('edge-eq', 'edge-equal', 0), status=201)
    equal = client.api('runs/edge-left/compare?against=edge-equal&register=edge-eq')['interchange']['targets']['A']
    require((equal['min'], equal['max']) == (0, 0) and equal['conclusion'] == 'interchangeable',
            '等于差限应算合格')
    client.api('runs/edge-left/registers', pair_register('edge-far', 'edge-far', 0), status=201)
    far = client.api('runs/edge-left/compare?against=edge-far&register=edge-far')['interchange']['targets']['A']
    require((far['min'], far['max']) == (1, 1) and far['conclusion'] == 'not_interchangeable',
            '超过差限且容许为零应确认不可换')


def check_trace(example):
    observed = example.trace('S-01')
    ids = {peak['id'] for peak in observed['source']['peaks']}
    corrected = observed['windows']['targets'][0]
    require(isclose(corrected['raw']['min'] - corrected['aligned']['min'], .04), '证据原始窗未使用定位峰偏移')
    require(set(observed['result']['assignments']['A']['ids']).issubset(ids), '证据峰无法追溯')
    example.client.api(example.run + '/injections/absent', status=404)
    require('色谱批次核算台' in example.client.request('/').decode(), '工作台首页不可用')
    for file in sorted(Path('web/js').glob('*.js')):
        require(example.client.request('/js/' + file.name) == file.read_bytes(), f'{file.name} 不可用')
    for file in sorted(Path('web/css').glob('*.css')):
        require(example.client.request('/css/' + file.name) == file.read_bytes(), f'{file.name} 不可用')


def run_store_verification(path):
    process = subprocess.run([sys.executable, '-m', 'tools.check_store', str(path)],
                             capture_output=True, text=True, timeout=10)
    return process.returncode, json.loads(process.stdout)


def check_integrity(example):
    if not example.client.api(example.run)['snapshot']:
        example.client.api(example.run + '/snapshot', {}, status=201)
    counterpart = deepcopy(example.document)
    counterpart['id'] = 'integrity-side'
    example.client.api('runs', counterpart, status=201)
    example.client.api(example.run + '/registers', {
        'id': 'integrity-split', 'version': 1, 'left_run': example.document['id'],
        'right_run': 'integrity-side', 'allowed_exceedances': 1, 'limits': {'A': 2, 'B': 2},
        'mother_samples': [{'id': 'M-1', 'source': '交接记录', 'left': ['S-01'], 'right': ['S-01'],
                            'relations': [{'left': 'S-01', 'right': 'S-01', 'source': '交接单'}]}],
    }, status=201)
    code, intact = run_store_verification(example.database)
    require(code == 0 and intact['ok'] and intact['snapshots'] >= 1 and intact['registers'] >= 1,
            '真实落库结果无法通过完整性检查')
    corrupt_copy = Path(example.database).with_name('corrupt.sqlite3')
    live = sqlite3.connect(example.database)
    copy = sqlite3.connect(corrupt_copy)
    try:
        live.backup(copy)
        with copy:
            copy.execute("UPDATE snapshots SET content = '{}' WHERE run_id = ?", (example.document['id'],))
    finally:
        live.close()
        copy.close()
    code, result = run_store_verification(corrupt_copy)
    require(code == 1 and result['issues'] == [{'id': example.document['id'], 'issues': ['snapshot_replay', 'snapshot_digest']}],
            '完整性检查未识别交付副本中的损坏')
    register_copy = Path(example.database).with_name('register-corrupt.sqlite3')
    live = sqlite3.connect(example.database)
    copy = sqlite3.connect(register_copy)
    try:
        live.backup(copy)
        with copy:
            copy.execute("UPDATE registers SET content = replace(content, '交接单', '伪造单')")
    finally:
        live.close()
        copy.close()
    code, result = run_store_verification(register_copy)
    require(code == 1 and any('register_digest' in item['issues'] for item in result['issues']),
            '完整性检查未识别分样清单改写')
    require(run_store_verification(example.database)[0] == 0, '核验扰动了原始数据库')


CHECKS = {fn.__name__.removeprefix('check_'): fn for fn in (
    check_preview, check_ingest, check_csv, check_alignment, check_assignment, check_calibration,
    check_blank, check_qc, check_reporting, check_snapshot, check_export, check_compare, check_register,
    check_trace, check_integrity,
)}


def run_workflows(selection):
    with TemporaryDirectory(prefix='chromaledger-check-') as scratch:
        db = str(Path(scratch) / 'smoke.sqlite3')
        command = [sys.executable, '-m', 'chromaledger', '--db', db, '--port', '0']
        with TemporaryDirectory(prefix='chromaledger-log-') as logs, (Path(logs) / 'server.log').open('w+') as log:
            with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log, text=True) as process:
                try:
                    with selectors.DefaultSelector() as readable:
                        readable.register(process.stdout, selectors.EVENT_READ)
                        require(bool(readable.select(timeout=8)), '服务器未在八秒内启动')
                    url = process.stdout.readline().strip().split()[-1:]
                    require(url and url[0].startswith('http://127.0.0.1:'), '服务器启动失败')
                    client = Client(url[0])
                    require(client.api('health')['status'] == 'ready', '数据库未就绪')
                    example = Example(client, db)
                    for workflow in selection:
                        CHECKS[workflow](example)
                        print(f'通过 {workflow}')
                except (OSError, RuntimeError, ValueError, KeyError):
                    log.seek(0)
                    print(log.read(), file=sys.stderr)
                    raise
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workflow', nargs='?', choices=tuple(CHECKS))
    args = parser.parse_args()
    run_workflows([args.workflow] if args.workflow else CHECKS)
