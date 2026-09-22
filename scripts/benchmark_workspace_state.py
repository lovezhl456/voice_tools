#!/usr/bin/env python3
"""Measure workspace refresh against identical synthetic historical payloads.

Run once with the baseline PYTHONPATH, then with the changed PYTHONPATH and the
same --root. Initialization/backfill is measured separately from refreshes.
"""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import time
import tracemalloc
import wave

import voice_tools
from voice_tools.tools.detect import store
from voice_tools.tools.detect.server import EditorApplication


def prepare(root, histories, samples):
    marker = root / '.benchmark.json'
    settings = {'histories': histories, 'samples': samples}
    if marker.exists():
        if json.loads(marker.read_text()) != settings:
            raise ValueError('Benchmark directory belongs to a different workload')
        return
    if root.exists() and any(root.iterdir()):
        raise ValueError('Use a new benchmark directory; unrelated data is never modified')
    inputs = root / 'inputs'
    inputs.mkdir(parents=True, exist_ok=True)
    audio = inputs / 'synthetic.wav'
    if not audio.exists():
        with wave.open(str(audio), 'wb') as stream:
            stream.setparams((2, 2, 8000, 0, 'NONE', 'not compressed'))
            stream.writeframes(b'\x00' * 32000)
    app = EditorApplication(root / 'library.sqlite3', [inputs], root / 'service')
    with store.connect(app.database) as db:
        if db.execute('SELECT COUNT(*) FROM sample_sets').fetchone()[0]:
            return
        # These are synthetic sizing fixtures, not human ground truth or accuracy evidence.
        standards = [{'id':str(index), 'revision':1, 'recording_id':f'{index:064x}', 'label':'benchmark',
                      'start_s':0, 'end_s':8, 'verdict':'problem', 'reviewer':'benchmark',
                      'comment':'synthetic historical payload'} for index in range(samples)]
        counts = dict(hits=1, misses=0, false_alarms=0, boundary_errors=0, unjudged=0, normal_correct=0)
        detail = [{'recording_id':f'{index:064x}', 'status':'ok', 'samples':[standards[index]],
                   'before':{'counts':counts,'labels':[]}, 'after':{'counts':counts,'labels':[]}}
                  for index in range(samples)]
        for index in range(histories):
            frozen = {'name':f'fixed-{index}', 'samples':standards}
            frozen['hash'] = hashlib.sha256(json.dumps(frozen).encode()).hexdigest()
            comparison = {'set_id':f'set-{index}', 'set_hash':frozen['hash'], 'set_name':frozen['name'],
                'before':'rule@1', 'after':'rule@2', 'config_hashes':['a','b'], 'status':'completed',
                'recordings':samples, 'comparable':samples, 'totals':{'before':counts,'after':counts},
                'matching':'synthetic load only', 'details':detail}
            db.execute('INSERT INTO sample_sets VALUES (?, ?, ?)', (f'set-{index}', str(index), json.dumps(frozen)))
            db.execute('INSERT INTO rule_comparisons VALUES (?, ?, ?)', (f'comparison-{index}', str(index), json.dumps(comparison)))
    marker.write_text(json.dumps(settings) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--histories', type=int, default=6)
    parser.add_argument('--samples', type=int, default=10000)
    parser.add_argument('--repeats', type=int, default=5)
    args = parser.parse_args()
    if min(args.histories, args.samples, args.repeats) <= 0:
        parser.error('histories, samples and repeats must be positive')
    root = args.root.resolve()
    prepare(root, args.histories, args.samples)
    started = time.perf_counter()
    app = EditorApplication(root / 'library.sqlite3', [root / 'inputs'], root / 'service')
    initialization_s = time.perf_counter() - started
    durations, peaks = [], []
    for _ in range(args.repeats):
        tracemalloc.start()
        started = time.perf_counter()
        encoded = json.dumps(app.workbench.state(), ensure_ascii=False).encode()
        durations.append(time.perf_counter() - started)
        peaks.append(tracemalloc.get_traced_memory()[1])
        tracemalloc.stop()
    result = {'source':voice_tools.__file__, 'histories':args.histories, 'samples_per_history':args.samples,
              'repeats':args.repeats, 'initialization_s':initialization_s,
              'median_refresh_s':statistics.median(durations), 'max_python_peak_bytes':max(peaks),
              'response_bytes':len(encoded), 'refresh_s':durations}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
