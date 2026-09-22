"""Synthetic fixtures using the installed product; no personal recordings or model downloads."""
import argparse
import copy
import hashlib
import json
from pathlib import Path

import numpy as np
import voice_tools
from voice_tools.audio.io import read_wav, write_wav
from voice_tools.core.files import write_json
from voice_tools.tools.gaps.service import analyze_batch as analyze_gaps
from voice_tools.tools.recording_qa.assessment import Policy
from voice_tools.tools.recording_qa.assessment_batch import run, summarize
from voice_tools.tools.recording_qa.assessment_report import render
from voice_tools.tools.recording_qa.batch import analyze_batch
from voice_tools.tools.recording_qa.detector import Config
from voice_tools.tools.task import bundle, runner, review


def sample_key(audio):
    return hashlib.sha256(audio.samples.tobytes()).hexdigest()


class FixtureSpeechModel:
    """Explicit model evidence keeps UI tests separate from neural-model accuracy."""
    identity = {'name': 'browser-fixture', 'version': '1', 'sha256': 'f' * 64}

    def __init__(self):
        self.predictions = {}

    def predict(self, audio):
        return {**self.identity, 'status': 'completed', 'coverage_s': audio.duration_s,
                'speech': self.predictions[sample_key(audio)]}


def recording(duration, user, agent):
    rate = 8000
    samples = np.zeros((round(duration * rate), 2), dtype=np.float32)
    for channel, spans in enumerate((user, agent)):
        for start, end in spans:
            left, right = round(start * rate), round(end * rate)
            frequency = 337 if channel == 0 else 220
            samples[left:right, channel] = .2 * np.sin(2 * np.pi * frequency * np.arange(right-left) / rate)
    return samples, rate


def generate(output, package_root):
    source = Path(voice_tools.__file__).resolve()
    if not source.is_relative_to(package_root.resolve()):
        raise RuntimeError(f'Expected installed wheel, got {source}')
    output.mkdir(parents=True, exist_ok=False)
    # duration, actual user spans, actual agent spans, model agent spans, verified roles
    user = [[.5, 1.5]]
    cases = {
        'normal': (8, user, [[2, 3]], [[2, 3]], True),
        'multiturn': (10, [[.5, 1.5], [6, 7]], [[2, 3], [7.5, 8.5]], [[2, 3], [7.5, 8.5]], True),
        'missing': (10, user, [], [], True),
        'late': (10, user, [[7, 8]], [[7, 8]], True),
        'output_gap': (10, user, [[2, 3], [6, 7]], [[2, 3], [6, 7]], True),
        'model_conflict': (8, user, [], [[2, 3]], True),
        'old_output': (8, user, [[.2, 3]], [[.2, 3]], True),
        'unknown_roles': (8, user, [[2, 3]], [[2, 3]], False),
    }
    model = FixtureSpeechModel()
    inputs = output / 'inputs'
    for name, (duration, user_spans, agent, model_agent, verified) in cases.items():
        folder = inputs / name
        folder.mkdir(parents=True)
        samples, rate = recording(duration, user_spans, agent)
        path = folder / 'call.wav'
        write_wav(path, samples, rate)
        write_json(path.with_suffix('.events.json'), {'schema_version': '1.0', 'system_channel': 1,
                   'channel_verified': verified, 'ai_start_s': 0})
        model.predictions[sample_key(read_wav(path))] = [user_spans, model_agent]
    summary = run([inputs], output / 'main', Policy(audit_percent=0), model=model, include_audio=True)
    expected = {'AUTO_PASS': 2, 'AUTO_ANOMALY': 3, 'NEEDS_REVIEW': 3}
    if summary['errors'] or summary['decisions'] != expected:
        raise RuntimeError(f'Unexpected synthetic fixture results: {summary}')
    rows = [json.loads(line) for line in (output / 'main/assessment.jsonl').read_text().splitlines()]
    for name in ('model_conflict', 'old_output'):
        row = next(row for row in rows if Path(row['input']).parent.name == name)
        assert row['result']['decision'] == 'NEEDS_REVIEW'
    legacy = copy.deepcopy(rows)
    for row in legacy:
        row.pop('waveform')
    render(output / 'main/legacy.html', legacy, summary)
    zero = copy.deepcopy(rows[0])
    zero['result'].update(review_windows=[[1, 1]], checked_range=[1, 1],
                          decision='NEEDS_REVIEW', review_required=True)
    render(output / 'main/zero-range.html', [zero], summarize([zero]))
    for field in ('review_windows', 'checked_range'):
        rounded = copy.deepcopy(rows[0])
        window = [1, rounded['result']['duration_s'] + .000005]
        rounded['result']['review_windows'] = [window] if field == 'review_windows' else []
        rounded['result']['checked_range'] = window
        render(output / f'main/rounded-{field}.html', [rounded], summarize([rounded]))
    no_roles = copy.deepcopy(rows[0])
    no_roles['result'].pop('channel_verified')
    no_roles['result'].pop('system_channel')
    render(output / 'main/legacy-roles.html', [no_roles], summarize([no_roles]))
    run([inputs / 'normal'], output / 'no-audio', Policy(), rules_only=True)
    unusual = output / 'unusual-inputs'
    unusual.mkdir()
    normal = read_wav(inputs / 'normal/call.wav')
    write_wav(unusual / 'mono.wav', normal.samples[:, :1], normal.sample_rate)
    (unusual / 'broken.wav').write_bytes(b'broken audio fixture')
    run([unusual], output / 'unusual', Policy(), rules_only=True, include_audio=True)
    analyze_batch([inputs / 'missing'], output / 'opportunity', Config(), include_audio=True)
    gaps = analyze_gaps([inputs / 'output_gap'], output / 'gaps', include_audio=True)
    assert gaps['errors'] == 0 and gaps['candidates'] > 0
    task = {'schema_version': '1.0', 'id': 'browser-review', 'title': 'Synthetic browser acceptance',
            'inputs': {'audio': 'missing'}, 'steps': [{'id': 'assess', 'tool': 'qa', 'action': 'assess',
            'params': {'inputs': [{'input': 'audio'}], 'rules_only': True, 'include_audio': True}}]}
    write_json(inputs / 'task.json', task)
    bundle.pack(inputs / 'task.json', inputs, output / 'task.zip')
    receipt = runner.run(output / 'task.zip', output / 'task-run')
    assert receipt['steps'][0]['status'] == 'findings'
    runner.collect(output / 'task-run', output / 'result.zip')
    review.review(output / 'result.zip', output / 'task-review')
    manifest = {'tool_version': voice_tools.__version__, 'imported_from': str(source),
                'numpy': np.__version__, 'cases': len(cases), 'decisions': summary['decisions'],
                'model': 'deterministic fixture; not real inference',
                'reports': ['main/report.html', 'no-audio/report.html', 'main/legacy.html',
                            'unusual/report.html', 'opportunity/review.html', 'gaps/review.html',
                            'task-review/index.html']}
    write_json(output / 'fixtures.json', manifest)
    links = ''.join(f'<li><a href="{path}">{path}</a></li>' for path in manifest['reports'])
    (output / 'index.html').write_text(f'<!doctype html><html lang="en"><title>Review acceptance</title>'
                                     f'<h1>Synthetic review fixtures</h1><ul>{links}</ul></html>')
    print(json.dumps(manifest))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--package-root', type=Path, required=True)
    args = parser.parse_args()
    generate(args.out.resolve(), args.package_root.resolve())
