import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from voice_tools.cli import main
from voice_tools.core.files import sha256, write_json
from voice_tools.tools.report.pcap import analyze, analyze_rows
from voice_tools.tools.report.service import build
from tests.report.fixtures import make_audio, make_pcap


def row(seq, when, stamp, pt=0):
    return ['1', str(when), '214', '214', '192.0.2.1', '', '16000', '192.0.2.2', '', '24000',
            '0x12345678', str(seq), str(stamp), str(pt)]


class ReportTests(unittest.TestCase):
    def test_wrap_reorder_duplicates_and_one_missing(self):
        seq = [65534, 65535, 0, 2, 1, 2, 4]
        rows = [row(s, i * .02, ((s - seq[0]) % 65536) * 160) for i, s in enumerate(seq)]
        stream = analyze_rows(rows)['streams'][0]
        self.assertEqual(stream['sequence_gap_candidates'], 1)
        self.assertEqual(stream['duplicate_candidates'], 1)
        self.assertEqual(stream['reordered_packets'], 1)
        self.assertEqual(stream['sequence_discontinuities'], 0)
        self.assertEqual(stream['unique_packets'], 6)

    def test_pt_switch_does_not_create_false_loss(self):
        streams = analyze_rows([row(1, 0, 0), row(2, .02, 160, 101), row(3, .04, 320)])['streams']
        self.assertEqual(len(streams), 1)
        self.assertEqual(streams[0]['sequence_gap_candidates'], 0)
        self.assertEqual(streams[0]['payload_types'], [0, 101])
        self.assertIsNone(streams[0]['jitter_max_ms'])

    def test_known_clock_and_timestamp_wrap(self):
        rows = [row(1, 0, 0xffffff60), row(2, .02, 0), row(3, .04, 160)]
        self.assertEqual(analyze_rows(rows)['streams'][0]['jitter_max_ms'], 0)
        rows = [row(1, 0, 0, 111), row(2, .02, 960, 111)]
        self.assertIsNone(analyze_rows(rows)['streams'][0]['jitter_max_ms'])
        self.assertEqual(analyze_rows(rows, {111: 48000})['streams'][0]['jitter_max_ms'], 0)

    def test_restart_and_analysis_cap_are_explicit(self):
        result = analyze_rows([row(1, 0, 0), row(9000, .02, 160)])
        self.assertEqual(result['streams'][0]['sequence_discontinuities'], 1)
        self.assertEqual(result['streams'][0]['sequence_gap_candidates'], 0)
        self.assertTrue(analyze_rows([row(1, 0, 0), row(2, .02, 160)], max_packets=1)['packet_limit_reached'])

    def test_audio_batch_error_isolation_and_html_escaping(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_audio(root / 'a.wav', silent=True)
            make_audio(root / 'b.wav')
            (root / 'bad.wav').write_bytes(b'bad')
            output = root / 'report'
            data = build(output, [root / 'a.wav', root / 'b.wav', root / 'bad.wav'], include_audio=True,
                         title='<script>alert(1)</script>')
            self.assertEqual(data['errors'], 1)
            self.assertTrue(data['partial'])
            self.assertEqual(data['audio'][0]['health']['channels'][1]['silence_fraction'], 1)
            self.assertEqual(len(list((output / 'audio').glob('*.wav'))), 2)
            document = (output / 'report.html').read_text()
            self.assertNotIn('<script>', document)
            self.assertIn('&lt;script&gt;', document)
            self.assertIn('未定位根因', document)
            self.assertEqual(json.loads((output / 'report.json').read_text())['errors'], 1)
            with self.assertRaises(ValueError):
                build(output, [root / 'a.wav'])

    def test_capture_checksum_and_per_file_ports(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            capture = root / 'cap'
            capture.mkdir()
            make_pcap(capture / 'session.pcap')
            make_pcap(root / 'unrelated.pcap')
            meta = {'schema_version': '1.0', 'tool': 'capture', 'status': 'complete',
                    'pcap': {'file': 'session.pcap', 'sha256': sha256(capture / 'session.pcap')},
                    'flows': [{'local_port': 16000, 'remote_port': 24000}]}
            write_json(capture / 'capture.json', meta)
            with patch('voice_tools.tools.report.pcap.analyze', side_effect=ValueError('test skip')) as analyzer:
                result = build(root / 'report', pcap_paths=[root / 'unrelated.pcap'], captures=[capture])
                self.assertEqual(result['errors'], 2)
                self.assertEqual(analyzer.call_args_list[0].args[1], set())
                self.assertEqual(analyzer.call_args_list[1].args[1], {16000, 24000})
            meta['pcap']['sha256'] = 'wrong'
            write_json(capture / 'capture.json', meta)
            with self.assertRaisesRegex(ValueError, 'SHA-256'):
                build(root / 'bad', captures=[capture])

    def test_missing_tshark_still_writes_audio_and_error_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_audio(root / 'a.wav')
            make_pcap(root / 'a.pcap')
            result = build(root / 'out', [root / 'a.wav'], [root / 'a.pcap'], tshark='/nonexistent/tshark')
            self.assertEqual(result['errors'], 1)
            self.assertEqual(result['audio'][0]['status'], 'ok')
            self.assertIn('tshark', result['pcaps'][0]['error'])

    def test_cli_invalid_clock_and_no_input(self):
        for args in (['--clock-rate', '111=0'], ['--clock-rate', 'nan'], []):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                main(['report', 'build', '--out', '/tmp/unused-report-test', *args])
            self.assertEqual(raised.exception.code, 2)

    def test_new_tools_machine_envelope_and_schema(self):
        for tool, actions in (('capture', {'start', 'fetch', 'batch', 'fetch-batch'}), ('report', {'build'})):
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(main(['schema', '--tool', tool]), 0)
            self.assertEqual(set(json.loads(stdout.getvalue())['cli']['commands']), actions)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_audio(root / 'a.wav')
            for args in (['capture', 'start', '--host', 'example-fs', '--flow', '192.0.2.1', '16000', '192.0.2.2', '24000',
                          '--dry-run', '--out', str(root / 'plan')],
                         ['report', 'build', '--audio', str(root / 'a.wav'), '--out', str(root / 'report')]):
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    self.assertEqual(main(['--json', *args]), 0)
                result = json.loads(stdout.getvalue())
                self.assertTrue(result['ok'])
                self.assertEqual(result['tool'], args[0])
                self.assertTrue(all(Path(p).exists() for p in result['artifacts'].values()))

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg unavailable')
    def test_compressed_recording_keeps_channels_and_playable_wav(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_audio(root / 'a.wav')
            subprocess.run(['ffmpeg', '-v', 'error', '-i', str(root/'a.wav'), str(root/'a.flac')], check=True)
            result = build(root/'out', [root/'a.flac'], include_audio=True)
            self.assertEqual(result['errors'], 0)
            self.assertEqual(result['audio'][0]['channels'], 2)
            self.assertEqual(result['audio'][0]['input_info']['codec'], 'flac')
            from voice_tools.audio.io import read_wav
            copied = read_wav(root/'out'/result['audio'][0]['playback'])
            self.assertEqual(copied.samples.shape[1], 2)
            self.assertEqual(copied.duration_s, 3)

    @unittest.skipUnless(shutil.which('tshark'), 'tshark unavailable')
    def test_real_tshark_and_corrupt_pcap(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_pcap(root / 'test.pcap')
            result = analyze(root / 'test.pcap', [16000])
            self.assertEqual(result['packets_examined'], 7)
            self.assertEqual(len(result['streams']), 1)
            self.assertEqual(result['streams'][0]['sequence_gap_candidates'], 1)
            self.assertEqual(result['streams'][0]['duplicate_candidates'], 1)
            self.assertEqual(result['streams'][0]['reordered_packets'], 1)
            self.assertTrue(analyze(root / 'test.pcap', [16000], max_packets=3)['packet_limit_reached'])
            (root / 'bad.pcap').write_bytes((root / 'test.pcap').read_bytes()[:-10])
            with self.assertRaisesRegex(ValueError, '完整读取'):
                analyze(root / 'bad.pcap', [16000])
