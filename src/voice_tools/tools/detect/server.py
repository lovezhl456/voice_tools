"""Loopback-only editor API, bounded to a selected library and startup recording list."""
import hmac
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import shutil
import threading
from urllib.parse import unquote, urlsplit

from voice_tools.core.files import write_json
from . import store
from .definition import fields, identifier, parse_document, validate
from .editor import document

MAX_BODY = 1024 * 1024



class EditorApplication:
    def __init__(self, database, inputs, output):
        from voice_tools.tools.recording_qa.batch import discover
        self.database = Path(database).resolve()
        self.inputs = discover(inputs)
        self.output = Path(output).resolve()
        self.token = secrets.token_urlsafe(32)
        self.run_lock = threading.Lock()
        with store.connect(self.database, create=True) as db:
            self.library_id = store.library_id(db)
        self.output.mkdir(parents=True, exist_ok=True)
        marker = self.output / '.library.json'
        identity = {'schema_version': '1.0', 'library_id': self.library_id}
        if marker.exists():
            if json.loads(marker.read_text(encoding='utf-8')) != identity:
                raise ValueError('服务输出目录属于其他检测库，请选择新目录')
        elif any(self.output.iterdir()):
            raise ValueError('服务输出目录须为空，或已由同一检测库创建')
        else:
            write_json(marker, identity)
        (self.output / 'reports').mkdir(exist_ok=True)

    def definitions(self, db):
        return [store.select_definition(db, row['id'] + '@' + row['version']) for row in store.definitions(db)]

    def library(self):
        with store.connect(self.database) as db:
            return {'definitions': self.definitions(db), 'batches': store.batch_details(db)}

    def html(self):
        data = self.library()
        session = {'library_id': self.library_id, 'token': self.token,
                   'inputs': [path.name for path in self.inputs]}
        return document(data['definitions'], session=session)

    def save(self, config):
        config = validate(config)
        with store.connect(self.database) as db:
            existing = db.execute('SELECT config FROM definitions WHERE id=? AND version=?',
                                  (config['id'], config['version'])).fetchone()
            if existing:
                previous = json.loads(existing['config'])
                # Browsers serialize 3.0 as 3. Preserve the stored hash for equal numeric values.
                if previous == config:
                    config = previous
            digest = store.save_definition(db, config)
            return {'hash': digest, 'definitions': self.definitions(db)}

    def run(self, request):
        from . import report, service
        fields(request, ('definitions', 'batch_id'), name='运行请求')
        refs = request['definitions']
        if not isinstance(refs, list) or not 1 <= len(refs) <= 32 or any(not isinstance(ref, str) for ref in refs):
            raise ValueError('请选择1–32个已保存的定义版本')
        batch = identifier(request['batch_id'], 'batch_id')
        target = self.output / 'reports' / batch
        if not self.run_lock.acquire(blocking=False):
            raise ValueError('已有检测正在运行，请稍后再试')
        created = False
        try:
            if target.exists():
                raise ValueError('批次报告已存在，请使用新的批次标识')
            with store.connect(self.database) as db:
                configs = [store.select_definition(db, ref) for ref in refs]
                summary = service.run(db, self.inputs, configs, batch)
                # Only this request owns the new directory. Failures roll back the batch transaction.
                target.mkdir()
                created = True
                report.render(db, target, include_audio=True, hide_paths=True)
                html = target / 'index.html'
                text = html.read_text(encoding='utf-8').replace('href="config.html"', 'href="/"')
                html.write_text(text, encoding='utf-8')
            return {'summary': summary, 'report_url': '/reports/' + batch + '/index.html'}
        except (OSError, ValueError):
            if created:
                shutil.rmtree(target)
            raise
        finally:
            self.run_lock.release()


