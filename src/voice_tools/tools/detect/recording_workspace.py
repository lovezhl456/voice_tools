"""Local recording browser, asynchronous annotation and portable review export."""
from contextlib import closing
import csv
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import secrets
from tempfile import TemporaryDirectory, TemporaryFile
from threading import Lock, Thread
import uuid
from zipfile import ZipFile

from voice_tools import __version__
from voice_tools.audio.formats import decode, probe
from voice_tools.audio.health import waveform
from voice_tools.audio.io import read_wav, write_wav
from voice_tools.core.files import sha256
from . import engine, store, timeline_rules
from .definition import canonical, fingerprint, parse_document

FORMATS = {'.wav', '.flac', '.mp3', '.m4a', '.ogg'}
API = '/api/recordings/'
MANUAL = {'schema_version': '1.0', 'id': 'workbench-manual', 'version': '1', 'name': '人工补标',
          'metrics': {'duration': {'kind': 'duration_s', 'channel': 0}},
          'rules': [{'id': 'manual', 'label': '人工补标', 'when': {'metric': 'duration', 'op': 'lt', 'value': 0}}]}
ASSETS = {
    '/recordings/app.js': 'recording_workspace.js',
    '/recordings/style.css': 'recording_workspace.css',
    '/recordings/timeline.js': 'duplex-timeline.js',
}
PAGE = Path(__file__).parent


def file_stamp(path):
    s = path.stat()
    return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]


def public_finding(finding):
    return {key: value for key, value in finding.items() if key != 'sources'}


def csv_text(value):
    return "'" + value if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')) else value


