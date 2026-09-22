"""Persistent daily settings, human standards and reproducible rule evaluation."""
import hashlib
import json
import uuid

from voice_tools.audio.io import read_wav
from voice_tools.core.files import sha256
from . import store
from .definition import canonical, fields, fingerprint, identifier, text
from .engine import analyze
from .metrics import finite


def initialize(db):
    # Additive tables leave the 1.0 CLI and historical detection records readable.
    statements = (
        'CREATE TABLE IF NOT EXISTS workspaces (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS standards (id TEXT NOT NULL, revision INTEGER NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(id, revision))',
        'CREATE TABLE IF NOT EXISTS sample_sets (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS rule_comparisons (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL)',
    )
    for statement in statements:
        db.execute(statement)


def settings(db, workspace_id):
    row = db.execute('SELECT * FROM workspaces WHERE id=?', (workspace_id,)).fetchone()
    return {'revision': row['revision'], **json.loads(row['payload'])} if row else None


def save_settings(db, workspace_id, value, expected_revision):
    previous = settings(db, workspace_id)
    revision = previous['revision'] if previous else 0
    if type(expected_revision) is not int or expected_revision != revision:
        raise ValueError('工作区设置已变化，请刷新后重试')
    text(value['name'], '工作区名称', 200)
    definitions(db, value['definitions'], allow_empty=True)
    db.execute('INSERT OR REPLACE INTO workspaces VALUES (?, ?, ?)',
               (workspace_id, revision + 1, canonical(value)))
    return settings(db, workspace_id)


def definitions(db, refs, allow_empty=False):
    if not isinstance(refs, list) or not (0 if allow_empty else 1) <= len(refs) <= 32:
        raise ValueError('请选择 1–32 个已保存规则版本')
    if any(not isinstance(ref, str) for ref in refs):
        raise ValueError('规则版本必须为文本')
    configs = [store.select_definition(db, ref) for ref in refs]
    if len({item['id'] for item in configs}) != len(configs):
        raise ValueError('同一定义只能选一个版本')
    return configs


def standards(db):
    rows = db.execute('''SELECT s.* FROM standards s JOIN
        (SELECT id, MAX(revision) AS revision FROM standards GROUP BY id) latest
        ON s.id=latest.id AND s.revision=latest.revision ORDER BY s.created_at,s.id''')
    result = []
    for row in rows:
        payload = json.loads(row['payload'])
        if payload['verdict'] != 'withdrawn':
            result.append({'id': row['id'], 'revision': row['revision'], 'created_at': row['created_at'], **payload})
    return result


def overlaps(a, b):
    return min(a['end_s'], b['end_s']) > max(a['start_s'], b['start_s'])


def save_standard(db, item):
    fields(item, ('id', 'expected_revision', 'recording_id', 'label', 'start_s', 'end_s', 'verdict', 'reviewer', 'comment', 'checked'),
           ('source',), '人工标准')
    identifier(item['id'], '样本 id')
    if item['checked'] is not True:
        raise ValueError('请先试听并明确确认检查范围')
    if item['verdict'] not in ('problem', 'normal', 'withdrawn'):
        raise ValueError('样本结论必须为 problem、normal 或 withdrawn')
    record = db.execute('SELECT * FROM recordings WHERE id=?', (item['recording_id'],)).fetchone()
    if record is None:
        raise ValueError('样本必须来自已检测的录音')
    text(item['label'], '业务标签', 100)
    text(item['reviewer'], '复核人', 100)
    text(item['comment'], '判断原因', 4000)
    start = finite(item['start_s'], 'start_s', 0, record['duration_s'])
    end = finite(item['end_s'], 'end_s', 0, record['duration_s'])
    if end <= start:
        raise ValueError('结束时间必须大于开始时间')
    row = db.execute('SELECT revision,payload FROM standards WHERE id=? ORDER BY revision DESC LIMIT 1', (item['id'],)).fetchone()
    revision = row['revision'] if row else 0
    if type(item['expected_revision']) is not int or revision != item['expected_revision']:
        raise ValueError('人工标准已被修改，请刷新后重试')
    if row and json.loads(row['payload'])['recording_id'] != item['recording_id']:
        raise ValueError('不能更改已有样本的录音身份')
    payload = {key: item[key] for key in ('recording_id', 'label', 'verdict', 'reviewer', 'comment')}
    payload.update(start_s=start, end_s=end, source=item.get('source', 'manual'))
    if not isinstance(payload['source'], str) or len(payload['source']) > 200:
        raise ValueError('样本来源无效')
    # Contradictory evidence needs an explicit correction, never last-write-wins.
    for previous in standards(db):
        if (previous['id'] != item['id'] and previous['recording_id'] == item['recording_id']
                and previous['label'] == item['label'] and overlaps(previous, payload)
                and {previous['verdict'], payload['verdict']} == {'problem', 'normal'}):
            raise ValueError('该标签的检查范围与已有人工结论冲突，请先修订或撤回原样本')
    db.execute('INSERT INTO standards VALUES (?, ?, ?, ?)', (item['id'], revision + 1, store.now(), canonical(payload)))
    return {'id': item['id'], 'revision': revision + 1, **payload}