class EditorHandler(SimpleHTTPRequestHandler):
    server_version = 'VoiceToolsLocal/1.0'

    @property
    def app(self):
        return self.server.application

    def log_message(self, *args):
        pass  # Do not log configuration bodies or local recording paths.

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Accept-Ranges', 'bytes')
        super().end_headers()

    def json_response(self, status, value):
        payload = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(payload)

    def host_allowed(self):
        port = self.server.server_port
        return self.headers.get('Host') in (f'127.0.0.1:{port}', f'localhost:{port}')

    def request_allowed(self):
        if not self.host_allowed():
            return False
        origin = self.headers.get('Origin')
        if origin is not None and origin not in (f'http://127.0.0.1:{self.server.server_port}',
                                                f'http://localhost:{self.server.server_port}'):
            return False
        supplied = self.headers.get('X-Voice-Tools-Session', '')
        return supplied.isascii() and hmac.compare_digest(supplied, self.app.token)

    def do_OPTIONS(self):
        self.json_response(403, {'ok': False, 'error': '本机服务不接受跨站请求'})

    def do_POST(self):
        if not self.request_allowed():
            self.json_response(403, {'ok': False, 'error': '请求来源或会话身份不匹配，请重新打开本机配置页'})
            return
        if self.headers.get_content_type() != 'application/json':
            self.json_response(415, {'ok': False, 'error': '请求必须为 JSON'})
            return
        try:
            length = int(self.headers.get('Content-Length', '-1'))
            if self.headers.get('Transfer-Encoding') or not 0 <= length <= MAX_BODY:
                self.json_response(413, {'ok': False, 'error': '请求大小无效或超过1 MiB'})
                return
            payload = self.rfile.read(length)
            if len(payload) != length:
                raise ValueError('请求内容不完整')
            request = parse_document(payload.decode('utf-8'))
            route = urlsplit(self.path).path
            if route in ('/api/validate', '/api/definitions'):
                fields(request, ('config',), name='配置请求')
                config = validate(request['config'])
                result = {'config': config} if route == '/api/validate' else self.app.save(config)
            elif route == '/api/run':
                result = self.app.run(request)
            else:
                self.json_response(404, {'ok': False, 'error': '未知操作'})
                return
            self.json_response(200, {'ok': True, **result})
        except (ValueError, OSError, RecursionError) as error:
            self.json_response(400, {'ok': False, 'error': '配置嵌套过深' if isinstance(error, RecursionError) else str(error)})

    def do_GET(self):
        if not self.host_allowed():
            self.json_response(403, {'ok': False, 'error': '仅允许本机访问'})
            return
        route = urlsplit(self.path).path
        if route == '/api/library':
            if not self.request_allowed():
                self.json_response(403, {'ok': False, 'error': '会话身份不匹配'})
                return
            try:
                self.json_response(200, {'ok': True, **self.app.library()})
            except (OSError, ValueError) as error:
                self.json_response(400, {'ok': False, 'error': str(error)})
            return
        super().do_GET()

    def do_HEAD(self):
        if not self.host_allowed():
            self.json_response(403, {'ok': False, 'error': '仅允许本机访问'})
            return
        super().do_HEAD()

    def send_head(self):
        self.remaining = None
        route = unquote(urlsplit(self.path).path)
        if route in ('/', '/editor.html'):
            import io
            payload = self.app.html().encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            return io.BytesIO(payload)
        if not route.startswith('/reports/') or '\\' in route:
            self.send_error(404)
            return None
        root = (self.app.output / 'reports').resolve()
        try:
            path = (root / route[len('/reports/'):]).resolve()
        except (OSError, ValueError):
            self.send_error(404)
            return None
        if not path.is_relative_to(root) or not path.is_file():
            self.send_error(404)
            return None
        # Only generated report files are addressable; never serve the database or input roots.
        stream = path.open('rb')
        size = path.stat().st_size
        start, end = 0, size - 1
        header = self.headers.get('Range')
        if header:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', header)
            if match and (match[1] or match[2]):
                if match[1]:
                    start = int(match[1]); end = min(int(match[2]), end) if match[2] else end
                else:
                    start = max(0, size - int(match[2]))
            else:
                start = size
            if start >= size or end < start:
                stream.close()
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{size}')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return None
        stream.seek(start)
        self.remaining = end - start + 1
        self.send_response(206 if header else 200)
        self.send_header('Content-Type', self.guess_type(str(path)))
        self.send_header('Content-Length', str(self.remaining))
        if header:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        return stream

    def copyfile(self, source, target):
        if self.remaining is None:
            return super().copyfile(source, target)
        remaining = self.remaining
        while remaining:
            block = source.read(min(65536, remaining))
            if not block:
                break
            target.write(block)
            remaining -= len(block)


def create_server(database, inputs, output, port=0):
    application = EditorApplication(database, inputs, output)
    server = ThreadingHTTPServer(('127.0.0.1', port), EditorHandler)
    server.application = application
    return server


def serve(args):
    with create_server(args.db, args.inputs, args.out, args.port) as server:
        url = f'http://127.0.0.1:{server.server_port}/'
        if args.json_output:
            print(json.dumps({'ok': True, 'status': 'listening', 'url': url,
                              'recordings': len(server.application.inputs)}), flush=True)
        else:
            print('本机配置界面：' + url + ' ；按 Ctrl+C 停止服务。', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0