class RecordingWorkspace:
    def __init__(self, app, sources):
        self.app = app
        self.lock = Lock()
        self.start_lock = Lock()
        self.job = None
        self.media = {}
        self.directory = None
        self.source = next((p for p in sources if p.is_dir()), None)
        with store.connect(self.app.database) as db:
            self.initialize(db)

    def initialize(self, db):
        db.execute('''CREATE TABLE IF NOT EXISTS recording_workbench_files
                   (root TEXT, relative TEXT, stamp TEXT, digest TEXT, PRIMARY KEY(root, relative))''')
        db.execute('''CREATE TABLE IF NOT EXISTS recording_workbench_config
                   (id INTEGER PRIMARY KEY CHECK(id=1), root TEXT, ai_channel INTEGER)''')
        db.execute('''CREATE TABLE IF NOT EXISTS recording_workbench_audio
                   (digest TEXT PRIMARY KEY, time_origin TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS recording_workbench_jobs
                   (id TEXT PRIMARY KEY, state TEXT, payload TEXT)''')

    def config(self, db):
        saved = db.execute('SELECT root,ai_channel FROM recording_workbench_config WHERE id=1').fetchone()
        return (Path(saved['root']), saved['ai_channel']) if saved else (self.source, None)

    def temporary(self):
        with self.lock:
            if self.directory is None:
                self.directory = TemporaryDirectory(prefix='voice-recording-workbench-')
            return Path(self.directory.name)

    def close(self):
        with self.lock:
            thread = getattr(self, 'thread', None)
        if thread:
            thread.join()
        if self.directory is not None:
            self.directory.cleanup()

    def definitions(self, db):
        rows = db.execute('SELECT config FROM definitions WHERE id != ? ORDER BY name,version', (MANUAL['id'],))
        return [json.loads(row['config']) for row in rows]

    def scan(self, db, root):
        if root is None:
            return
        if not root.is_dir():
            return
        for path in root.rglob('*'):
            if path.suffix.lower() not in FORMATS or path.is_symlink():
                continue
            try:
                if not path.is_file() or path.resolve() != path:
                    continue
                stamp = canonical(file_stamp(path))
            except OSError:
                continue
            db.execute('''INSERT INTO recording_workbench_files VALUES (?, ?, ?, NULL)
                       ON CONFLICT(root,relative) DO UPDATE SET stamp=excluded.stamp,digest=NULL
                       WHERE stamp!=excluded.stamp''', (str(root), path.relative_to(root).as_posix(), stamp))

    def entry(self, db, root, relative):
        row = db.execute('SELECT * FROM recording_workbench_files WHERE root=? AND relative=?',
                         (str(root), relative)).fetchone()
        if row is None or root is None:
            raise ValueError('录音不在已关联的目录清单中，请刷新文件夹')
        path = root / relative
        if path.resolve() != path or not path.is_file():
            raise ValueError('源录音已移走或不可读取')
        stamp = file_stamp(path)
        digest = row['digest'] if json.loads(row['stamp']) == stamp else None
        if digest is None:
            digest = sha256(path)
            if file_stamp(path) != stamp:
                raise ValueError('源录音在读取期间变化，请重试')
            db.execute('UPDATE recording_workbench_files SET stamp=?,digest=? WHERE root=? AND relative=?',
                       (canonical(stamp), digest, str(root), relative))
        return path, stamp, digest

    def entries(self, db, root):
        rows = db.execute('SELECT relative,stamp,digest FROM recording_workbench_files WHERE root=? ORDER BY relative',
                          (str(root),)).fetchall()
        counts = dict(db.execute('SELECT recording_id,COUNT(*) FROM findings GROUP BY recording_id'))
        processed = {r[0] for r in db.execute('SELECT DISTINCT recording_id FROM evaluations')}
        known = {}
        for row in db.execute('SELECT id,sources FROM recordings'):
            for source in json.loads(row['sources']):
                known[source] = row['id']
        result = []
        for row in rows:
            relative = row['relative']
            path = root / relative
            try:
                exists = path.resolve() == path and path.is_file()
                fresh = exists and file_stamp(path) == json.loads(row['stamp'])
            except OSError:
                exists = fresh = False
            digest = row['digest'] if fresh else None
            if digest:
                status = 'completed' if digest in processed else 'not_run'
            elif fresh and str(path) in known:
                status = 'history'
            else:
                status = 'not_run' if exists else 'missing'
            result.append(dict(id=relative, size=json.loads(row['stamp'])[2], status=status,
                               findings=counts.get(digest, 0)))
        return result

    def state(self, request):
        with store.connect(self.app.database) as db:
            root, channel = self.config(db)
            if request.get('scan'):
                self.scan(db, root)
            jobs = [dict(json.loads(row['payload']), id=row['id'],
                         state='interrupted' if row['state'] == 'running' and row['id'] != self.job else row['state'])
                    for row in db.execute('SELECT * FROM recording_workbench_jobs ORDER BY rowid DESC LIMIT 10')]
            return dict(folder=str(root) if root else '', ai_channel=channel,
                        files=self.entries(db, root), rules=self.definitions(db), jobs=jobs,
                        model=os.getenv('VOICE_TOOLS_OPENAI_MODEL', 'gpt-4o-mini'),
                        key_on_server=bool(os.getenv('OPENAI_API_KEY')))

    def associate(self, request):
        proposed = Path(request['folder']).expanduser()
        if not proposed.is_absolute() or not proposed.is_dir():
            raise ValueError('请输入实际存在的本机录音文件夹绝对路径')
        root = proposed.resolve(strict=True)
        if root == self.app.output or root in self.app.output.parents or self.app.output in root.parents:
            raise ValueError('录音目录不能与工作台数据目录重叠')
        channel = request.get('ai_channel')
        if channel is not None and (type(channel) is not int or channel not in (0, 1)):
            raise ValueError('AI 声道须为左、右或未知')
        with store.connect(self.app.database) as db:
            self.scan(db, root)
            db.execute('INSERT OR REPLACE INTO recording_workbench_config VALUES (1,?,?)', (str(root), channel))
        return self.state({})

    def prepare(self, path, stamp, digest):
        try:
            audio = read_wav(path)
            playable = path
            time_origin = 'original_pcm'
        except ValueError:
            info = probe(path)
            if info['native_pcm16']:
                raise
            cache = self.temporary()
            playable = cache / (digest + '.wav')
            if playable.exists():
                audio = read_wav(playable)
            else:
                with TemporaryDirectory(dir=cache) as directory:
                    candidate = Path(directory) / 'audio.wav'
                    audio, _ = decode(path, candidate, info['sample_rate'], info=info)
                    candidate.replace(playable)
            time_origin = 'prepared_pcm'
        if file_stamp(path) != stamp:
            raise ValueError('源录音已变化，请重新打开')
        return audio, playable, time_origin

    def register(self, db, path, digest, audio, time_origin):
        db.execute('INSERT OR IGNORE INTO recording_workbench_audio VALUES (?,?)', (digest, time_origin))
        row = db.execute('SELECT sources,waveform FROM recordings WHERE id=?', (digest,)).fetchone()
        if row:
            sources = set(json.loads(row['sources']))
            if str(path) not in sources:
                db.execute('UPDATE recordings SET sources=? WHERE id=?', (canonical(sorted(sources | {str(path)})), digest))
            return json.loads(row['waveform'])
        summary = waveform(audio, bins=min(12000, max(1200, int(audio.duration_s * 30))))
        db.execute('INSERT INTO recordings VALUES (?, ?, ?, ?, ?)',
                   (digest, audio.duration_s, audio.samples.shape[1], canonical([str(path)]), canonical(summary)))
        return summary

    def media_url(self, path, stamp, playable):
        with self.lock:
            token = secrets.token_urlsafe(20)
            self.media[token] = (path, stamp, playable)
            while len(self.media) > 64:
                del self.media[next(iter(self.media))]
        return '/recordings/media/' + token

    def resource(self, route):
        if route == '/recordings/':
            template = (PAGE / 'recording_workspace.html').read_text(encoding='utf-8')
            return template.replace('__SESSION__', self.app.token).encode('utf-8')
        if route in ASSETS:
            return PAGE / ASSETS[route]
        if route.startswith('/recordings/media/'):
            with self.lock:
                item = self.media.get(route.rsplit('/', 1)[-1])
            if item:
                original, stamp, playable = item
                try:
                    if original.resolve() == original and stamp == file_stamp(original):
                        return playable
                except OSError:
                    pass
        return None

    def evaluations(self, db, digest):
        sql = '''SELECT e.*, d.id AS rule, d.version, d.name FROM evaluations e
                 JOIN definitions d ON d.hash=e.config_hash WHERE e.recording_id=?
                 ORDER BY e.batch_id,d.name'''
        return [dict(r) for r in db.execute(sql, (digest,))]

    def open(self, request):
        with store.connect(self.app.database) as db:
            root, channel = self.config(db)
            path, stamp, digest = self.entry(db, root, request['file'])
        audio, playable, time_origin = self.prepare(path, stamp, digest)
        selected = request.get('channel', 'both')
        if selected not in ('both', 'left', 'right'):
            raise ValueError('试听声道无效')
        if selected != 'both':
            index = 0 if selected == 'left' else 1
            if index >= audio.samples.shape[1]:
                raise ValueError('录音没有所选声道')
            cache = self.temporary()
            mono = cache / f'{digest}-{selected}.wav'
            if not mono.exists():
                with TemporaryDirectory(dir=cache) as directory:
                    candidate = Path(directory) / 'mono.wav'
                    write_wav(candidate, audio.samples[:, index:index + 1], audio.sample_rate)
                    candidate.replace(mono)
            playable = mono
        with store.connect(self.app.database) as db:
            summary = self.register(db, path, digest, audio, time_origin)
            findings = [public_finding(row) for row in store.query(db, exact_recording=digest)]
            evaluations = self.evaluations(db, digest)
        return dict(id=digest, duration=audio.duration_s, channels=audio.samples.shape[1], ai_channel=channel,
                    time_origin=time_origin, waveform=summary, url=self.media_url(path, stamp, playable),
                    findings=findings, evaluations=evaluations)

    def rule(self, request, *, save=False):
        raw = parse_document(request['config_text']) if 'config_text' in request else request['config']
        config = timeline_rules.validate(raw)
        if config['id'] == MANUAL['id']:
            raise ValueError('规则标识已保留给人工补标')
        if save:
            with self.app.write_access(), store.connect(self.app.database) as db:
                try:
                    old = store.select_definition(db, config['id'] + '@' + config['version'], timeline=True)
                except ValueError:
                    old = None
                store.save_definition(db, old if old == config else config, timeline=True)
        return {'config': config}

    def start(self, request):
        refs = request['rules']
        files = request['files']
        if not isinstance(refs, list) or not 1 <= len(refs) <= 32 or not isinstance(files, list) or not files:
            raise ValueError('请选择录音和 1–32 条规则')
        with self.app.write_access(), store.connect(self.app.database) as db:
            root, channel = self.config(db)
            rules = [store.select_definition(db, ref, timeline=True) for ref in refs]
            if any(r['id'] == MANUAL['id'] for r in rules) or len({r['id'] for r in rules}) != len(rules):
                raise ValueError('每条规则每次只可运行一个版本')
            if root is None or any(not isinstance(file, str) or not db.execute(
                    'SELECT 1 FROM recording_workbench_files WHERE root=? AND relative=?', (str(root), file)).fetchone()
                    for file in files):
                raise ValueError('所选录音不在当前目录清单')
            files = list(dict.fromkeys(files))
            ident = uuid.uuid4().hex
            task = dict(total=len(files), done=0, files=files, errors={}, root=str(root), rules=refs)
            db.execute('INSERT INTO batches VALUES (?,?,?,?,?)', (ident, store.now(), 'running', __version__, 'recording-1'))
            db.execute('INSERT INTO recording_workbench_jobs VALUES (?,?,?)', (ident, 'running', canonical(task)))
        with self.lock:
            if self.job is not None:
                # The start route uses the dispatch lock; this guard also protects shutdown.
                raise ValueError('已有标注任务运行中')
            self.job = ident
            self.thread = Thread(target=self.run, args=(ident, task, root, channel, rules), daemon=True)
            self.thread.start()
        return {'id': ident}

    def run(self, ident, task, root, channel, rules):
        status = 'completed'
        seen = set()
        try:
            for file in task['files']:
                try:
                    with store.connect(self.app.database) as db:
                        path, stamp, digest = self.entry(db, root, file)
                    if digest not in seen:
                        audio, playable, time_origin = self.prepare(path, stamp, digest)
                        results = [self.evaluate(audio, config, channel, playable) for config in rules]
                        with store.connect(self.app.database) as db:
                            self.register(db, path, digest, audio, time_origin)
                            for config, evaluation in zip(rules, results):
                                self.record_evaluation(db, ident, digest, config, evaluation)
                                if evaluation['status'] != 'ok':
                                    status = 'partial'
                                if evaluation['status'] == 'error':
                                    task['errors'][file] = evaluation['note']
                        seen.add(digest)
                except (ValueError, OSError) as error:
                    task['errors'][file] = str(error)
                    status = 'partial'
                task['done'] += 1
                with store.connect(self.app.database) as db:
                    db.execute('UPDATE recording_workbench_jobs SET payload=? WHERE id=?', (canonical(task), ident))
        except Exception as error:
            task['error'] = str(error)
            status = 'interrupted'
        finally:
            try:
                with store.connect(self.app.database) as db:
                    db.execute('UPDATE batches SET status=? WHERE id=?', (status, ident))
                    db.execute('UPDATE recording_workbench_jobs SET state=?,payload=? WHERE id=?',
                               (status, canonical(task), ident))
            finally:
                with self.lock:
                    self.job = None

    def evaluate(self, audio, config, channel, playable):
        try:
            if config['schema_version'] == '1.0':
                return engine.analyze(audio, config)
            return timeline_rules.analyze(audio, config, channel, playable)
        except (ValueError, OSError) as error:
            return dict(status='error', windows=0, note=str(error), findings=[])

    def record_evaluation(self, db, ident, digest, config, result):
        rule_hash = fingerprint(config)
        db.execute('INSERT INTO evaluations VALUES (?,?,?,?,?,?)',
                   (ident, digest, rule_hash, result['status'], result['windows'], result['note']))
        for finding in result['findings']:
            db.execute('INSERT INTO findings VALUES (?,?,?,?,?,?)',
                       (uuid.uuid4().hex, ident, digest, rule_hash, 'auto', canonical(finding)))
        return result

    def review(self, request):
        with self.app.write_access(), store.connect(self.app.database) as db:
            request['reviewer'] = '本机复核'
            finding = store.review(db, request)
        return dict(finding=public_finding(finding))

    def manual(self, request):
        with self.app.write_access(), store.connect(self.app.database) as db:
            root, _ = self.config(db)
            _, _, digest = self.entry(db, root, request['file'])
            key = store.save_definition(db, MANUAL)
            batch = 'recording-workbench-manual'
            db.execute('INSERT OR IGNORE INTO batches VALUES (?,?,?,?,?)', (batch, store.now(), 'completed', __version__, 'manual'))
            db.execute('INSERT OR IGNORE INTO evaluations VALUES (?,?,?,?,?,?)', (batch, digest, key, 'ok', 0, ''))
            packet = dict(id=uuid.uuid4().hex, batch_id=batch, recording_id=digest, config_hash=key,
                          label=request['label'], start_s=request['start_s'], end_s=request['end_s'],
                          reviewer='本机复核', comment=request.get('comment', ''))
            return dict(finding=public_finding(store.add_manual(db, packet)))

    def export(self, request):
        files = request['files']
        if not isinstance(files, list) or not files or any(not isinstance(file, str) for file in files) or len(files) != len(set(files)):
            raise ValueError('请选择要导出的录音')
        archive = TemporaryFile()
        try:
            with closing(ZipFile(archive, 'w', allowZip64=True)) as bundle, store.connect(self.app.database) as db:
                root, role = self.config(db)
                manifest, findings, snapshots = [], [], {}
                table = StringIO()
                columns = ('file', 'sha256', 'label', 'definition_id', 'version', 'batch_id', 'start_s',
                           'end_s', 'status', 'reviewer', 'reviewed_at', 'comment')
                writer = csv.DictWriter(table, columns)
                writer.writeheader()
                for index, file in enumerate(files, 1):
                    if not db.execute('SELECT 1 FROM recording_workbench_files WHERE root=? AND relative=?',
                                      (str(root), file)).fetchone():
                        raise ValueError('所选录音不在当前目录清单')
                    row = dict(file=file, path=None, sha256=None, status='source_missing', evaluations=[])
                    try:
                        path, stamp, digest = self.entry(db, root, file)
                    except (ValueError, OSError):
                        if not request.get('data_only'):
                            raise ValueError(f'源录音不可用：{file}。可改选「仅数据」或重新关联原目录。')
                        historical = db.execute('SELECT digest FROM recording_workbench_files WHERE root=? AND relative=?',
                                                (str(root), file)).fetchone()
                        digest = historical['digest'] if historical else None
                    else:
                        row.update(sha256=digest, status='not_run')
                        if not request.get('data_only'):
                            member = f'audio/{index:04}/{path.name}'
                            calculated = hashlib.sha256()
                            with path.open('rb') as source, bundle.open(member, 'w', force_zip64=True) as target:
                                for block in iter(lambda: source.read(1024 * 1024), b''):
                                    calculated.update(block)
                                    target.write(block)
                            if file_stamp(path) != stamp or calculated.hexdigest() != digest:
                                raise ValueError(f'导出过程中录音变化：{file}，请重试')
                            row['path'] = member
                    if digest:
                        row['sha256'] = digest
                        row['evaluations'] = self.evaluations(db, digest)
                        if row['status'] != 'source_missing' and row['evaluations']:
                            row['status'] = 'evaluated'
                        timing = db.execute('SELECT time_origin FROM recording_workbench_audio WHERE digest=?',
                                            (digest,)).fetchone()
                        row['time_origin'] = timing[0] if timing else 'original_pcm' if row['evaluations'] else None
                        for evaluation in row['evaluations']:
                            key = evaluation['config_hash']
                            snapshots[key] = json.loads(db.execute('SELECT config FROM definitions WHERE hash=?', (key,)).fetchone()[0])
                        for finding in store.query(db, exact_recording=digest):
                            if request.get('confirmed_only') and finding['status'] not in ('confirmed', 'corrected'):
                                continue
                            item = dict(public_finding(finding), file=file, sha256=digest)
                            findings.append(item)
                            writer.writerow({field: csv_text(item.get(field, '')) for field in columns})
                    manifest.append(row)
                encode = lambda value: json.dumps(value, ensure_ascii=False, indent=2)
                bundle.writestr('recordings.json', encode(dict(schema_version='1.0', ai_channel=role, recordings=manifest)))
                bundle.writestr('annotations.json', encode(dict(schema_version='1.0', findings=findings)))
                bundle.writestr('annotations.csv', table.getvalue().encode('utf-8-sig'))
                for key, config in snapshots.items():
                    bundle.writestr(f'rules/{key}.json', encode(config))
            return archive
        except BaseException:
            archive.close()
            raise

    def dispatch(self, route, request):
        if not isinstance(request, dict):
            raise ValueError('请求必须为 JSON 对象')
        action = route.removeprefix(API)
        handlers = {'state': self.state, 'associate': self.associate, 'open': self.open,
                    'validate': self.rule, 'save': lambda data: self.rule(data, save=True),
                    'review': self.review, 'manual': self.manual, 'export': self.export,
                    'ai': self.draft, 'run': self.start}
        if action not in handlers:
            raise ValueError('未知录音工作台操作')
        try:
            if action == 'run':
                with self.start_lock:
                    with self.lock:
                        if self.job is not None:
                            raise ValueError('已有标注任务运行中')
                    return self.start(request)
            return handlers[action](request)
        except (KeyError, TypeError) as error:
            raise ValueError('请求字段缺失或格式无效') from error

    def draft(self, request):
        from .rule_drafting import draft
        return draft(request)
