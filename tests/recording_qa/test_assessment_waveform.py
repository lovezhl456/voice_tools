import copy
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from voice_tools.audio.io import write_wav
from voice_tools.core.review.page import ASSETS, render_page
from voice_tools.tools.recording_qa import assessment_batch
from voice_tools.tools.recording_qa.assessment import Policy
from voice_tools.tools.recording_qa.assessment_report import render
from voice_tools.tools.recording_qa.assessment_review import validate_record
from tests.recording_qa.test_assessment import recording
from tests.recording_qa.test_assessment_workflow import FixtureModel


class AssessmentWaveformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        audio = recording()
        self.source = self.root / 'call.wav'
        write_wav(self.source, audio.samples, audio.sample_rate)
        self.policy = Policy(channels_verified=True, ai_start_s=0, audit_percent=0)

    def generate(self, name='report'):
        output = self.root / name
        summary = assessment_batch.run([self.source], output, self.policy, model=FixtureModel())
        record = json.loads((output / 'assessment.jsonl').read_text())
        return output, summary, record

    def test_offline_report_has_both_waveforms_without_copying_audio(self):
        output, _, record = self.generate()
        waveform = record['waveform']
        self.assertEqual(waveform['duration_s'], 8)
        self.assertEqual(len(waveform['channels']), 2)
        self.assertLessEqual(len(waveform['channels'][0]), 1200)
        self.assertNotEqual(waveform['channels'][0], waveform['channels'][1])
        self.assertNotIn('playback_sources', record)
        self.assertFalse((output / 'audio').exists())
        page = (output / 'report.html').read_text()
        for marker in ('__WAVEFORM_', '__PLAYBACK_', '__DATA__'):
            self.assertNotIn(marker, page)
        self.assertIn('createReviewWaveform', page)
        self.assertIn('WaveSurfer.js 7.12.12', page)
        self.assertIn('建议复核范围 · 固定', page)
        self.assertEqual(re.findall(r'<script\b[^>]*\bsrc=', page), [])
        payload = json.loads(re.search(r'<script id="reviewData" type="application/json">(.*?)</script>', page, re.S)[1])
        self.assertEqual(payload['records'][0]['waveform'], waveform)

    def test_waveform_is_display_only_and_does_not_change_label_identity(self):
        _, _, first = self.generate('first')
        changed = copy.deepcopy(first['waveform'])
        changed['channels'][0][0] = [-.5, .5]
        with patch.object(assessment_batch, 'waveform', return_value=changed):
            _, _, second = self.generate('second')
        self.assertEqual(first['assessment_id'], second['assessment_id'])
        self.assertEqual(first['result'], second['result'])

    def test_old_record_without_waveform_remains_renderable(self):
        _, summary, record = self.generate()
        record.pop('waveform')
        validate_record(record)
        render(self.root / 'legacy.html', [record], summary)
        self.assertIn('此录音未附带波形数据', (self.root / 'legacy.html').read_text())

    def test_malformed_imported_waveform_is_rejected(self):
        _, _, record = self.generate()
        wave = record['waveform']
        bad = [None, {**wave, 'bin_s': float('nan')}, {**wave, 'duration_s': 9},
               {**wave, 'channels': []}, {**wave, 'channels': [[[-1, 1]]] * 3},
               {**wave, 'channels': [[[-1, 1]] * 1201]},
               {**wave, 'channels': [[[1, -1]]]}, {**wave, 'channels': [[[-2, 2]]]},
               {**wave, 'channels': [[['<script>', 1]]]},
               {**wave, 'channels': [[[float('inf'), 1]]]},
               {**wave, 'channels': [wave['channels'][0], wave['channels'][1][:-1]]},
               {**wave, 'bin_s': .000001}]
        for value in bad:
            with self.subTest(value=str(value)[:80]), self.assertRaises(ValueError):
                validate_record({**record, 'waveform': value})

    def test_imported_channel_metadata_cannot_falsely_verify_roles(self):
        _, _, record = self.generate()
        bad = [{'channel_verified': flag} for flag in ('false', 'true', 0, 1, None)]
        bad += [{'system_channel': channel} for channel in (-1, 2, '1', True, 1.0, None)]
        for fields in bad:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                validate_record({**record, 'result': {**record['result'], **fields}})
        del record['result']['system_channel']
        with self.assertRaisesRegex(ValueError, '缺少系统声道'):
            validate_record(record)

    def test_legacy_missing_roles_remain_renderable_without_verification(self):
        _, summary, record = self.generate()
        for field in ('channel_verified', 'system_channel'):
            record['result'].pop(field)
        validate_record(record)
        render(self.root / 'unknown-roles.html', [record], summary)

    def test_imported_ranges_allow_only_the_documented_rounding_tolerance(self):
        _, _, record = self.generate()
        for field in ('review_windows', 'checked_range'):
            for excess, valid in ((.000005, True), (.00002, False)):
                value = [1, record['result']['duration_s'] + excess]
                candidate = {**record, 'result': {**record['result'], field: [value] if field == 'review_windows' else value}}
                with self.subTest(field=field, excess=excess):
                    if valid:
                        validate_record(candidate)
                    else:
                        with self.assertRaisesRegex(ValueError, '时间窗口'):
                            validate_record(candidate)

    def test_single_channel_and_failed_recording_do_not_break_report(self):
        audio = recording()
        write_wav(self.source, audio.samples[:, :1], audio.sample_rate)
        bad = self.root / 'broken.wav'
        bad.write_bytes(b'bad')
        output = self.root / 'mixed'
        result = assessment_batch.run([self.source, bad], output, self.policy, rules_only=True)
        rows = [json.loads(line) for line in (output / 'assessment.jsonl').read_text().splitlines()]
        self.assertEqual(result['errors'], 1)
        good = next(row for row in rows if not row.get('error'))
        self.assertEqual(len(good['waveform']['channels']), 1)
        self.assertNotIn('waveform', next(row for row in rows if row.get('error')))

    def test_shared_shell_keeps_legacy_playback_and_bundled_assets(self):
        target = self.root / 'legacy-review.html'
        render_page(target, {'records': []}, '/* adapter */')
        page = target.read_text()
        for identifier in ('waveform', 'start', 'end', 'player', 'channel', 'loop', 'play', 'volume', 'mute'):
            self.assertEqual(page.count(f'id="{identifier}"'), 1)
        self.assertIn('当前机会', page)
        self.assertIn('标注窗口 · 固定', page)
        for asset in ('waveform-panel.html', 'waveform-panel.css', 'playback-controls.html'):
            self.assertTrue((ASSETS / asset).is_file())
