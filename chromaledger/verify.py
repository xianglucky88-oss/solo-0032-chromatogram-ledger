"""委托方离线验件：只信任先收到的封存凭据和自己申领的样品号。

用法：python3 -m chromaledger.verify 凭据.json 交付件.json [--sample 样品号]

交付件自带的任何摘要都不作为验件依据；交付件内容的 SHA-256 必须与
先收到的凭据中的 delivery_sha256 完全一致。凭据只为有可交付行的样品
签发，空交付不会被认成有效交付。
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

from .deliveries import CREDENTIAL_KIND, DELIVERY_KIND
from .encoding import invalid_constant, unique_pairs
from .fields import KEY

HEX = re.compile(r'[0-9a-f]{64}\Z')


def parse_json(raw, problems, what):
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_pairs, parse_constant=invalid_constant)
    except (UnicodeError, ValueError, RecursionError):
        problems.append(f'{what}不是有效的 JSON。')
        return None
    if not isinstance(value, dict):
        problems.append(f'{what}应为 JSON 对象。')
        return None
    return value


def check_credential(credential, problems):
    if credential.get('kind') != CREDENTIAL_KIND or credential.get('schema') != 1:
        problems.append('凭据类型或版本无法识别。')
        return
    for field in ('run_id', 'sample_id'):
        if not isinstance(credential.get(field), str) or not KEY.fullmatch(credential[field]):
            problems.append(f'凭据的编号字段 {field} 无效。')
    for field in ('snapshot_sha256', 'delivery_sha256'):
        if not isinstance(credential.get(field), str) or not HEX.fullmatch(credential[field]):
            problems.append(f'凭据的摘要字段 {field} 无效。')
    if type(credential.get('row_count')) is not int or credential['row_count'] < 1:
        problems.append('凭据的交付行数无效。')
    if not isinstance(credential.get('sealed_at'), str) or not credential['sealed_at']:
        problems.append('凭据缺少封存时间。')


def check_delivery(document, problems):
    if document.get('kind') != DELIVERY_KIND or document.get('schema') != 1:
        problems.append('交付件类型或版本无法识别。')
        return
    for field in ('run_id', 'sample_id'):
        if not isinstance(document.get(field), str) or not KEY.fullmatch(document[field]):
            problems.append(f'交付件的编号字段 {field} 无效。')
    if not isinstance(document.get('snapshot_sha256'), str) or not HEX.fullmatch(document['snapshot_sha256']):
        problems.append('交付件的封存摘要无效。')
    if not isinstance(document.get('results'), list) or not document['results']:
        problems.append('交付件没有交付行。')


def verify_pair(credential_raw, delivery_raw, expect_sample=None):
    """返回 (凭据, 问题列表)；问题列表为空时才可接受交付件。"""
    problems = []
    credential = parse_json(credential_raw, problems, '封存凭据')
    document = parse_json(delivery_raw, problems, '交付件')
    if credential is None or document is None:
        return None, problems
    check_credential(credential, problems)
    check_delivery(document, problems)
    if problems:
        return None, problems
    if credential['sample_id'] != document['sample_id']:
        problems.append('凭据与交付件的样品号不一致。')
    if expect_sample is not None and credential['sample_id'] != expect_sample:
        problems.append(f"凭据样品号 {credential['sample_id']} 与申领的样品号 {expect_sample} 不一致。")
    if credential['run_id'] != document['run_id']:
        problems.append('凭据与交付件的批次号不一致。')
    if credential['snapshot_sha256'] != document['snapshot_sha256']:
        problems.append('交付件声明的封存摘要与凭据不一致。')
    if hashlib.sha256(delivery_raw).hexdigest() != credential['delivery_sha256']:
        problems.append('交付件内容与凭据摘要不一致。')
    if len(document['results']) != credential['row_count']:
        problems.append('交付行数与凭据不符。')
    if any(not isinstance(row, dict) or row.get('sample_id') != credential['sample_id'] for row in document['results']):
        problems.append('交付件混入其他样品的行。')
    return credential, problems


def main(argv=None):
    parser = argparse.ArgumentParser(description='离线核验分样交付件与封存时签发的凭据')
    parser.add_argument('credential', type=Path, help='封存时先收到的凭据 JSON')
    parser.add_argument('delivery', type=Path, help='随后收到的交付件 JSON')
    parser.add_argument('--sample', help='申领的样品号，提供时须与凭据一致')
    args = parser.parse_args(argv)
    try:
        credential_raw = args.credential.read_bytes()
        delivery_raw = args.delivery.read_bytes()
    except OSError as exc:
        print(f'拒收：无法读取文件 {exc.filename}。')
        return 1
    credential, problems = verify_pair(credential_raw, delivery_raw, args.sample)
    if problems:
        for problem in problems:
            print(f'拒收：{problem}')
        return 1
    print(f"接受：批次 {credential['run_id']} 样品 {credential['sample_id']} 的 "
          f"{credential['row_count']} 行交付与封存凭据一致。")
    print(f"封存摘要 {credential['snapshot_sha256']}，封存时间 {credential['sealed_at']}。")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
