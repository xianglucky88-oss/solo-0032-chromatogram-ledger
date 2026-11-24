import json
from dataclasses import dataclass, field
from pathlib import Path
import re
from urllib.parse import parse_qs

from .comparison import compare
from .database import connect
from .encoding import canonical, decode_upload
from .errors import Rejected
from .evidence import injection_trace, report_view
from .exports import export_snapshot
from .intake import ingest, preview
from .register import create as create_register
from .register import fetch as fetch_register
from .register import list_for_run
from .repository import catalog, find_run
from .snapshots import freeze, replay

API = '/chromaledger/api/'
BASE = Path(__file__).resolve().parent.parent
RUN_ID = r'([A-Za-z0-9][A-Za-z0-9_.-]{0,47})'
RUN_PATH = re.compile(rf'runs/{RUN_ID}(?:/(.*))?\Z')
TRACE = re.compile(rf'injections/{RUN_ID}\Z')
REGISTERS = re.compile(rf'registers/{RUN_ID}\Z')
VERSION = r'[1-9][0-9]{0,3}'


@dataclass(frozen=True)
class Response:
    body: bytes
    mime: str
    status: int = 200
    headers: dict = field(default_factory=dict)


def json_response(value, status=200):
    return Response(canonical(value).encode('utf-8'), 'application/json; charset=utf-8', status)


def query_fields(query, allowed):
    try:
        fields = parse_qs(query, keep_blank_values=True, max_num_fields=len(allowed) + 1)
    except ValueError as exc:
        raise Rejected('invalid_query', '查询参数无效。', status=400) from exc
    if set(fields) - set(allowed):
        raise Rejected('invalid_query', '存在未识别的查询参数。', status=400)
    for values in fields.values():
        if len(values) != 1:
            raise Rejected('invalid_query', '查询参数只能出现一次。', status=400)
    return fields


def offset_from(query):
    values = query_fields(query, ('offset',)).get('offset', ['0'])
    if not re.fullmatch(r'[0-9]{1,8}', values[0]):
        raise Rejected('invalid_offset', '无效的目录偏移。', status=400)
    return int(values[0])


def version_from(fields):
    version = fields.get('version', [''])[0]
    if version and not re.fullmatch(VERSION, version):
        raise Rejected('invalid_register', '请指定有效的分样清单版本。', status=400)
    return int(version) if version else None


def compare_from(conn, run_id, query):
    fields = query_fields(query, ('against', 'register', 'version'))
    against = fields.get('against', [''])
    if len(against) != 1 or not re.fullmatch(RUN_ID, against[0]):
        raise Rejected('invalid_comparison', '请指定有效的对比批次。', status=400)
    name = fields.get('register', [''])[0]
    version = version_from(fields)
    if name:
        if not re.fullmatch(RUN_ID, name):
            raise Rejected('invalid_comparison', '请指定有效的分样清单编号。', status=400)
        selection = fetch_register(conn, name, version, run_id)
    elif version is not None:
        raise Rejected('invalid_comparison', '指定版本时必须同时指定分样清单。', status=400)
    else:
        selection = None
    return compare(conn, run_id, against[0], selection)


def register_detail(conn, run_id, register_id, query):
    version = version_from(query_fields(query, ('version',)))
    row = fetch_register(conn, register_id, version, run_id)
    result = {'id': row['register_id'], 'version': row['version'], 'hash': row['hash'],
              'supersedes': row['supersedes'], 'created_at': row['created_at'],
              'register': json.loads(row['content'])}
    result['comparison'] = compare(conn, run_id, result['register']['left_run']
                                   if run_id == result['register']['right_run'] else result['register']['right_run'],
                                   row)
    return result


def run_route(conn, method, run_id, action, query, read_body):
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
        if action == 'compare':
            return json_response(compare_from(conn, run_id, query))
        if action == 'registers':
            find_run(conn, run_id)
            return json_response(list_for_run(conn, run_id))
        detail = REGISTERS.fullmatch(action)
        if detail:
            find_run(conn, run_id)
            return json_response(register_detail(conn, run_id, detail[1], query))
    elif method == 'POST':
        if action == 'registers':
            find_run(conn, run_id)
            result = create_register(conn, run_id, decode_upload(read_body()))
            return json_response(result, 201)
        if action == 'snapshot':
            if decode_upload(read_body()) != {}:
                raise Rejected('invalid_action', '提交封存操作时请使用空对象。')
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
            return run_route(conn, method, match[1], match[2], query, read_body)
    raise Rejected('not_found', '没有该接口。', status=404)
