import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from voice_tools.audio.io import write_wav
from voice_tools.core.files import write_json
from voice_tools.tools.recording_qa.assessment import Policy
from voice_tools.tools.recording_qa.assessment_batch import run
from voice_tools.tools.recording_qa.assessment_review import FIELDS, check, evaluate
from tests.recording_qa.fixtures import FixtureModel, recording


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'calls';self.source.mkdir()
        self.audio=self.source/'call.wav';audio=recording();write_wav(self.audio,audio.samples,audio.sample_rate)
        self.out=self.root/'out';self.policy=Policy(channels_verified=True,ai_start_s=0,audit_percent=0)

    def run_batch(self,**kwargs):
        return run([self.source],self.out,self.policy,model=FixtureModel(),**kwargs)

    def rows(self):
        with (self.out/'recording-review.csv').open(encoding='utf-8-sig',newline='') as stream:return list(csv.DictReader(stream))

    def save_rows(self,rows):
        with (self.out/'recording-review.csv').open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=FIELDS);writer.writeheader();writer.writerows(rows)

    def test_one_recording_one_blank_human_row_and_self_contained_report(self):
        summary=self.run_batch(include_audio=True)
        self.assertEqual(summary['decisions']['AUTO_PASS'],1)
        self.assertEqual(summary['review_recordings'],0)
        rows=self.rows();self.assertEqual(len(rows),1);self.assertEqual(rows[0]['decision'],'')
        self.assertEqual(check(self.out/'recording-review.csv',self.out/'assessment.jsonl')[1],{})
        page=(self.out/'report.html').read_text()
        for marker in ['__SCRIPT__','__DATA__','__FILTER__','__STYLE__']:self.assertNotIn(marker,page)
        self.assertIn('整通人工结论',page)
        record=json.loads((self.out/'assessment.jsonl').read_text())
        for path in record['playback_sources'].values():self.assertTrue((self.out/path).is_file())

    def test_rules_only_never_passes_and_keeps_engineering_evidence(self):
        summary=self.run_batch(rules_only=True)
        self.assertEqual(summary['decisions']['NEEDS_REVIEW'],1)
        result=json.loads((self.out/'assessment.jsonl').read_text())['result']
        self.assertEqual(result['engineering']['status'],'completed')
        self.assertEqual(result['model']['status'],'not_run')

    def test_corrupt_input_and_model_failure_keep_partial_batch(self):
        (self.source/'broken.wav').write_bytes(b'broken')
        summary=self.run_batch()
        self.assertEqual(summary['files'],2);self.assertEqual(summary['errors'],1)
        self.assertEqual(summary['decisions']['AUTO_PASS'],1)
        rows=self.rows();self.assertEqual(len(rows),2)

    def test_model_failure_does_not_turn_into_normal(self):
        with patch.object(FixtureModel,'predict',side_effect=ValueError('inference failed')):
            summary=self.run_batch()
        self.assertEqual(summary['errors'],1)
        result=json.loads((self.out/'assessment.jsonl').read_text())['result']
        self.assertEqual(result['decision'],'NEEDS_REVIEW')
        self.assertEqual(result['engineering']['status'],'completed')

    def test_invalid_model_payload_becomes_reportable_error(self):
        with patch.object(FixtureModel,'predict',return_value={'status':'completed','coverage_s':float('nan')}):
            summary=self.run_batch()
        self.assertEqual(summary['errors'],1)
        self.assertEqual(summary['decisions']['NEEDS_REVIEW'],1)

    def test_duplicate_content_in_different_directories_has_separate_review_identity(self):
        other=self.source/'other';other.mkdir();(other/'call.wav').write_bytes(self.audio.read_bytes())
        self.run_batch();rows=self.rows()
        self.assertEqual(len(rows),2);self.assertNotEqual(rows[0]['assessment_id'],rows[1]['assessment_id'])

    def test_hidden_paths_and_hostile_filename_are_not_active_html(self):
        self.audio.rename(self.source/'<img onerror=alert(1)>.wav')
        self.run_batch(hide_paths=True)
        page=(self.out/'report.html').read_text()
        self.assertNotIn(str(self.source),page);self.assertNotIn('<img onerror=',page)
        self.assertIn('\\u003cimg',page)

    def test_missing_model_fails_before_creating_output_and_existing_output_refused(self):
        with self.assertRaises(ValueError):run([self.source],self.out,self.policy,model_dir=self.root/'missing')
        self.assertFalse(self.out.exists())
        self.run_batch()
        with self.assertRaises(ValueError):self.run_batch()

    def test_event_role_override_and_unverified_preparation_are_preserved(self):
        write_json(self.audio.with_suffix('.events.json'),{'schema_version':'1.0','system_channel':0,'channel_verified':False})
        summary=self.run_batch()
        self.assertEqual(summary['decisions']['NEEDS_REVIEW'],1)
        record=json.loads((self.out/'assessment.jsonl').read_text());self.assertEqual(record['result']['system_channel'],0)
        self.assertIn('events_sha256',record)

    def test_labels_validate_identity_timestamp_and_decision(self):
        self.run_batch();rows=self.rows()
        rows[0].update(decision='normal',reviewer='tester',reviewed_at='2026-09-21T12:00:00Z')
        self.save_rows(rows)
        self.assertEqual(len(check(self.out/'recording-review.csv',self.out/'assessment.jsonl')[1]),1)
        for field,value in [('audio_sha256','wrong'),('automatic_decision','AUTO_ANOMALY'),('reviewed_at','2026-09-21T12:00:00'),('decision','AUTO_PASS')]:
            edited=[{**rows[0],field:value}];self.save_rows(edited)
            with self.subTest(field=field),self.assertRaises(ValueError):check(self.out/'recording-review.csv',self.out/'assessment.jsonl')

    def test_automatic_pass_misses_are_visible_only_when_human_labels_exist(self):
        self.run_batch();csv_path=self.out/'recording-review.csv';results=self.out/'assessment.jsonl'
        metrics=evaluate(csv_path,results,'synthetic')
        self.assertIsNone(metrics['automatic_pass_error_on_reviewed'])
        rows=self.rows();rows[0].update(decision='abnormal',reviewer='tester',reviewed_at='2026-09-21T12:00:00Z');self.save_rows(rows)
        metrics=evaluate(csv_path,results,'synthetic')
        self.assertEqual(metrics['abnormal_in_automatic_pass'],1)
        self.assertEqual(metrics['automatic_pass_error_on_reviewed'],1)

    def test_duplicate_labels_incomplete_rows_and_legacy_csv_are_rejected(self):
        self.run_batch();rows=self.rows()
        for edited in [[rows[0],rows[0]],[{**rows[0],'notes':'not saved'}]]:
            self.save_rows(edited)
            with self.assertRaises(ValueError):check(self.out/'recording-review.csv',self.out/'assessment.jsonl')
        (self.out/'recording-review.csv').write_text('sample_id,decision\nanything,normal\n')
        with self.assertRaises(ValueError):check(self.out/'recording-review.csv',self.out/'assessment.jsonl')
