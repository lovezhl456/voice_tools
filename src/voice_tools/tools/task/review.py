"""Build a local viewer from data. Never execute HTML/scripts supplied in a bundle."""
import json
from pathlib import Path
import shutil
import wave
import array
from urllib.parse import quote

from voice_tools.core.files import new_output, read_json
from .bundle import unpack
from .catalog import catalog
from .contract import ID

WEB = Path(__file__).parent / 'web'


def embedded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')


def page(output, name, data):
    for asset in ('style.css', 'app.js'):
        shutil.copyfile(WEB / asset, output / asset)
    html = (WEB / 'index.html').read_text()
    (output / 'index.html').write_text(html.replace('/*__DATA__*/', 'window.VT_DATA=' + embedded({'mode': name, **data}) + ';'))


def workbench(output):
    output = new_output(output)
    page(output, 'author', {'catalog': catalog()})
    return {'index': str((output / 'index.html').resolve()), 'network_accessed': False}


def peaks(path):
    try:
        if path.stat().st_size > 64 * 1024**2: return None
        with wave.open(str(path)) as reader:
            rate, channels, frames = reader.getframerate(), reader.getnchannels(), reader.getnframes()
            if reader.getsampwidth() != 2 or channels not in (1, 2) or not frames: return None
            width = max(1, (frames + 799) // 800)
            bins = [[] for _ in range(channels)]
            while True:
                samples = array.array('h', reader.readframes(width))
                if not samples: break
                for c in range(channels): bins[c].append(round(max(abs(v) for v in samples[c::channels]) / 32768, 4))
            return {'duration': frames / rate, 'channels': bins}
    except (OSError, wave.Error, EOFError, ValueError): return None


def review(package, output):
    output = new_output(output).resolve()
    manifest = unpack(package, output / 'evidence', 'voice_result')
    root = output / 'evidence'; receipt = read_json(root / 'run.json')
    if receipt.get('kind') != 'task_run' or not isinstance(receipt.get('steps'), list): raise ValueError('运行回执无效')
    audio_index, aliases = {}, {}
    source_root = root / 'work' / 'inputs'
    candidates = [p for p in root.rglob('*') if p.is_file() and p.suffix.lower() in ('.wav', '.flac', '.mp3') and ('steps' in p.relative_to(root).parts or source_root in p.parents)]
    for index, path in enumerate(sorted(candidates)[:2000]):
        rel = path.relative_to(root).as_posix()
        item = {'name': rel, 'path': quote(path.relative_to(output).as_posix()), 'wave': peaks(path) if index < 128 and path.suffix.lower() == '.wav' else None}
        audio_index[rel] = item
        if receipt.get('execution_directory'): aliases[str(Path(receipt['execution_directory']) / rel)] = item
    rows = []
    for step in receipt['steps']:
        if not isinstance(step.get('id'), str) or not ID.fullmatch(step['id']): raise ValueError('回执步骤 ID 无效')
        item = dict(step); item.update(files=[], assertions=[], scores=[], records=[], audio=[])
        folder = root / 'steps' / step['id']
        if folder.exists():
            for path in sorted(folder.rglob('*')):
                if not path.is_file(): continue
                relative = quote(path.relative_to(output).as_posix())
                item['files'].append({'name': path.relative_to(folder).as_posix(), 'path': relative, 'bytes': path.stat().st_size})
                if path.relative_to(root).as_posix() in audio_index:
                    item['audio'].append(audio_index[path.relative_to(root).as_posix()])
                if path.name == 'assertions.json':
                    try: item['assertions'] += read_json(path).get('items', [])
                    except (ValueError, OSError): pass
                if path.suffix == '.jsonl':
                    with path.open(errors='replace') as stream:
                        for line in stream:
                            if len(item['records']) >= 2000: item['records_truncated'] = True; break
                            try: row = json.loads(line)
                            except ValueError: continue
                            if not isinstance(row, dict): continue
                            match = aliases.get(str(row.get('file') or row.get('degraded') or row.get('input')))
                            if match: row['playback'] = match['path']
                            item['records'].append(row)
                            if isinstance(row, dict) and ('scores' in row or 'moslqo' in row):
                                match = aliases.get(str(row.get('file') or row.get('degraded') or row.get('input')))
                                if match:
                                    row['playback'] = match['path']
                                    if match not in item['audio']: item['audio'].append(match)
                                item['scores'].append(row)
                if path.name in ('stdout.json', 'report.json', 'comparison.json', 'metrics.json', 'result.json', 'batch-result.json') and path.stat().st_size < 4 * 1024**2:
                    try: item.setdefault('details', []).append({'name': path.relative_to(folder).as_posix(), 'value': read_json(path)})
                    except (OSError, ValueError): pass
        if not item['audio']:
            try:
                invocation = read_json(folder / 'invocation.json')
                def strings(v):
                    if isinstance(v,str): yield v
                    elif isinstance(v,list):
                        for x in v: yield from strings(x)
                    elif isinstance(v,dict):
                        for x in v.values(): yield from strings(x)
                selected = {}
                for value in strings(invocation):
                    for source, audio in aliases.items():
                        if source == value or source.startswith(value.rstrip('/') + '/'):
                            selected[audio['path']] = audio
                item['audio'] = list(selected.values())
            except (ValueError, OSError): pass
        rows.append(item)
    page(output, 'review', {'receipt': receipt, 'steps': rows, 'inputs_audio': [v for k,v in audio_index.items() if k.startswith('work/inputs/')], 'manifest': {'run_id': manifest.get('run_id'), 'files': len(manifest['files'])}})
    return {'index': str(output / 'index.html'), 'steps': len(rows), 'verified_files': len(manifest['files']), 'network_accessed': False}
