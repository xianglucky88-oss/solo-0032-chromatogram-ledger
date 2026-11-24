import csv
import io
import json

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
