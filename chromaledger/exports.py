import csv
import io
import json

from .encoding import digest
from .errors import Rejected
from .repository import delivery_row
from .snapshots import get_snapshot

CSV_FIELDS = ('sample_id', 'injection_id', 'order', 'target_id', 'concentration', 'dilution', 'peak_id', 'blank_id', 'qc_id')


def export_snapshot(conn, run_id, form):
    snapshot = get_snapshot(conn, run_id)
    if form == 'json':
        return snapshot['content'].encode('utf-8'), 'application/json'
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, CSV_FIELDS, lineterminator='\r\n')
    writer.writeheader()
    writer.writerows(json.loads(snapshot['content'])['results'])
    return output.getvalue().encode('utf-8'), 'text/csv; charset=utf-8'


def export_delivery(conn, run_id, sample_id, part):
    row = delivery_row(conn, run_id, sample_id)
    if row is None:
        raise Rejected('delivery_not_found', '该样品在此次封存中没有分样交付件。', status=404)
    if digest(row['content']) != row['hash']:
        raise Rejected('delivery_corrupt', '分样交付件与封存摘要不一致。', status=409)
    text = row['credential'] if part == 'credential' else row['content']
    return text.encode('utf-8'), 'application/json; charset=utf-8'
