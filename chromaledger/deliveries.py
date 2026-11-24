"""分样交付：封存时按样品从冻结内容派生交付件与封存凭据。

委托方先收到凭据，再收到交付件，离线核对两者即可确认收齐了
封存时本样品的全部可交付行。交付件只含本样品的冻结行，不含
其他样品号、其他样品的逐针结果或可猜测内容的逐行核对值。
"""
import json

from .encoding import canonical, digest
from .errors import Rejected
from .repository import delivery_rows, snapshot_row

DELIVERY_KIND = 'chromaledger-delivery'
CREDENTIAL_KIND = 'chromaledger-delivery-credential'


def build_deliveries(snapshot_hash, snapshot_content, sealed_at):
    frozen = json.loads(snapshot_content)
    rows_by_sample = {}
    for row in frozen['results']:
        rows_by_sample.setdefault(row['sample_id'], []).append(row)
    documents = []
    for sample_id in sorted(rows_by_sample):
        rows = rows_by_sample[sample_id]
        document = {'schema': 1, 'kind': DELIVERY_KIND, 'run_id': frozen['run_id'],
                    'sample_id': sample_id, 'snapshot_sha256': snapshot_hash, 'results': rows}
        content = canonical(document)
        credential = {'schema': 1, 'kind': CREDENTIAL_KIND, 'run_id': frozen['run_id'],
                      'sample_id': sample_id, 'snapshot_sha256': snapshot_hash,
                      'delivery_sha256': digest(content), 'row_count': len(rows), 'sealed_at': sealed_at}
        documents.append({'sample_id': sample_id, 'content': content, 'credential': canonical(credential)})
    return documents


def issue_deliveries(conn, run_id, snapshot):
    documents = build_deliveries(snapshot['hash'], snapshot['content'], snapshot['created_at'])
    for document in documents:
        conn.execute('INSERT INTO deliveries (run_id, sample_id, hash, content, credential) VALUES (?, ?, ?, ?, ?)',
                     (run_id, document['sample_id'], digest(document['content']), document['content'],
                      document['credential']))
    return [document['sample_id'] for document in documents]


def delivery_samples(conn, run_id):
    return [row['sample_id'] for row in delivery_rows(conn, run_id)]


def summarize(row):
    credential = json.loads(row['credential'])
    return {'sample_id': row['sample_id'], 'row_count': credential['row_count'], 'delivery_sha256': row['hash']}


def list_deliveries(conn, run_id):
    if snapshot_row(conn, run_id) is None:
        raise Rejected('snapshot_not_found', '该批次尚无交付快照。', status=404)
    return [summarize(row) for row in delivery_rows(conn, run_id)]