def standard_from_finding(db, finding):
    key = 'finding-' + finding['id']
    row = db.execute('SELECT revision FROM standards WHERE id=? ORDER BY revision DESC LIMIT 1', (key,)).fetchone()
    revision = row['revision'] if row else 0
    verdict = {'confirmed': 'problem', 'corrected': 'problem', 'false_positive': 'normal', 'pending': 'withdrawn'}[finding['status']]
    if verdict == 'withdrawn' and not row:
        return
    save_standard(db, {'id': key, 'expected_revision': revision, 'recording_id': finding['recording_id'],
        'label': finding['label'], 'start_s': finding['start_s'], 'end_s': finding['end_s'], 'verdict': verdict,
        'reviewer': finding['reviewer'], 'comment': finding['comment'] or '人工确认问题片段',
        'checked': True, 'source': finding['id']})


def save_reviews(db, packet):
    # Validation and standards share the caller's transaction with the original history.
    fields(packet, ('schema_version', 'library_id', 'reviews', 'manual'), name='复核文件')
    if not isinstance(packet['reviews'], list) or any(not isinstance(item, dict) for item in packet['reviews']):
        raise ValueError('复核条目必须为对象列表')
    for decision in packet['reviews']:
        if decision.get('status') == 'false_positive':
            text(decision.get('comment'), '误报原因', 4000)
    result = store.import_reviews(db, packet)
    for item in packet['reviews']:
        standard_from_finding(db, store.current_finding(db, item['finding_id']))
    for item in packet['manual']:
        standard_from_finding(db, store.current_finding(db, item['id']))
    return result


def freeze(db, name, ids):
    text(name, '样本集名称', 200)
    if not isinstance(ids, list) or not ids or len(ids) > 10000 or any(not isinstance(i, str) for i in ids):
        raise ValueError('请选择 1–10000 条人工标准')
    available = {item['id']: item for item in standards(db)}
    if len(set(ids)) != len(ids) or any(key not in available for key in ids):
        raise ValueError('样本重复、已撤回或不存在，请刷新后重选')
    payload = {'name': name, 'samples': [available[key] for key in sorted(ids)]}
    payload['hash'] = hashlib.sha256(canonical(payload).encode()).hexdigest()
    identity = str(uuid.uuid4())
    db.execute('INSERT INTO sample_sets VALUES (?, ?, ?)', (identity, store.now(), canonical(payload)))
    return {'id': identity, **payload}


def saved_items(db, table):
    if table not in ('sample_sets', 'rule_comparisons'):
        raise ValueError('未知历史类型')
    return [{'id': row['id'], 'created_at': row['created_at'], **json.loads(row['payload'])}
            for row in db.execute('SELECT * FROM ' + table + ' ORDER BY created_at DESC')]


def merge_intervals(items):
    merged = []
    for item in sorted(items, key=lambda value: (value['start_s'], value['end_s'])):
        if merged and item['start_s'] < merged[-1]['end_s']:
            merged[-1]['end_s'] = max(merged[-1]['end_s'], item['end_s'])
        else:
            merged.append({'start_s': item['start_s'], 'end_s': item['end_s']})
    return merged


