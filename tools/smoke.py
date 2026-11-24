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

from chromaledger.archive_database import initialize as initialize_archive
from chromaledger.archive_intake import receive_backup
from chromaledger.database import connect as open_store, initialize
from chromaledger.encoding import digest
from chromaledger.errors import Rejected
from chromaledger.integrity import inspect_archive
from chromaledger.intake import ingest
from chromaledger.snapshots import freeze

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
    def __init__(self, client, database, archive_database):
        self.client = client
        self.database = database
        self.archive_database = archive_database
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


def checkpoint(path):
    conn = sqlite3.connect(path)
    try:
        conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    finally:
        conn.close()


def make_workstation_backup(scratch, name, batches, sealed=()):
    """Build a workstation-style SQLite backup with the real intake pipeline."""
    path = Path(scratch) / name
    initialize(path)
    for document in batches:
        with open_store(path) as conn:
            ingest(conn, document)
    for batch_id in sealed:
        with open_store(path) as conn:
            freeze(conn, batch_id)
    checkpoint(path)
    return path


def variant(document, batch_id, *, dilution=None, label_suffix=''):
    copy = deepcopy(document)
    copy['id'] = batch_id
    if label_suffix:
        copy['label'] = f'{copy["label"]} {label_suffix}'
    if dilution is not None:
        next(row for row in copy['injections'] if row['id'] == 'S-01')['dilution'] = dilution
    return copy


