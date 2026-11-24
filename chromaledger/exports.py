import csv
import io
import json

from .snapshots import CSV_FIELDS_V1, CSV_FIELDS_V2, get_snapshot


def export_snapshot(conn, run_id, form):
    snapshot = get_snapshot(conn, run_id)
    if form == 'json':
        return snapshot['content'].encode('utf-8'), 'application/json'
    payload = json.loads(snapshot['content'])
    fields = CSV_FIELDS_V2 if payload.get('schema') == 2 else CSV_FIELDS_V1
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fields, lineterminator='\r\n', extrasaction='ignore')
    writer.writeheader()
    writer.writerows(payload['results'])
    return output.getvalue().encode('utf-8'), 'text/csv; charset=utf-8'
