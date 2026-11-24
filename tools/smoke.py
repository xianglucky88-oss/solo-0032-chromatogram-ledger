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
    code, intact = run_store_verification(example.database)
    require(code == 0 and intact['ok'] and intact['snapshots'] == 1, '真实落库结果无法通过完整性检查')
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
    require(code == 1 and result['issues'] == [{'id': example.document['id'],
                                                'issues': ['snapshot_replay', 'snapshot_digest', 'delivery_replay']}],
            '完整性检查未识别交付副本中的损坏')
    require(run_store_verification(example.database)[0] == 0, '核验扰动了原始数据库')


def run_verify(credential_path, delivery_path, *extra):
    return subprocess.run([sys.executable, '-m', 'chromaledger.verify', str(credential_path), str(delivery_path), *extra],
                          capture_output=True, text=True, timeout=10)


def canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def check_delivery(example):
    client = example.client
    document = deepcopy(example.document)
    document['id'] = 'split-batch'
    second = deepcopy(next(row for row in document['injections'] if row['id'] == 'S-01'))
    second['id'] = 'S-01R'
    second['order'] = 14
    second['peaks'] = [peak for peak in second['peaks'] if peak['id'] != 'p-beta']
    document['injections'].append(second)
    client.api('runs', document, status=201)
    report = client.api('runs/split-batch')
    decisions = {row['id']: row['decisions'] for row in report['injections']}
    require(decisions['S-01R']['A']['status'] == 'reportable' and decisions['S-01R']['B']['status'] == 'held',
            '第二针应仅组分甲可交付')
    created = client.api('runs/split-batch/snapshot', {}, status=201)
    require(created['deliveries'] == ['VIAL-17', 'VIAL-19', 'VIAL-22'], '封存时未按样品签发凭据')
    again = client.api('runs/split-batch/snapshot', {})
    require(again['reused'] and again['hash'] == created['hash'] and again['deliveries'] == created['deliveries'],
            '重试未取回原凭据')
    items = {item['sample_id']: item for item in client.api('runs/split-batch/deliveries')['items']}
    require(set(items) == {'VIAL-17', 'VIAL-19', 'VIAL-22'} and items['VIAL-17']['row_count'] == 3, '分样凭据目录错误')
    client.request(PREFIX + 'runs/split-batch/deliveries/VIAL-25/delivery.json', status=404)
    credential = client.request(PREFIX + 'runs/split-batch/deliveries/VIAL-17/credential.json')
    delivery = client.request(PREFIX + 'runs/split-batch/deliveries/VIAL-17/delivery.json')
    require(client.request(PREFIX + 'runs/split-batch/deliveries/VIAL-17/credential.json') == credential,
            '凭据再次获取不一致')
    store = sqlite3.connect(example.database)
    try:
        stored = store.execute('SELECT credential FROM deliveries WHERE run_id = ? AND sample_id = ?',
                               ('split-batch', 'VIAL-17')).fetchone()
    finally:
        store.close()
    require(stored and stored[0].encode('utf-8') == credential, '凭据未持久化，重启后无法取回')
    rows = json.loads(delivery)['results']
    identities = {(row['injection_id'], row['target_id']) for row in rows}
    require(identities == {('S-01', 'A'), ('S-01', 'B'), ('S-01R', 'A')}, '交付行不是该样品全部可交付行')
    text = delivery.decode('utf-8')
    require(all(row['sample_id'] == 'VIAL-17' for row in rows)
            and not any(other in text for other in ('VIAL-19', 'VIAL-22', 'VIAL-25')), '随件材料混入其他样品')
    with TemporaryDirectory(prefix='chromaledger-delivery-') as scratch:
        scratch = Path(scratch)
        cred_file, drop_file = scratch / 'credential.json', scratch / 'delivery.json'
        cred_file.write_bytes(credential)
        drop_file.write_bytes(delivery)
        accepted = run_verify(cred_file, drop_file, '--sample', 'VIAL-17')
        require(accepted.returncode == 0, f'有效交付被拒收：{accepted.stdout}{accepted.stderr}')
        require(run_verify(cred_file, drop_file).returncode == 0, '未指定样品号时有效交付被拒收')
        require(run_verify(cred_file, drop_file, '--sample', 'VIAL-19').returncode == 1, '申领样品号不符却通过验件')

        def rewrite(doc):
            drop_file.write_bytes(canonical_bytes(doc))
            return run_verify(cred_file, drop_file).returncode

        original = json.loads(delivery)
        dropped = [row for row in original['results'] if row['injection_id'] != 'S-01R']
        require(rewrite({**original, 'results': dropped}) == 1, '缺少第二针甲的交付被接受')
        first_a = next(row for row in original['results'] if row['injection_id'] == 'S-01' and row['target_id'] == 'A')
        require(rewrite({**original, 'results': [*dropped, first_a]}) == 1, '重复第一针甲补足行数的交付被接受')
        tampered = deepcopy(original)
        tampered['results'][0]['concentration'] = 99.5
        tampered['snapshot_sha256'] = hashlib.sha256(canonical_bytes(tampered['results'])).hexdigest()
        require(rewrite(tampered) == 1, '改数值并重算随件摘要的交付被接受')
        alternate = deepcopy(document)
        alternate['id'] = 'split-batch-alt'
        next(row for row in alternate['injections'] if row['id'] == 'S-01')['dilution'] = 3
        client.api('runs', alternate, status=201)
        client.api('runs/split-batch-alt/snapshot', {}, status=201)
        drop_file.write_bytes(client.request(PREFIX + 'runs/split-batch-alt/deliveries/VIAL-17/delivery.json'))
        require(run_verify(cred_file, drop_file).returncode == 1, '另一批同号样品的交付被接受')
        other_cred = scratch / 'other-credential.json'
        other_cred.write_bytes(client.request(PREFIX + 'runs/split-batch/deliveries/VIAL-19/credential.json'))
        drop_file.write_bytes(delivery)
        require(run_verify(other_cred, drop_file).returncode == 1, '凭据与交付件张冠李戴却通过验件')
        empty_doc = {'schema': 1, 'kind': 'chromaledger-delivery', 'run_id': 'split-batch', 'sample_id': 'VIAL-25',
                     'snapshot_sha256': hashlib.sha256(b'empty').hexdigest(), 'results': []}
        empty_raw = canonical_bytes(empty_doc)
        empty_cred = {'schema': 1, 'kind': 'chromaledger-delivery-credential', 'run_id': 'split-batch',
                      'sample_id': 'VIAL-25', 'snapshot_sha256': empty_doc['snapshot_sha256'],
                      'delivery_sha256': hashlib.sha256(empty_raw).hexdigest(), 'row_count': 0,
                      'sealed_at': '2026-11-24T00:00:00.000Z'}
        (scratch / 'empty-credential.json').write_bytes(canonical_bytes(empty_cred))
        (scratch / 'empty-delivery.json').write_bytes(empty_raw)
        require(run_verify(scratch / 'empty-credential.json', scratch / 'empty-delivery.json').returncode == 1,
                '空交付被认成有效交付')
    live = sqlite3.connect(example.database)
    try:
        with live:
            live.execute('DELETE FROM deliveries WHERE run_id = ?', ('split-batch',))
    finally:
        live.close()
    require(client.api('runs/split-batch/deliveries')['items'] == [], '旧封存件不应列出分样凭据')
    client.request(PREFIX + 'runs/split-batch/deliveries/VIAL-17/credential.json', status=404)
    artifact = client.request(PREFIX + 'runs/split-batch/snapshot.json')
    require(hashlib.sha256(artifact).hexdigest() == created['hash'], '旧封存件原下载流程失效')
    require(client.api('runs/split-batch/snapshot/replay')['matches'], '旧封存件原复算流程失效')
    reused = client.api('runs/split-batch/snapshot', {})
    require(reused['reused'] and reused['deliveries'] == [], '旧封存件被后补了凭据')


CHECKS = {fn.__name__.removeprefix('check_'): fn for fn in (
    check_preview, check_ingest, check_csv, check_alignment, check_assignment, check_calibration,
    check_blank, check_qc, check_reporting, check_snapshot, check_export, check_compare, check_trace, check_integrity,
    check_delivery,
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
