"""HTTP routes for the central workstation archive."""
import re

from .archive_database import connect
from .archive_views import (
    archive_catalog, archive_report_view, archive_source_view, archive_trace,
    list_receipts, list_stations, replay_archive,
)
from .errors import Rejected
from .exports import export_archive_snapshot
from .routes import RUN_ID, Response, json_response

IDENT = r'([A-Za-z0-9][A-Za-z0-9_.-]{0,47})'
ARCHIVE_PREFIX = 'archive/'
STATIONS = re.compile(rf'{ARCHIVE_PREFIX}stations\Z')
STATION = re.compile(rf'{ARCHIVE_PREFIX}stations/{IDENT}\Z')
RECEIPTS = re.compile(rf'{ARCHIVE_PREFIX}stations/{IDENT}/receipts\Z')
CATALOG = re.compile(rf'{ARCHIVE_PREFIX}runs\Z')
RECORD = re.compile(rf'{ARCHIVE_PREFIX}stations/{IDENT}/runs/{RUN_ID}(?:/(.*))?\Z')
TRACE = re.compile(rf'injections/{RUN_ID}\Z')


def offset_from(query):
    try:
        from urllib.parse import parse_qs
        fields = parse_qs(query, keep_blank_values=True, max_num_fields=2)
    except ValueError:
        fields = {'invalid': []}
    values = fields.get('offset', ['0'])
    station_filter = fields.get('station', [])
    if set(fields) - {'offset', 'station'} or len(values) != 1 \
            or not re.fullmatch(r'[0-9]{1,8}', values[0]):
        raise Rejected('invalid_offset', '无效的目录偏移。', status=400)
    station = station_filter[0] if station_filter else None
    if station is not None and not re.fullmatch(IDENT, station):
        raise Rejected('invalid_station', '工作站标识无效。', status=400)
    return int(values[0]), station


def record_route(conn, station_id, batch_id, action, query):
    if action is None:
        return json_response(archive_report_view(conn, station_id, batch_id))
    trace = TRACE.fullmatch(action)
    if trace:
        return json_response(archive_trace(conn, station_id, batch_id, trace[1]))
    if action == 'source':
        return json_response(archive_source_view(conn, station_id, batch_id))
    if action == 'snapshot/replay':
        return json_response(replay_archive(conn, station_id, batch_id))
    if action in ('snapshot.json', 'snapshot.csv'):
        form = action.split('.')[1]
        body, mime = export_archive_snapshot(conn, station_id, batch_id, form)
        filename = f'{station_id}-{batch_id}-snapshot.{form}'
        return Response(body, mime,
                        headers={'Content-Disposition': f'attachment; filename="{filename}"'})
    raise Rejected('not_found', '没有该接口。', status=404)


def dispatch_archive(archive_db, method, route, query):
    if method != 'GET':
        raise Rejected('not_found', '归档接口仅支持读取，收件请使用命令行工具。', status=404)
    with connect(archive_db) as conn:
        if STATIONS.fullmatch(route):
            return json_response(list_stations(conn))
        match = STATION.fullmatch(route)
        if match:
            station_id = match[1]
            summary = conn.execute('''
                SELECT COUNT(*) AS batch_count,
                       SUM(CASE WHEN s.batch_id IS NOT NULL THEN 1 ELSE 0 END) AS snapshot_count,
                       MAX(r.received_at) AS last_received_at
                FROM archive_runs r
                LEFT JOIN archive_snapshots s ON r.station_id = s.station_id AND r.batch_id = s.batch_id
                WHERE r.station_id = ?
            ''', (station_id,)).fetchone()
            if summary['batch_count'] == 0:
                raise Rejected('archive_station_not_found', f'没有工作站 {station_id} 的收件记录。', status=404)
            return json_response({'station_id': station_id, **dict(summary)})
        match = RECEIPTS.fullmatch(route)
        if match:
            return json_response(list_receipts(conn, match[1]))
        if CATALOG.fullmatch(route):
            offset, station = offset_from(query)
            return json_response(archive_catalog(conn, station, offset=offset))
        match = RECORD.fullmatch(route)
        if match:
            return record_route(conn, match[1], match[2], match[3], query)
    raise Rejected('not_found', '没有该接口。', status=404)
