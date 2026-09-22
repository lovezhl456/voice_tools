"""Local workspace application; all file access stays inside startup-authorized roots."""
import hashlib
import json
from pathlib import Path
import threading
import uuid

from voice_tools.core.files import sha256
from voice_tools.tools.recording_qa.batch import discover
from . import store, workspace
from .definition import fields, fingerprint

ROOT = Path(__file__).parent


def workspace_id(output):
    return hashlib.sha256(str(Path(output).resolve()).encode()).hexdigest()


def restore_inputs(database, output):
    with store.connect(database) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='workspaces'").fetchone():
            raise ValueError('首次启动请指定录音目录')
        saved = workspace.settings(db, workspace_id(output))
        if not saved:
            raise ValueError('没有已保存的工作区，请指定录音目录')
        return [Path(path) for path in saved['roots']]


class Workbench:
    def __init__(self, app, roots):
        self.app = app
        self.roots = roots
        self.id = workspace_id(app.output)
        self.jobs = {}
        self.jobs_lock = threading.Lock()
        with store.connect(app.database) as db:
            workspace.initialize(db)
            saved = workspace.settings(db, self.id)
            value = {'name': saved['name'] if saved else '录音质检工作区',
                     'definitions': saved['definitions'] if saved else [],
                     'roots': [str(path) for path in roots]}
            if not saved or saved['roots'] != value['roots']:
                workspace.save_settings(db, self.id, value, saved['revision'] if saved else 0)

    def scan(self):
        paths = discover(self.roots)
        output = self.app.output
        for path in paths:
            if path == output or output in path.parents:
                raise ValueError('扫描结果包含工作区报告，请检查输入目录')
            if not any(path == root or root.is_dir() and root in path.parents for root in self.roots):
                raise ValueError('录音符号链接超出启动时授权的输入范围')
        self.app.inputs = paths
        return paths

    def input_rows(self):
        rows = []
        for path in self.app.inputs:
            root = next((root for root in self.roots if root.is_dir() and root in path.parents), path.parent)
            rows.append({'id': hashlib.sha256(str(path).encode()).hexdigest(), 'name': str(path.relative_to(root))})
        return rows

    def state(self):
        with store.connect(self.app.database) as db:
            sets = workspace.saved_summaries(db, 'sample_sets')
            comparisons = workspace.saved_summaries(db, 'rule_comparisons')
            batches = store.batch_details(db)
            for batch in batches:
                target = self.app.output / 'reports' / batch['id'] / 'index.html'
                batch['report_url'] = '/reports/' + batch['id'] + '/index.html' if target.is_file() else None
            records = {row['id']: json.loads(row['sources']) for row in db.execute('SELECT id,sources FROM recordings')}
            samples = [{**item, 'recording_name': Path(records[item['recording_id']][0]).name} for item in workspace.standards(db)]
            result = {'settings': workspace.settings(db, self.id), 'definitions': store.definitions(db),
                      'inputs': self.input_rows(), 'batches': batches, 'standards': samples,
                      'sets': sets, 'comparisons': comparisons}
        with self.jobs_lock:
            result['jobs'] = list(self.jobs.values())
        return result

    def html(self):
        session = json.dumps({'token': self.app.token}).replace('<', '\\u003c')
        return ((ROOT / 'workspace.html').read_text(encoding='utf-8')
                .replace('__STYLE__', (ROOT / 'editor.css').read_text(encoding='utf-8') + (ROOT / 'workspace.css').read_text(encoding='utf-8'))
                .replace('__SCRIPT__', (ROOT / 'workspace.js').read_text(encoding='utf-8')).replace('__SESSION__', session))

    def update_job(self, identity, **changes):
        with self.jobs_lock:
            self.jobs[identity] = {**self.jobs[identity], **changes}

    def start_job(self, kind, action):
        if not self.app.run_lock.acquire(blocking=False):
            raise workspace.WorkspaceBusyError()
        identity = str(uuid.uuid4())
        with self.jobs_lock:
            self.jobs[identity] = {'id': identity, 'kind': kind, 'status': 'running', 'done': 0, 'total': 0}
        def execute():
            try:
                result = action(lambda done, total: self.update_job(identity, done=done, total=total))
                self.update_job(identity, status='completed', result=result)
            except Exception as error:
                self.update_job(identity, status='failed', error=str(error))
            finally:
                self.app.run_lock.release()
        threading.Thread(target=execute, daemon=True).start()
        return {'job_id': identity}

    def run(self, request):
        if self.app.run_lock.locked():
            raise workspace.WorkspaceBusyError()
        fields(request, ('definitions', 'mode'), ('limit', 'selected'), '批跑请求')
        mode = request['mode']
        if mode not in ('all', 'new', 'trial'):
            raise ValueError('未知批跑模式')
        paths = self.scan()
        with store.connect(self.app.database) as db:
            configs = workspace.definitions(db, request['definitions'])
            hashes = {fingerprint(config) for config in configs}
            if mode == 'new':
                remaining = []
                for path in paths:
                    try:
                        digest = sha256(path)
                    except OSError:
                        remaining.append(path)
                        continue
                    completed = {row[0] for row in db.execute("SELECT config_hash FROM evaluations WHERE recording_id=? AND status='ok'", (digest,))}
                    if not hashes <= completed:
                        remaining.append(path)
                paths = remaining
        if mode == 'trial':
            limit = request.get('limit', 10)
            if type(limit) is not int or not 1 <= limit <= 100:
                raise ValueError('试跑数量须为 1–100')
            selected = request.get('selected', [])
            available = {row['id']: path for row, path in zip(self.input_rows(), self.app.inputs)}
            if not isinstance(selected, list) or any(not isinstance(key, str) or key not in available for key in selected):
                raise ValueError('试跑录音不在当前授权清单中')
            paths = [available[key] for key in dict.fromkeys(selected)] if selected else paths[:limit]
            if len(paths) > 100:
                raise ValueError('一次最多试跑 100 个录音')
        if not paths:
            return {'skipped': True, 'message': '没有需要检测的新录音；这些录音已由所选规则成功检测。'}
        def action(progress):
            from . import report, service
            batch = 'run-' + str(uuid.uuid4())
            target = self.app.output / 'reports' / batch
            with store.connect(self.app.database) as db:
                summary = service.run(db, paths, configs, batch, progress=progress)
            # Detection survives report-rendering errors and can be exported again from the CLI.
            with store.connect(self.app.database) as db:
                report.render(db, target, include_audio=True, hide_paths=True, batch=batch)
            return {'summary': summary, 'report_url': '/reports/' + batch + '/index.html'}
        return self.start_job('检测', action)

    def dispatch(self, route, request):
        if route == '/api/workspace/scan':
            fields(request, (), name='重新扫描')
            self.scan()
            return self.state()
        if route == '/api/workspace/run':
            return self.run(request)
        if route == '/api/workspace/compare':
            if self.app.run_lock.locked():
                raise workspace.WorkspaceBusyError()
            fields(request, ('set_id', 'before', 'after'), name='版本比较')
            paths = self.scan()
            def action(progress):
                with store.connect(self.app.database) as db:
                    result = workspace.compare_set(db, request['set_id'], request['before'], request['after'], paths, progress)
                    return workspace.history_summary('rule_comparisons', result)
            return self.start_job('版本比较', action)
        with self.app.write_access(), store.connect(self.app.database) as db:
            if route == '/api/workspace/settings':
                fields(request, ('name', 'definitions', 'expected_revision'), name='工作区设置')
                value = {'name': request['name'], 'definitions': request['definitions'], 'roots': [str(path) for path in self.roots]}
                return {'settings': workspace.save_settings(db, self.id, value, request['expected_revision'])}
            if route == '/api/workspace/reviews':
                result = workspace.save_reviews(db, request)
                return {**result, 'findings': store.query(db)}
            if route == '/api/workspace/standard':
                return {'standard': workspace.save_standard(db, request)}
            if route == '/api/workspace/freeze':
                fields(request, ('name', 'ids'), name='冻结样本集')
                return {'sample_set': workspace.freeze(db, request['name'], request['ids'])}
        raise ValueError('未知工作区操作')
