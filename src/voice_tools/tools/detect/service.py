"""Batch orchestration and comparable-version changes; never overwrites old runs."""
import json
import uuid

from voice_tools import __version__
from voice_tools.audio.io import read_wav
from voice_tools.audio.health import waveform
from voice_tools.core.files import sha256
from voice_tools.tools.recording_qa.batch import discover
from .definition import canonical, fingerprint, identifier, validate
from .engine import analyze
from .store import evaluation_rows, now, query, save_definition


def run(db, inputs, configs, batch_id=None, progress=None):
    paths = discover(inputs)
    configs = [validate(config) for config in configs]
    if not configs or len(configs) > 32:
        raise ValueError('每次选择 1–32 个检测定义')
    if len({config['id'] for config in configs}) != len(configs):
        raise ValueError('同一批次每个定义 id 只能选择一个版本；版本对比请创建不同批次')
    batch_id = batch_id or str(uuid.uuid4())
    identifier(batch_id, 'batch_id')
    if db.execute('SELECT 1 FROM batches WHERE id=?', (batch_id,)).fetchone():
        raise ValueError('batch_id 已存在，重跑必须使用新批次')
    for config in configs:
        save_definition(db, config)
    db.execute('INSERT INTO batches VALUES (?, ?, ?, ?, ?)', (batch_id, now(), 'running', __version__, 'detection-1'))
    summary = {'batch_id': batch_id, 'tool_version': __version__, 'files': len(paths), 'recordings': 0,
               'findings': 0, 'errors': 0, 'no_windows': 0, 'definitions': [config['id'] + '@' + config['version'] for config in configs]}
    seen = set()
    for index, path in enumerate(paths):
        if progress:
            progress(index, len(paths))
        try:
            digest = sha256(path)
            audio = read_wav(path)
            if sha256(path) != digest:
                raise ValueError('录音在读取期间变化，请停止写入后重新检测')
        except (ValueError, OSError) as error:
            db.execute('INSERT INTO input_errors VALUES (?, ?, ?)', (batch_id, str(path), str(error)))
            summary['errors'] += 1
            continue
        existing = db.execute('SELECT sources FROM recordings WHERE id=?', (digest,)).fetchone()
        sources = set(json.loads(existing['sources'])) if existing else set()
        sources.add(str(path))
        if existing:
            db.execute('UPDATE recordings SET sources=? WHERE id=?', (canonical(sorted(sources)), digest))
        else:
            db.execute('INSERT INTO recordings VALUES (?, ?, ?, ?, ?)',
                       (digest, audio.duration_s, audio.samples.shape[1], canonical(sorted(sources)), canonical(waveform(audio))))
        if digest in seen:
            continue
        seen.add(digest)
        summary['recordings'] += 1
        for config in configs:
            config_hash = fingerprint(config)
            try:
                result = analyze(audio, config)
            except (ValueError, OSError) as error:
                result = {'status': 'error', 'windows': 0, 'note': str(error), 'findings': []}
            if result['status'] == 'error':
                summary['errors'] += 1
            elif result['status'] == 'no_windows':
                summary['no_windows'] += 1
            db.execute('INSERT INTO evaluations VALUES (?, ?, ?, ?, ?, ?)',
                       (batch_id, digest, config_hash, result['status'], result['windows'], result['note']))
            for finding in result['findings']:
                db.execute('INSERT INTO findings VALUES (?, ?, ?, ?, ?, ?)',
                           (str(uuid.uuid4()), batch_id, digest, config_hash, 'auto', canonical(finding)))
                summary['findings'] += 1
    if progress:
        progress(len(paths), len(paths))
    status = 'partial' if summary['errors'] or summary['no_windows'] else 'completed'
    db.execute('UPDATE batches SET status=? WHERE id=?', (status, batch_id))
    summary['status'] = status
    return summary


def compare(db, before, after, definition):
    if before == after:
        raise ValueError('请选择两个不同的检测批次')
    snapshots = []
    for batch in (before, after):
        rows = [row for row in evaluation_rows(db, batch) if row['definition_id'] == definition]
        if not rows:
            raise ValueError(f'批次 {batch} 没有定义 {definition} 的检测记录')
        snapshots.append({row['recording_id']: row for row in rows})
    common = set(snapshots[0]) & set(snapshots[1])
    comparable = {rid for rid in common if all(snapshot[rid]['status'] == 'ok' for snapshot in snapshots)}
    auto = []
    for batch in (before, after):
        auto.append({(row['recording_id'], row['automatic']['label'])
                     for row in query(db, batch=batch, definition=definition) if row['origin'] == 'auto' and row['recording_id'] in comparable})
    changed = lambda items: [{'recording_id': rid, 'label': label} for rid, label in sorted(items)]
    truth, false_positives = set(), set()
    # Human evidence in the two compared runs; unreviewed recordings are never ground truth.
    for batch in (before, after):
        for row in query(db, batch=batch, definition=definition):
            if row['recording_id'] not in comparable:
                continue
            if row['status'] in ('confirmed', 'corrected'):
                truth.add((row['recording_id'], row['label']))
            if row['status'] == 'false_positive' or (row['status'] == 'corrected' and row['label'] != row['automatic']['label']):
                false_positives.add((row['recording_id'], row['automatic']['label']))
    conflicts = truth & false_positives
    truth -= conflicts
    false_positives -= conflicts
    old, new = auto
    return {'before': before, 'after': after, 'definition_id': definition,
            'before_versions': sorted({row['version'] for row in snapshots[0].values()}),
            'after_versions': sorted({row['version'] for row in snapshots[1].values()}),
            'comparable_recordings': len(comparable),
            'only_before': sorted(set(snapshots[0]) - set(snapshots[1])),
            'only_after': sorted(set(snapshots[1]) - set(snapshots[0])),
            'not_comparable': sorted(common - comparable),
            'added': changed(new - old), 'removed': changed(old - new), 'unchanged': changed(old & new),
            'resolved_false_positives': changed((old - new) & false_positives),
            'introduced_false_positives': changed((new - old) & false_positives),
            'recovered_misses': changed((new - old) & truth),
            'introduced_misses': changed((old - new) & truth),
            'human_evidence_conflicts': changed(conflicts),
            'scope': '按同一录音摘要与业务标签比较自动命中；仅已人工复核的标签用于误报/漏报分类，不推断未复核准确率或片段重叠等价。'}