def check_archive(example):
    client = example.client
    archive_db = Path(example.archive_database)
    with TemporaryDirectory(prefix='chromaledger-archive-') as scratch:
        b07 = variant(example.document, 'B07', label_suffix='东')
        west_b07 = variant(example.document, 'B07', dilution=3, label_suffix='西')
        b08 = variant(example.document, 'B08', label_suffix='东')
        b09 = variant(example.document, 'B09', label_suffix='东')

        east_first = make_workstation_backup(scratch, 'east-first.sqlite3', [b07])
        west_first = make_workstation_backup(scratch, 'west-first.sqlite3', [west_b07], sealed=['B07'])
        east_second = make_workstation_backup(
            scratch, 'east-second.sqlite3', [b07, b08], sealed=['B07'])

        first = receive_backup(archive_db, 'east', east_first)
        require(not first['reused'] and first['added_batches'] == ['B07']
                and first['completed_seals'] == [], '东站首份未封存备份入档结果错误')
        west = receive_backup(archive_db, 'west', west_first)
        require(west['added_batches'] == ['B07'] and west['completed_seals'] == ['B07'],
                '西站已封存 B07 应直接入档封存')
        # 同号批次在不同工作站下各自成记录，输入与封存摘要不同也不冲突
        catalog = client.api('archive/runs')
        require({(item['station_id'], item['batch_id']) for item in catalog['items']}
                >= {('east', 'B07'), ('west', 'B07')}, '归档目录未按工作站区分同号批次')

        second = receive_backup(archive_db, 'east', east_second)
        require(second['added_batches'] == ['B08'] and second['completed_seals'] == ['B07'],
                '晚到备份应补齐东站 B07 封存并只新增 B08')
        # 较早副本晚到不得撤掉已收封存或丢掉其他批次
        again = receive_backup(archive_db, 'east', east_first)
        require(again['reused'] and again['added_batches'] == [] and again['completed_seals'] == [],
                '原样重交应识别为同一份收件且不改动档案')
        east_view = client.api('archive/stations/east')
        require(east_view['batch_count'] == 2 and east_view['snapshot_count'] == 1,
                '重交早期副本不应撤掉封存或其他批次')

        # 两站 B07 均可下载原样封存件并复算原摘要
        for station in ('east', 'west'):
            replay = client.api(f'archive/stations/{station}/runs/B07/snapshot/replay')
            require(replay['matches'], f'{station} B07 封存摘要复算不一致')
            artifact = client.request(PREFIX + f'archive/stations/{station}/runs/B07/snapshot.json')
            require(hashlib.sha256(artifact).hexdigest() == replay['stored_sha256'],
                    f'{station} B07 下载件不是原样封存件')
            report = client.api(f'archive/stations/{station}/runs/B07')
            require(report['input_hash'] == replay['input_sha256'], f'{station} B07 输入摘要不一致')
        client.request(PREFIX + 'archive/stations/west/runs/B07/snapshot.csv')

        # 第三站提交与西站完全相同的输入及封存：分别可查且原摘要相同
        north_backup = make_workstation_backup(
            scratch, 'north-first.sqlite3', [variant(west_b07, 'B07')], sealed=['B07'])
        north = receive_backup(archive_db, 'north', north_backup)
        require(north['added_batches'] == ['B07'], '第三站相同内容应独立入档')
        west_report = client.api('archive/stations/west/runs/B07')
        north_report = client.api('archive/stations/north/runs/B07')
        require(west_report['input_hash'] == north_report['input_hash']
                and west_report['snapshot']['hash'] == north_report['snapshot']['hash'],
                '相同输入与封存的两站记录摘要应一致')
        stations = {row['station_id']: row for row in client.api('archive/stations')['items']}
        require(set(stations) == {'east', 'west', 'north'}, '总台目录应能分清来源工作站')

        # 输入发生变化的 B07 与新批次 B09 同份到达：整份拒收，B09 不得落地
        changed_b07 = variant(b07, 'B07', dilution=4)
        input_conflict = make_workstation_backup(
            scratch, 'east-input-conflict.sqlite3', [changed_b07, b09])
        try:
            receive_backup(archive_db, 'east', input_conflict)
            raise RuntimeError('同站同号输入变化必须作为冲突整份拒收')
        except Rejected as exc:
            require(exc.code == 'archive_input_conflict' and exc.status == 409, '输入冲突的错误类型不对')
        require(all(item['batch_id'] != 'B09' for item in client.api('archive/runs?station=east')['items']),
                '拒收的备份不得让 B09 出现在目录')

        # B09 封存被改结果后重算文件摘要：核验失败，整份拒收，B09 仍不得入档
        tampered = make_workstation_backup(scratch, 'east-tamper.sqlite3', [b09], sealed=['B09'])
        raw = sqlite3.connect(tampered)
        try:
            content = json.loads(raw.execute("SELECT content FROM snapshots WHERE run_id = 'B09'").fetchone()[0])
            content['results'][0]['concentration'] = 999
            new_content = json.dumps(content, ensure_ascii=False, separators=(',', ':'), sort_keys=True)
            with raw:
                raw.execute("UPDATE snapshots SET content = ?, hash = ? WHERE run_id = 'B09'",
                            (new_content, digest(new_content)))
        finally:
            raw.close()
        checkpoint(tampered)
        try:
            receive_backup(archive_db, 'east', tampered)
            raise RuntimeError('仅内部摘要自洽但无法由原输入核回的封存必须拒收')
        except Rejected as exc:
            require(exc.code == 'backup_verification_failed', '封存核验失败的错误类型不对')
        require(all(item['batch_id'] != 'B09' for item in client.api('archive/runs?station=east')['items']),
                '核验失败后 B09 仍不得入档')

        # 按来源打开原输入、报告与进样证据
        source = client.api('archive/stations/east/runs/B08/source')
        require(source['id'] == 'B08' and source['injections'], '无法按来源打开归档原输入')
        trace = client.api('archive/stations/east/runs/B08/injections/S-01')
        require(trace['source']['id'] == 'S-01' and trace['result']['decisions'], '无法读取归档进样证据')
        receipts = client.api('archive/stations/east/receipts')
        require(len(receipts['items']) == 2, '每份不同的备份都应留下一条收件记录')
        require({tuple(row['batch_ids']) for row in receipts['items']} == {('B07',), ('B07', 'B08')},
                '收件记录应保留各备份的批次清单')

        # 源备份移走后，总台自己的目录中的读取、下载和复算仍能完成
        relocated = Path(scratch) / 'moved'
        relocated.mkdir()
        for backup in Path(scratch).glob('*.sqlite3*'):
            backup.rename(relocated / backup.name)
        replay = client.api('archive/stations/east/runs/B07/snapshot/replay')
        require(replay['matches'], '源备份移走后封存复算失败')
        artifact = client.request(PREFIX + 'archive/stations/east/runs/B07/snapshot.json')
        require(hashlib.sha256(artifact).hexdigest() == replay['stored_sha256'],
                '源备份移走后无法下载原样封存件')
        require(client.api('archive/stations/west/runs/B07/source')['id'] == 'B07',
                '源备份移走后无法打开原输入')

        intact = inspect_archive(archive_db)
        require(intact['ok'] and intact['runs'] == 4 and intact['snapshots'] == 3
                and intact['stations'] == 3, '归档库只读完整性核验失败')
        code, cli = run_archive_cli(archive_db, 'east', relocated / 'east-first.sqlite3')
        require(code == 0 and cli['ok'] and cli['reused'], '命令行收件无法识别原样重交')
        # 未参加归档接收的本机批次仍走原来的批次号入口
        require(client.api('runs')['items'], '本机批次目录不应被归档接收改动')
        client.api('archive/stations/unknown', status=404)
        client.api('archive/stations/east/runs/B09', status=404)


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
    require(code == 1 and result['issues'] == [{'id': example.document['id'], 'issues': ['snapshot_replay', 'snapshot_digest']}],
            '完整性检查未识别交付副本中的损坏')
    require(run_store_verification(example.database)[0] == 0, '核验扰动了原始数据库')


def run_archive_cli(archive_db, station_id, backup):
    process = subprocess.run(
        [sys.executable, '-m', 'tools.intake_archive', station_id, str(backup),
         '--archive-db', str(archive_db)],
        capture_output=True, text=True, timeout=15,
    )
    return process.returncode, json.loads(process.stdout)


CHECKS = {fn.__name__.removeprefix('check_'): fn for fn in (
    check_preview, check_ingest, check_csv, check_alignment, check_assignment, check_calibration,
    check_blank, check_qc, check_reporting, check_snapshot, check_export, check_compare, check_trace,
    check_integrity, check_archive,
)}


def run_workflows(selection):
    with TemporaryDirectory(prefix='chromaledger-check-') as scratch:
        db = str(Path(scratch) / 'smoke.sqlite3')
        archive_db = str(Path(scratch) / 'archive.sqlite3')
        initialize_archive(archive_db)
        command = [sys.executable, '-m', 'chromaledger', '--db', db,
                   '--archive-db', archive_db, '--port', '0']
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
                    example = Example(client, db, archive_db)
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
