from dataclasses import dataclass, field
from pathlib import Path
import re
from urllib.parse import parse_qs

from .comparison import compare
from .database import connect
from .deliveries import list_deliveries
from .encoding import canonical, decode_upload
from .errors import Rejected
from .evidence import injection_trace, report_view
from .exports import export_delivery, export_snapshot
from .intake import ingest, preview
from .repository import catalog, find_run
from .snapshots import freeze, replay

API = '/chromaledger/api/'
BASE = Path(__file__).resolve().parent.parent
RUN_ID = r'([A-Za-z0-9][A-Za-z0-9_.-]{0,47})'
RUN_PATH = re.compile(rf'runs/{RUN_ID}(?:/(.*))?\Z')
TRACE = re.compile(rf'injections/{RUN_ID}\Z')
DELIVERY = re.compile(rf'deliveries/{RUN_ID}/(credential|delivery)\.json\Z')


@dataclass(frozen=True)
class Response:
    body: bytes
    mime: str
    status: int = 200
    headers: dict = field(default_factory=dict)


def json_response(value, status=200):
    return Response(canonical(value).encode('utf-8'), 'application/json; charset=utf-8', status)


def offset_from(query):
    try:
        fields = parse_qs(query, keep_blank_values=True, max_num_fields=3)
    except ValueError:
        fields = {'invalid': []}
    values = fields.get('offset', ['0'])
    if set(fields) - {'offset'} or len(values) != 1 or not re.fullmatch(r'[0-9]{1,8}', values[0]):
        raise Rejected('invalid_offset', '无效的目录偏移。', status=400)
    return int(values[0])


def run_route(conn, method, run_id, action, query):
    if method == 'GET':
        if action is None:
            return json_response(report_view(conn, run_id))
        trace = TRACE.fullmatch(action)
        if trace:
            return json_response(injection_trace(conn, run_id, trace[1]))
        if action == 'source':
            return json_response(find_run(conn, run_id)['source'])
        if action == 'snapshot/replay':
            return json_response(replay(conn, run_id))
        if action in ('snapshot.json', 'snapshot.csv'):
            form = action.split('.')[1]
            body, mime = export_snapshot(conn, run_id, form)
            filename = f'{run_id}-snapshot.{form}'
            return Response(body, mime, headers={'Content-Disposition': f'attachment; filename="{filename}"'})
        if action == 'deliveries':
            return json_response({'items': list_deliveries(conn, run_id)})
        delivery = DELIVERY.fullmatch(action)
        if delivery:
            body, mime = export_delivery(conn, run_id, delivery[1], delivery[2])
            filename = f'{run_id}-{delivery[1]}-{delivery[2]}.json'
            return Response(body, mime, headers={'Content-Disposition': f'attachment; filename="{filename}"'})
        if action == 'compare':
            try:
                fields = parse_qs(query, max_num_fields=1)
            except ValueError:
                fields = {}
            against = fields.get('against', [''])
            if len(against) != 1 or not re.fullmatch(RUN_ID, against[0]):
                raise Rejected('invalid_comparison', '请指定有效的对比批次。', status=400)
            return json_response(compare(conn, run_id, against[0]))
    elif action == 'snapshot':
        result = freeze(conn, run_id)
        return json_response(result, 200 if result['reused'] else 201)
    raise Rejected('not_found', '没有该接口。', status=404)


def dispatch(db, method, path, query, read_body):
    route = path[len(API):]
    if method == 'GET':
        if route == 'health':
            with connect(db) as conn:
                conn.execute('SELECT id FROM runs LIMIT 1').fetchone()
            return json_response({'status': 'ready'})
        if route == 'example':
            return Response((BASE / 'examples' / 'peak-series.json').read_bytes(), 'application/json; charset=utf-8')
    if method == 'POST' and route == 'preview':
        return json_response(preview(decode_upload(read_body())))
    with connect(db) as conn:
        if route == 'runs':
            if method == 'GET':
                return json_response(catalog(conn, 40, offset_from(query)))
            result = ingest(conn, decode_upload(read_body()))
            return json_response(result, 200 if result['reused'] else 201)
        match = RUN_PATH.fullmatch(route)
        if match:
            if method == 'POST':
                if decode_upload(read_body()) != {}:
                    raise Rejected('invalid_action', '提交封存操作时请使用空对象。')
            return run_route(conn, method, match[1], match[2], query)
    raise Rejected('not_found', '没有该接口。', status=404)
