"""Run engineering checks and local model evidence for every input recording."""
from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

from voice_tools import __version__
from voice_tools.audio.io import read_wav, write_wav
from voice_tools.audio.health import waveform
from voice_tools.core.files import new_output, sha256, write_json
from .assessment import ALGORITHM, DECISIONS, Policy, assess
from .batch import discover, load_evidence
from .reports import write_csv


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def summarize(records):
    counts = Counter(record['result']['decision'] for record in records)
    review = [record for record in records if record['result']['review_required']]
    total = len(records)
    return {'kind': 'qa_assessment_run', 'schema_version': '1.0', 'tool_version': __version__,
            'created_at': datetime.now(timezone.utc).isoformat(), 'files': total,
            'decisions': {key: counts[key] for key in DECISIONS},
            'errors': sum(bool(record.get('error')) for record in records),
            'review_recordings': len(review), 'audit_recordings': sum(record['result']['audit_selected'] for record in records),
            'review_fraction': len(review)/total if total else None,
            'automatic_decision_fraction': (counts['AUTO_PASS']+counts['AUTO_ANOMALY'])/total if total else None,
            'model_completed': sum(record['result']['model'].get('status') == 'completed' for record in records),
            'analysis_turns': sum(len(record['result']['turns']) for record in records),
            'activity_opportunities': sum(record['result']['engineering'].get('opportunities_before_grouping', 0) for record in records),
            'suggested_listening_s': round(sum(b-a for record in review for a,b in record['result']['review_windows']), 3),
            'notice': '复核比例是本批路由统计；试听时长是建议窗口总长，不是实测人工耗时或真实漏检率。'}


def audio_previews(output, path, record, audio):
    folder = output / 'audio'
    folder.mkdir(exist_ok=True)
    stem = record['audio_sha256']
    full = folder / (stem + '.wav')
    if not full.exists():
        shutil.copyfile(path, full)
    sources = {'both': str(full.relative_to(output))}
    if audio.samples.shape[1] == 2:
        for channel, name in enumerate(('left', 'right')):
            target = folder / f'{stem}-{name}.wav'
            if not target.exists():
                write_wav(target, audio.samples[:, channel], audio.sample_rate)
            sources[name] = str(target.relative_to(output))
    record['playback_sources'] = sources


def run(inputs, output, policy=None, model_dir=None, rules_only=False, include_audio=False,
        hide_paths=False, use_event_channel=True, model=None):
    policy = policy or Policy()
    policy.validate()
    paths = discover(inputs)
    if len(paths) > 200:
        raise ValueError('整通质检单批最多 200 个录音，请分批处理')
    if not rules_only and model is None:
        from .speech_model import SpeechModel
        model = SpeechModel(model_dir)
    output = new_output(output)
    records = []
    for path in paths:
        record = {'kind': 'qa_assessment', 'schema_version': '1.0', 'tool_version': __version__,
                  'input': str(path), 'audio_sha256': ''}
        effective = policy
        model_result = {'status': 'not_run', 'error': '仅运行工程规则，未执行语音模型'}
        try:
            record['audio_sha256'] = sha256(path)
            metadata, evidence = load_evidence(path, record['audio_sha256'])
            record.update(evidence)
            if use_event_channel:
                effective = replace(policy, system_channel=metadata.get('system_channel', policy.system_channel))
            audio = read_wav(path)
            record['waveform'] = waveform(audio)
            if not rules_only:
                try:
                    model_result = model.predict(audio)
                except (ValueError, OSError) as error:
                    model_result = {**getattr(model, 'identity', {}), 'status': 'error', 'error': str(error)}
                    record['error'] = '语音模型未完成：' + str(error)
            record['result'] = assess(audio, record['audio_sha256'], metadata, effective, model_result)
            model_result = record['result']['model']
            if model_result['status'] == 'error':
                record['error'] = '语音模型证据无效：' + model_result['error']
            if record['result']['engineering']['status'] != 'completed':
                record.setdefault('error', '工程检查未完整完成')
            if include_audio:
                audio_previews(output, path, record, audio)
        except (ValueError, OSError) as error:
            record.pop('waveform', None)
            record['error'] = str(error)
            record['result'] = {'decision': 'NEEDS_REVIEW', 'review_required': True, 'audit_selected': False,
                                'duration_s': None, 'blockers': [str(error)], 'findings': [], 'turns': [],
                                'review_windows': [], 'model': model_result, 'engineering': {'status': 'error'}}
        record['assessment_id'] = identity({'algorithm': ALGORITHM, 'input': str(path),
            'audio': record['audio_sha256'], 'events': record.get('events_sha256'), 'policy': asdict(effective),
            'model': {key: model_result.get(key) for key in ('name','version','sha256','runtime','threshold','status')},
            'error': record.get('error')})
        records.append(record)
    summary = summarize(records)
    with (output/'assessment.jsonl').open('w', encoding='utf-8') as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False)+'\n')
    write_json(output/'run.json', summary)
    rows = [{'file': record['input'], 'assessment_id': record['assessment_id'],
             'decision': record['result']['decision'], 'review_required': record['result']['review_required'],
             'audit_selected': record['result']['audit_selected'], 'findings': len(record['result']['findings']),
             'error': record.get('error', '')} for record in records]
    write_csv(output/'summary.csv', list(rows[0]) if rows else [], rows)
    from .assessment_review import FIELDS, blank_row
    write_csv(output/'recording-review.csv', FIELDS, [blank_row(record) for record in records])
    from .assessment_report import render
    render(output/'report.html', records, summary, hide_paths)
    return summary
