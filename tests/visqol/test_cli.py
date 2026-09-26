import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import wave

import numpy as np

from voice_tools.audio.io import write_wav
from voice_tools.tools.visqol.service import MODELS, read_pairs


# 模拟外部程序的协议，不冒充真实 ViSQOL 质量测试。实际后端另有本机验收记录。
FAKE_BACKEND = '''#!{python}
import json,sys,time
from pathlib import Path
args=sys.argv[1:]
def arg(k):return args[args.index(k)+1]
mode=Path(__file__).parent.parent/'behavior'
b=mode.read_text() if mode.exists() else 'ok'
print('native stdout')
print('native stderr',file=sys.stderr)
if b=='timeout':time.sleep(20)
if b=='exit':sys.exit(7)
if b=='missing':sys.exit(0)
r={{'moslqo':4.2,'referenceFilepath':arg('--reference_file'),'degradedFilepath':arg('--degraded_file')}}
if b=='nan':r['moslqo']=float('nan')
if b=='wrong-path':r['referenceFilepath']='someone-else.wav'
if b=='out-of-range':r['moslqo']=7
Path(arg('--output_debug')).write_text(json.dumps(r))
Path(arg('--results_csv')).write_text('reference,degraded,moslqo\\nref,deg,4.2\\n')
'''


class VisqolCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='visqol test,中文 ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.backend = self.root / 'backend'
        (self.backend / 'bazel-bin').mkdir(parents=True)
        (self.backend / 'model').mkdir()
        for name in MODELS.values():
            (self.backend / 'model' / name).write_text('fixture model')
        binary = self.backend / 'bazel-bin/visqol'
        binary.write_text(FAKE_BACKEND.format(python=sys.executable))
        binary.chmod(0o755)
        samples = .1 * np.sin(2 * np.pi * 200 * np.arange(16000 * 3) / 16000)
        self.reference = self.root / 'reference, original.wav'
        self.degraded = self.root / 'degraded.wav'
        write_wav(self.reference, samples, 16000)
        write_wav(self.degraded, samples * .8, 16000)

    def command(self, *args, env=None, cwd=None):
        result = subprocess.run([sys.executable, '-m', 'voice_tools', '--json', 'visqol', *map(str, args)],
                                capture_output=True, text=True, env=env, cwd=cwd)
        self.assertNotIn('Traceback', result.stderr)
        return result, json.loads(result.stdout)

    def score(self, out='out', *extra):
        return self.command('score', '--reference', self.reference, '--degraded', self.degraded,
                            '--visqol-dir', self.backend, '--out', self.root / out, *extra)

    def test_real_cli_protocol_artifacts_paths_and_native_output_isolation(self):
        result, envelope = self.score()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(envelope['summary']['completed'], 1)
        self.assertNotIn('native stdout', result.stdout)
        with (self.root / 'out/results.csv').open(newline='') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(rows[0]['reference'], str(self.reference))
        pair = json.loads((self.root / 'out/pairs/00001/result.json').read_text())
        self.assertEqual(pair['moslqo'], 4.2)
        self.assertEqual(pair['inputs']['reference']['channels'], 1)
        self.assertEqual(len(pair['inputs']['reference']['sha256']), 64)
        self.assertEqual((self.root / 'out/pairs/00001/reference.wav').read_bytes(), self.reference.read_bytes())
        self.assertIn('native stderr', (self.root / 'out/pairs/00001/stderr.log').read_text())
        run = json.loads((self.root / 'out/run.json').read_text())
        self.assertEqual(run['backend']['mode'], 'speech')
        self.assertEqual(len(run['backend']['model_sha256']), 64)

    def test_doctor_missing_dependency_and_environment_resolution(self):
        r, e = self.command('doctor', '--visqol-dir', self.root / 'missing')
        self.assertEqual(r.returncode, 2)
        self.assertFalse(e['ok'])
        env = dict(os.environ, VOICE_TOOLS_VISQOL_DIR=str(self.backend))
        r, e = self.command('doctor', env=env)
        self.assertEqual(r.returncode, 0)
        self.assertTrue(e['summary']['ready'])
        self.assertIn('不代表', e['summary']['check_scope'])

    def test_nonempty_output_is_never_overwritten(self):
        out = self.root / 'out'; out.mkdir(); (out / 'keep').write_text('user data')
        r, e = self.score()
        self.assertEqual(r.returncode, 2)
        self.assertEqual((out / 'keep').read_text(), 'user data')
        self.assertEqual(len(list(out.iterdir())), 1)

    def test_invalid_audio_has_null_score_and_no_backend_run(self):
        variants = ['stereo', 'rate', 'silence', 'truncated', 'too-short', 'missing']
        for index, variant in enumerate(variants):
            with self.subTest(variant=variant):
                data = np.full(3 * 16000, .1)
                if variant == 'stereo':data = np.column_stack([data, data])
                if variant == 'silence':data[:] = 0
                if variant == 'too-short':data = data[:100]
                write_wav(self.degraded, data, 8000 if variant == 'rate' else 16000)
                if variant == 'truncated':self.degraded.write_bytes(self.degraded.read_bytes()[:-200])
                if variant == 'missing':self.degraded.unlink()
                r, e = self.score(str(index))
                self.assertEqual(r.returncode, 3)
                self.assertEqual(e['summary']['errors'], 1)
                pair = json.loads((self.root / str(index) / 'pairs/00001/result.json').read_text())
                self.assertIsNone(pair['moslqo'])
                self.assertFalse((self.root / str(index) / 'pairs/00001/invocation.json').exists())

    def test_backend_failures_do_not_become_success_scores(self):
        for behavior in ('exit', 'missing', 'nan', 'wrong-path', 'out-of-range', 'timeout'):
            with self.subTest(behavior=behavior):
                (self.backend / 'behavior').write_text(behavior)
                r, e = self.score(behavior, '--timeout', '.2' if behavior == 'timeout' else '10')
                self.assertEqual(r.returncode, 3)
                self.assertFalse(e['ok'])
                pair = json.loads((self.root / behavior / 'pairs/00001/result.json').read_text())
                self.assertIsNone(pair['moslqo'])
                self.assertEqual(pair['status'], 'error')

    def test_batch_relative_paths_mixed_failures_continue(self):
        pairs = self.root / 'pairs.csv'
        with pairs.open('w', newline='') as f:
            w = csv.writer(f); w.writerow(['reference','degraded'])
            w.writerow([self.reference.name,'missing.wav'])
            w.writerow([self.reference.name,self.degraded.name])
        parsed = read_pairs(pairs)
        self.assertEqual(parsed[1]['reference'], self.reference)
        r, e = self.command('batch', '--pairs', pairs, '--visqol-dir', self.backend, '--out', self.root / 'batch')
        self.assertEqual(r.returncode, 3)
        self.assertEqual(e['summary']['pairs'], 2)
        self.assertEqual(e['summary']['completed'], 1)
        self.assertEqual(e['summary']['errors'], 1)
        rows = [json.loads(line) for line in (self.root / 'batch/results.jsonl').read_text().splitlines()]
        self.assertIsNone(rows[0]['moslqo']); self.assertEqual(rows[1]['moslqo'], 4.2)

    def test_empty_and_malformed_csv_fail_before_output(self):
        for i, data in enumerate(['reference,degraded\n', 'ref,deg\na,b\n', 'reference,degraded\na,b,c\n', 'reference,degraded\na,\n', '"reference,degraded\n']):
            p = self.root / f'p{i}.csv'; p.write_text(data)
            out = self.root / f'o{i}'
            r, e = self.command('batch', '--pairs', p, '--visqol-dir', self.backend, '--out', out)
            self.assertEqual(r.returncode, 2)
            self.assertFalse(out.exists())

    def test_audio_mode_and_timeout_validation(self):
        data = np.full(3 * 48000, .1)
        write_wav(self.reference, data, 48000); write_wav(self.degraded, data, 48000)
        r, e = self.score('audio', '--mode', 'audio')
        self.assertEqual(r.returncode, 0)
        invocation = json.loads((self.root / 'audio/pairs/00001/invocation.json').read_text())
        self.assertNotIn('--use_speech_mode', invocation['argv'])
        for i, timeout in enumerate(['0', '-1', 'nan', 'inf', '3601']):
            r, e = self.score(f'time{i}', '--timeout', timeout)
            self.assertEqual(r.returncode, 2)
            self.assertFalse((self.root / f'time{i}').exists())


if __name__ == '__main__':unittest.main()
