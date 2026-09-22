"""SQLite library: immutable definitions/evaluations and append-only human decisions."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid

from .definition import canonical, fields, fingerprint, identifier, text, validate
from .metrics import finite

SCHEMA = '''
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE definitions (hash TEXT PRIMARY KEY, id TEXT NOT NULL, version TEXT NOT NULL,
                          name TEXT NOT NULL, config TEXT NOT NULL, UNIQUE(id, version));
CREATE TABLE recordings (id TEXT PRIMARY KEY, duration_s REAL NOT NULL, channels INTEGER NOT NULL,
                         sources TEXT NOT NULL, waveform TEXT NOT NULL);
CREATE TABLE batches (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL,
                      tool_version TEXT NOT NULL, engine_version TEXT NOT NULL);
CREATE TABLE evaluations (batch_id TEXT NOT NULL REFERENCES batches(id),
                         recording_id TEXT NOT NULL REFERENCES recordings(id),
                         config_hash TEXT NOT NULL REFERENCES definitions(hash),
                         status TEXT NOT NULL, windows INTEGER NOT NULL, note TEXT NOT NULL,
                         PRIMARY KEY(batch_id, recording_id, config_hash));
CREATE TABLE input_errors (batch_id TEXT NOT NULL REFERENCES batches(id), source TEXT NOT NULL, error TEXT NOT NULL);
CREATE TABLE findings (id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, recording_id TEXT NOT NULL,
                       config_hash TEXT NOT NULL, origin TEXT NOT NULL, payload TEXT NOT NULL,
                       FOREIGN KEY(batch_id, recording_id, config_hash)
                       REFERENCES evaluations(batch_id, recording_id, config_hash));
CREATE INDEX finding_recording ON findings(recording_id, batch_id);
CREATE TABLE reviews (finding_id TEXT NOT NULL REFERENCES findings(id), revision INTEGER NOT NULL,
                      created_at TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(finding_id, revision));
'''
STATUSES = ('pending', 'confirmed', 'false_positive', 'corrected')


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect(path, create=False):
    path = Path(path).resolve()
    if not path.exists() and not create:
        raise ValueError('检测库不存在，请先 detect config-add 或 detect run')
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    db = None
    try:
        db = sqlite3.connect(str(path), timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        if not tables and create:
            db.executescript(SCHEMA)
            db.executemany('INSERT INTO meta VALUES (?, ?)', [('schema_version', '1.0'), ('library_id', str(uuid.uuid4()))])
            db.commit()
        metadata = dict(db.execute('SELECT key, value FROM meta').fetchall())
        if metadata.get('schema_version') != '1.0' or not metadata.get('library_id'):
            raise ValueError('不支持的检测库版本')
        with db:
            yield db
    except sqlite3.Error as error:
        raise ValueError(f'检测库操作失败：{error}') from error
    finally:
        if db is not None:
            db.close()


def library_id(db):
    return db.execute("SELECT value FROM meta WHERE key='library_id'").fetchone()[0]


def save_definition(db, config):
    config = validate(config)
    digest = fingerprint(config)
    previous = db.execute('SELECT hash FROM definitions WHERE id=? AND version=?',
                          (config['id'], config['version'])).fetchone()
    if previous and previous['hash'] != digest:
        raise ValueError(f"{config['id']}@{config['version']} 已保存且内容不同，请使用新版本")
    db.execute('INSERT OR IGNORE INTO definitions VALUES (?, ?, ?, ?, ?)',
               (digest, config['id'], config['version'], config['name'], canonical(config)))
    return digest


def definitions(db):
    return [dict(row) for row in db.execute('SELECT hash,id,version,name FROM definitions ORDER BY id,version')]


def select_definition(db, reference):
    parts = reference.split('@')
    if len(parts) != 2:
        raise ValueError('检测定义必须为 id@version，不能隐式选择最新版本')
    row = db.execute('SELECT config FROM definitions WHERE id=? AND version=?', parts).fetchone()
    if row is None:
        raise ValueError(f'检测定义不存在：{reference}')
    return validate(json.loads(row['config']))


def batch_details(db):
    result = []
    for row in db.execute('SELECT * FROM batches ORDER BY created_at DESC'):
        item = dict(row)
        item['evaluations'] = db.execute('SELECT COUNT(*) FROM evaluations WHERE batch_id=?', (row['id'],)).fetchone()[0]
        item['errors'] = [dict(error) for error in db.execute('SELECT source,error FROM input_errors WHERE batch_id=?', (row['id'],))]
        item['evaluation_issues'] = [dict(issue) for issue in db.execute(
            'SELECT recording_id,config_hash,status,note FROM evaluations WHERE batch_id=? AND status<>?',
            (row['id'], 'ok'))]
        result.append(item)
    return result


def evaluation_rows(db, batch=None):
    sql = '''SELECT e.*, r.duration_s, r.channels, r.sources, d.id AS definition_id, d.version, d.name
             FROM evaluations e JOIN recordings r ON r.id=e.recording_id
             JOIN definitions d ON d.hash=e.config_hash'''
    rows = db.execute(sql + (' WHERE e.batch_id=?' if batch else '') + ' ORDER BY e.batch_id,e.recording_id,e.config_hash',
                      (batch,) if batch else ())
    return [{**dict(row), 'sources': json.loads(row['sources'])} for row in rows]


def current_finding(db, finding_id):
    row = db.execute('''SELECT f.*, r.duration_s, r.channels, r.sources, d.id AS definition_id, d.version, d.name
                        FROM findings f JOIN recordings r ON r.id=f.recording_id
                        JOIN definitions d ON d.hash=f.config_hash WHERE f.id=?''', (finding_id,)).fetchone()
    if row is None:
        raise ValueError(f'找不到检测结果：{finding_id}')
    return decode_finding(db, row)


def decode_finding(db, row):
    item = dict(row)
    automatic = json.loads(item.pop('payload'))
    item['sources'] = json.loads(item['sources'])
    latest = db.execute('SELECT revision,created_at,payload FROM reviews WHERE finding_id=? ORDER BY revision DESC LIMIT 1',
                        (item['id'],)).fetchone()
    review = json.loads(latest['payload']) if latest else {}
    item.update(automatic)
    item.update({'automatic': automatic, 'revision': latest['revision'] if latest else 0,
                 'reviewed_at': latest['created_at'] if latest else None,
                 'status': review.get('status', 'pending'), 'reviewer': review.get('reviewer', ''),
                 'comment': review.get('comment', '')})
    for key in ('label', 'start_s', 'end_s'):
        item[key] = review.get(key, automatic[key])
    return item


def query(db, tags=(), tag_mode='any', recording=None, batch=None, definition=None, version=None, status=None):
    clauses, args = [], []
    for column, value in (('f.batch_id', batch), ('d.id', definition), ('d.version', version)):
        if value is not None:
            clauses.append(column + '=?')
            args.append(value)
    sql = '''SELECT f.*, r.duration_s, r.channels, r.sources, d.id AS definition_id, d.version, d.name
             FROM findings f JOIN recordings r ON r.id=f.recording_id
             JOIN definitions d ON d.hash=f.config_hash'''
    if clauses:
        sql += ' WHERE ' + ' AND '.join(clauses)
    rows = [decode_finding(db, row) for row in db.execute(sql + ' ORDER BY f.batch_id,f.recording_id,f.id', args)]
    if recording:
        needle = recording.casefold()
        rows = [row for row in rows if needle in row['recording_id'] or any(needle in path.casefold() for path in row['sources'])]
    if status:
        rows = [row for row in rows if row['status'] == status]
    if tags:
        required = set(tags)
        if tag_mode == 'all':
            grouped = {}
            for row in rows:
                grouped.setdefault((row['batch_id'], row['recording_id']), set()).add(row['label'])
            rows = [row for row in rows if required <= grouped[(row['batch_id'], row['recording_id'])]]
        rows = [row for row in rows if row['label'] in required]
    rows.sort(key=lambda row: (row['batch_id'], row['sources'][0], row['recording_id'],
                               row['start_s'], row['end_s'], row['label'], row['definition_id'], row['id']))
    return rows


def validate_decision(decision, current):
    fields(decision, ('finding_id', 'expected_revision', 'status', 'reviewer'),
           ('label', 'start_s', 'end_s', 'comment'), '人工复核')
    if type(decision['expected_revision']) is not int or decision['expected_revision'] != current['revision']:
        raise ValueError('复核版本冲突，请重新生成报告；整次导入已取消')
    if decision['status'] not in STATUSES:
        raise ValueError('未知人工复核状态')
    text(decision['reviewer'], 'reviewer', 100)
    comment = decision.get('comment', '')
    if not isinstance(comment, str) or len(comment) > 4000:
        raise ValueError('comment 必须为最多 4000 字符的文本')
    label = text(decision.get('label', current['label']), 'label', 100)
    start = finite(decision.get('start_s', current['start_s']), 'start_s', 0, current['duration_s'])
    end = finite(decision.get('end_s', current['end_s']), 'end_s', 0, current['duration_s'])
    if end <= start:
        raise ValueError('人工片段的 end_s 必须大于 start_s')
    changed = (label, start, end) != (current['automatic']['label'], current['automatic']['start_s'], current['automatic']['end_s'])
    if changed and decision['status'] != 'corrected':
        raise ValueError('修正标签或片段后，状态必须为 corrected')
    return {'status': decision['status'], 'reviewer': decision['reviewer'], 'label': label,
            'start_s': start, 'end_s': end, 'comment': comment}


def review(db, decision):
    current = current_finding(db, decision.get('finding_id'))
    payload = validate_decision(decision, current)
    db.execute('INSERT INTO reviews VALUES (?, ?, ?, ?)',
               (current['id'], current['revision'] + 1, now(), canonical(payload)))
    return current_finding(db, current['id'])


def add_manual(db, item):
    fields(item, ('id', 'batch_id', 'recording_id', 'config_hash', 'label', 'start_s', 'end_s', 'reviewer', 'comment'), name='漏检补标')
    identifier(item['id'], 'manual.id')
    text(item['comment'], '漏检原因', 4000)
    evaluation = db.execute('''SELECT r.duration_s FROM evaluations e JOIN recordings r ON r.id=e.recording_id
                               WHERE e.batch_id=? AND e.recording_id=? AND e.config_hash=?''',
                            (item['batch_id'], item['recording_id'], item['config_hash'])).fetchone()
    if evaluation is None:
        raise ValueError('漏检补标必须关联已有批次、录音和检测配置')
    payload = {'rule_id': None, 'label': item['label'], 'start_s': item['start_s'], 'end_s': item['end_s'],
               'reason': item['comment'], 'values': {}, 'units': {}, 'when': None, 'unless': None}
    current = {**payload, 'revision': 0, 'duration_s': evaluation['duration_s'], 'automatic': payload}
    decision = {'finding_id': item['id'], 'expected_revision': 0, 'status': 'confirmed',
                'reviewer': item['reviewer'], 'comment': item['comment']}
    checked = validate_decision(decision, current)
    if db.execute('SELECT 1 FROM findings WHERE id=?', (item['id'],)).fetchone():
        raise ValueError('漏检补标 id 已存在；请勿重复导入')
    db.execute('INSERT INTO findings VALUES (?, ?, ?, ?, ?, ?)',
               (item['id'], item['batch_id'], item['recording_id'], item['config_hash'], 'manual', canonical(payload)))
    db.execute('INSERT INTO reviews VALUES (?, 1, ?, ?)', (item['id'], now(), canonical(checked)))
    return current_finding(db, item['id'])


def import_reviews(db, packet):
    fields(packet, ('schema_version', 'library_id', 'reviews', 'manual'), name='复核导入文件')
    if packet['schema_version'] != '1.0' or packet['library_id'] != library_id(db):
        raise ValueError('复核文件版本或检测库身份不匹配')
    if not isinstance(packet['reviews'], list) or not isinstance(packet['manual'], list):
        raise ValueError('reviews 和 manual 必须为列表')
    if len(packet['reviews']) + len(packet['manual']) > 10000:
        raise ValueError('每次最多导入 10000 条复核')
    # The caller owns a transaction: any conflict rolls back every event in this packet.
    for decision in packet['reviews']:
        if not isinstance(decision, dict):
            raise ValueError('人工复核必须为对象')
        review(db, decision)
    for item in packet['manual']:
        add_manual(db, item)
    return {'reviews': len(packet['reviews']), 'manual': len(packet['manual'])}


def history(db, finding_id):
    current_finding(db, finding_id)
    return [{'revision': row['revision'], 'created_at': row['created_at'], **json.loads(row['payload'])}
            for row in db.execute('SELECT * FROM reviews WHERE finding_id=? ORDER BY revision', (finding_id,))]
