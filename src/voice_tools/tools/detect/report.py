"""Portable local snapshot with shared waveforms; browser decisions import transactionally."""
import json
from pathlib import Path
import shutil
import uuid

from voice_tools.audio.io import read_wav, write_wav
from voice_tools.core.files import new_output, sha256, write_json
from voice_tools.core.review.page import ASSETS, embed_playback_assets
from . import store

ROOT = Path(__file__).parent


def add_audio(output, record):
    """Do not use a changed source just because its path still matches."""
    for source in record['sources']:
        path = Path(source)
        if not path.is_file():
            continue
        try:
            if sha256(path) != record['id']:
                continue
            directory = output / 'audio'
            directory.mkdir(exist_ok=True)
            copy = directory / (record['id'] + '.wav')
            shutil.copyfile(path, copy)
            if sha256(copy) != record['id']:
                copy.unlink()
                continue
            audio = read_wav(copy)
            record['playback_sources'] = {'both': str(copy.relative_to(output))}
            for channel in range(audio.samples.shape[1]):
                target = directory / (record['id'] + f'-ch{channel}.wav')
                write_wav(target, audio.samples[:, channel], audio.sample_rate)
                record['playback_sources']['left' if channel == 0 else 'right'] = str(target.relative_to(output))
            return
        except (ValueError, OSError) as error:
            record['audio_error'] = str(error)
    record['audio_error'] = '源音频不存在、已变化或无法读取；保留波形和检测证据，试听不可用'


def render(db, output, include_audio=False, hide_paths=False):
    output = new_output(output)
    records = []
    for row in db.execute('SELECT * FROM recordings ORDER BY id'):
        record = dict(row)
        record['sources'] = json.loads(record['sources'])
        record['waveform'] = json.loads(record['waveform'])
        record['result'] = {'duration_s': record['duration_s']}
        if include_audio:
            add_audio(output, record)
        record['input'] = record['sources'][0]
        records.append(record)
    payload = {'schema_version': '1.0', 'library_id': store.library_id(db), 'report_id': str(uuid.uuid4()),
               'created_at': store.now(), 'records': records, 'findings': store.query(db),
               'evaluations': store.evaluation_rows(db), 'batches': store.batch_details(db),
               'definitions': [{**row, 'config': json.loads(row['config'])}
                               for row in map(dict, db.execute('SELECT * FROM definitions ORDER BY id,version'))]}
    if hide_paths:
        for record in payload['records'] + payload['findings'] + payload['evaluations']:
            record['sources'] = [Path(path).name for path in record['sources']]
            if 'input' in record:
                record['input'] = Path(record['input']).name
        for batch in payload['batches']:
            for error in batch['errors']:
                source = error['source']
                error.update(source=Path(source).name, error=error['error'].replace(source, Path(source).name))
    write_json(output / 'results.json', payload)
    data = json.dumps(payload, ensure_ascii=False, allow_nan=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    page = embed_playback_assets((ROOT / 'report.html').read_text(encoding='utf-8'))
    page = page.replace('__STYLE__', (ROOT / 'report.css').read_text(encoding='utf-8'))
    page = page.replace('__FILTER__', (ASSETS / 'file-filter.js').read_text(encoding='utf-8'))
    page = page.replace('__SCRIPT__', (ROOT / 'report.js').read_text(encoding='utf-8')).replace('__DATA__', data)
    page = page.replace('当前机会', '当前片段').replace('标注窗口 · 固定', '检测片段 · 固定')
    (output / 'index.html').write_text(page, encoding='utf-8')
    return {'recordings': len(records), 'findings': len(payload['findings']), 'batches': len(payload['batches']),
            'audio_unavailable': sum('audio_error' in record for record in records)}