def score_intervals(samples, findings):
    """One-to-one interval matches; predictions outside inspected ranges stay unknown."""
    counts = dict(hits=0, misses=0, false_alarms=0, boundary_errors=0, unjudged=0, normal_correct=0)
    details = []
    labels = sorted({sample['label'] for sample in samples} | {finding['label'] for finding in findings})
    for label in labels:
        truth = merge_intervals([sample for sample in samples if sample['label'] == label and sample['verdict'] == 'problem'])
        normal = merge_intervals([sample for sample in samples if sample['label'] == label and sample['verdict'] == 'normal'])
        predicted = merge_intervals([finding for finding in findings if finding['label'] == label])
        candidates = []
        for expected in truth:
            options = []
            for index, actual in enumerate(predicted):
                intersection = max(0, min(expected['end_s'], actual['end_s']) - max(expected['start_s'], actual['start_s']))
                union = max(expected['end_s'], actual['end_s']) - min(expected['start_s'], actual['start_s'])
                if intersection / union >= 0.3:
                    options.append(index)
            candidates.append(options)
        matched = {}
        def assign(truth_index, visited):
            for prediction in candidates[truth_index]:
                if prediction in visited:
                    continue
                visited.add(prediction)
                if prediction not in matched or assign(matched[prediction], visited):
                    matched[prediction] = truth_index
                    return True
            return False
        for index in range(len(truth)):
            assign(index, set())
        current = dict(hits=len(matched), misses=len(truth) - len(matched), false_alarms=0,
                       boundary_errors=0, unjudged=0, normal_correct=0)
        for index, actual in enumerate(predicted):
            # A matched wide prediction may also intrude on a confirmed normal interval.
            if any(overlaps(actual, item) for item in normal):
                current['false_alarms'] += 1
            elif index not in matched:
                category = 'boundary_errors' if any(overlaps(actual, item) for item in truth) else 'unjudged'
                current[category] += 1
        current['normal_correct'] = sum(not any(overlaps(item, actual) for actual in predicted) for item in normal)
        for key, value in current.items():
            counts[key] += value
        details.append({'label': label, **current, 'expected': truth, 'normal': normal, 'predicted': predicted})
    return {'counts': counts, 'labels': details}


def compare_set(db, set_id, before, after, paths, progress=None):
    if before == after:
        raise ValueError('请选择两个不同的规则版本')
    for ref in (before, after):
        text(ref, '规则版本', 161)
    text(set_id, '样本集 id', 80)
    configs = [store.select_definition(db, ref) for ref in (before, after)]
    if configs[0]['id'] != configs[1]['id']:
        raise ValueError('固定集比较必须选择同一定义的两个版本')
    row = db.execute('SELECT payload FROM sample_sets WHERE id=?', (set_id,)).fetchone()
    if row is None:
        raise ValueError('固定样本集不存在')
    dataset = json.loads(row['payload'])
    labels = {rule['label'] for config in configs for rule in config['rules']}
    if any(sample['label'] not in labels for sample in dataset['samples']):
        raise ValueError('样本集包含这两个规则版本都不检查的标签，请按标签选择并冻结专用样本集')
    grouped = {}
    for sample in dataset['samples']:
        grouped.setdefault(sample['recording_id'], []).append(sample)
    path_map = {}
    for path in paths:
        try:
            path_map.setdefault(sha256(path), path)
        except OSError:
            continue
    results = []
    for index, (rid, samples) in enumerate(sorted(grouped.items())):
        record = {'recording_id': rid, 'samples': samples}
        try:
            if rid not in path_map:
                raise ValueError('授权目录中没有摘要匹配的原录音')
            path = path_map[rid]
            audio = read_wav(path)
            if sha256(path) != rid:
                raise ValueError('录音在读取期间变化')
            for side, config in zip(('before', 'after'), configs):
                result = analyze(audio, config)
                if result['status'] != 'ok':
                    raise ValueError(result.get('note') or '没有可用分析窗口')
                record[side] = score_intervals(samples, result['findings'])
            record['status'] = 'ok'
        except (ValueError, OSError) as error:
            record = {'recording_id': rid, 'status': 'error', 'error': str(error)}
        results.append(record)
        if progress:
            progress(index + 1, len(grouped))
    comparable = [record for record in results if record['status'] == 'ok']
    totals = {side: {key: sum(record[side]['counts'][key] for record in comparable)
                     for key in ('hits', 'misses', 'false_alarms', 'boundary_errors', 'unjudged', 'normal_correct')}
              for side in ('before', 'after')}
    payload = {'set_id': set_id, 'set_hash': dataset['hash'], 'set_name': dataset['name'],
               'before': before, 'after': after, 'config_hashes': [fingerprint(config) for config in configs],
               'status': 'completed' if len(comparable) == len(results) else 'incomplete',
               'recordings': len(results), 'comparable': len(comparable), 'totals': totals, 'details': results,
               'matching': '同标签重叠片段合并；问题片段按 IoU >= 0.3 一对一匹配；正常范围内命中记误报，未检查范围保留未知。'}
    identity = str(uuid.uuid4())
    db.execute('INSERT INTO rule_comparisons VALUES (?, ?, ?)', (identity, store.now(), canonical(payload)))
    return {'id': identity, **payload}
