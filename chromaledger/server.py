import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import logging
import mimetypes
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit

from .database import initialize
from .errors import Rejected
from .routes import API, BASE, Response, dispatch, json_response

MAX_BYTES = 2_000_000
WEB = BASE / 'web'
STATIC = re.compile(r'/(?:css/[a-z-]+\.css|js/[a-z-]+\.js)\Z')
LOGGER = logging.getLogger(__name__)


class WorkbenchServer(ThreadingHTTPServer):
    def __init__(self, address, database):
        self.database = database
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(15)
        super().setup()

    def read_payload(self):
        if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length', [])) != 1:
            raise Rejected('invalid_length', '需要单个有效的 Content-Length。', status=400)
        raw_size = self.headers['Content-Length']
        if not re.fullmatch(r'[0-9]{1,10}', raw_size):
            raise Rejected('invalid_length', '请求长度无效。', status=400)
        size = int(raw_size)
        if size > MAX_BYTES:
            raise Rejected('body_too_large', '导入文件不得超过 2 MB。', status=413)
        if self.headers.get_content_type() != 'application/json':
            raise Rejected('unsupported_media', '需要 application/json。', status=415)
        data = self.rfile.read(size)
        if len(data) != size:
            raise Rejected('incomplete_body', '请求未完整接收。', status=400)
        return data

    def static(self, path):
        if path == '/':
            return Response((WEB / 'index.html').read_bytes(), 'text/html; charset=utf-8')
        if STATIC.fullmatch(path):
            file = WEB / path[1:]
            if file.is_file():
                return Response(file.read_bytes(), f'{mimetypes.guess_type(file)[0]}; charset=utf-8')
        raise Rejected('not_found', '没有该资源。', status=404)

    def respond(self):
        try:
            try:
                request = urlsplit(self.path)
            except ValueError as exc:
                raise Rejected('invalid_path', '请求路径无效。', status=400) from exc
            if request.path.startswith(API):
                result = dispatch(self.server.database, self.command, request.path, request.query, self.read_payload)
            elif self.command == 'GET':
                result = self.static(request.path)
            else:
                raise Rejected('not_found', '没有该接口。', status=404)
        except Rejected as exc:
            result = json_response({'error': exc.code, 'message': str(exc)}, exc.status)
        except (TimeoutError, ConnectionError):
            return
        except sqlite3.OperationalError as exc:
            if exc.sqlite_errorcode in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                result = json_response({'error': 'database_busy', 'message': '数据库繁忙，请重试。'}, 503)
            else:
                LOGGER.exception('Database operation failed')
                result = json_response({'error': 'database_error', 'message': '无法读取或写入本地数据库。'}, 500)
        self.send_response(result.status)
        self.send_header('Content-Type', result.mime)
        self.send_header('Content-Length', str(len(result.body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        for name, value in result.headers.items():
            self.send_header(name, value)
        self.end_headers()
        try:
            self.wfile.write(result.body)
        except ConnectionError:
            pass

    do_GET = respond
    do_POST = respond


def main():
    parser = argparse.ArgumentParser(description='启动色谱批次核算台')
    parser.add_argument('--db', type=Path, default=Path('.chromaledger/batches.sqlite3'))
    parser.add_argument('--port', type=int, default=8773)
    args = parser.parse_args()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    initialize(args.db)
    with WorkbenchServer(('127.0.0.1', args.port), args.db) as server:
        print(f'色谱批次核算台运行于 http://127.0.0.1:{server.server_port}', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
