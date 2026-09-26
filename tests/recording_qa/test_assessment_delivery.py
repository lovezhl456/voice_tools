import json
from pathlib import Path
import re
import tempfile
import unittest

from voice_tools.audio.io import write_wav
from voice_tools.core.files import write_json
from voice_tools.tools.task import bundle,runner,review,catalog
from tests.recording_qa.fixtures import recording


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'source';self.source.mkdir()
        audio=recording();write_wav(self.source/'call.wav',audio.samples,audio.sample_rate)
        self.task={'schema_version':'1.0','id':'autoqa','title':'整通复核','inputs':{'audio':'call.wav'},
            'steps':[{'id':'assess','tool':'qa','action':'assess','params':{'inputs':[{'input':'audio'}],
                'rules_only':True,'include_audio':True,'channels_verified':True,'ai_start':0}}]}

    def pack_run(self, profile=None):
        path=self.source/'task.json';write_json(path,self.task)
        bundle.pack(path,self.source,self.root/'task.zip')
        return runner.run(self.root/'task.zip',self.root/'run',profile)

    def test_catalog_classifies_model_path_as_runtime_and_download_as_setup(self):
        entries=catalog.catalog()
        self.assertNotIn('qa.model-download',entries)
        arg=next(a for a in entries['qa.assess']['arguments'] if a['name']=='qa_model_dir')
        self.assertEqual(arg['role'],'runtime')
        self.assertEqual(catalog.capabilities({'tool':'qa','action':'assess'})['dependencies'],['qa_model'])
        self.assertEqual(catalog.capabilities(self.task['steps'][0])['dependencies'],[])
        self.assertEqual(entries['qa.assess-evaluate']['output_kind'],'file')

    def test_rules_only_task_rebuilds_whole_call_report_from_verified_data(self):
        receipt=self.pack_run();self.assertEqual(receipt['steps'][0]['status'],'findings')
        runner.collect(self.root/'run',self.root/'result.zip')
        review.review(self.root/'result.zip',self.root/'review')
        page=self.root/'review/assessment-review/assess/report.html'
        self.assertTrue(page.is_file());html=page.read_text()
        self.assertIn('返回任务报告',html);self.assertIn('整通人工结论',html)
        self.assertIn('assessment_review',(self.root/'review/index.html').read_text())
        data=json.loads(re.search(r'<script id="reviewData" type="application/json">(.*?)</script>',html,re.S)[1])
        self.assertEqual(len(data['records']),1)
        self.assertEqual(data['records'][0]['result']['decision'],'NEEDS_REVIEW')
        self.assertEqual(len(data['records'][0]['waveform']['channels']), 2)
        self.assertIn('同步双轨录音波形', html)
        for value in data['records'][0]['playback_sources'].values():self.assertTrue((page.parent/value).is_file())

    def test_untrusted_html_and_foreign_playback_are_not_reused(self):
        self.pack_run()
        file=next((self.root/'run').rglob('assessment.jsonl'))
        value=json.loads(file.read_text());value['playback_sources']={'both':'https://invalid.example/foreign.wav'}
        file.write_text(json.dumps(value)+'\n')
        (file.parent/'report.html').write_text('<script>window.untrustedBundleScript=1</script>')
        runner.collect(self.root/'run',self.root/'result.zip');review.review(self.root/'result.zip',self.root/'review')
        page=self.root/'review/assessment-review/assess/report.html';html=page.read_text()
        self.assertNotIn('window.untrustedBundleScript',html);self.assertNotIn('invalid.example',html)

    def test_missing_model_dependency_is_reported_before_task_run(self):
        self.task['steps'][0]['params'].pop('rules_only')
        path=self.source/'task.json';write_json(path,self.task);bundle.pack(path,self.source,self.root/'task.zip')
        profile=self.root/'executor.json';write_json(profile,{'schema_version':'1.0','environments':{},'qa_model_dir':str(self.root/'missing')})
        result=runner.check(self.root/'task.zip',profile)
        self.assertFalse(result['ready'])
        self.assertTrue(any('语音模型' in item for item in result['issues']))

    def test_failed_model_doctor_blocks_dependants_but_not_independent_steps(self):
        self.task['steps'] = [
            {'id': 'doctor', 'tool': 'qa', 'action': 'model-doctor',
             'params': {}},
            {**self.task['steps'][0], 'depends_on': ['doctor']},
            {'id': 'inspect', 'tool': 'audio', 'action': 'inspect',
             'params': {'inputs': [{'input': 'audio'}]}},
        ]
        profile = self.root / 'executor.json'
        write_json(profile, {'schema_version': '1.0', 'environments': {},
                             'qa_model_dir': str(self.root / 'missing-model')})
        receipt = self.pack_run(profile)
        self.assertEqual([step['status'] for step in receipt['steps']], ['failed', 'skipped', 'completed'])
        self.assertEqual(receipt['status'], 'failed')
        self.assertFalse((self.root / 'run/steps/assess').exists())

    def test_model_doctor_requires_successful_ready_receipt(self):
        cases = [(0, {'summary': {'ready': True}}, 'completed'),
                 (1, {'summary': {'ready': False}}, 'failed'),
                 (1, {'summary': {'ready': True}}, 'failed'),
                 (0, {'summary': {'ready': False}}, 'failed'),
                 (0, {'summary': []}, 'failed'), (0, None, 'failed'),
                 (3, {'summary': {'ready': False}}, 'failed'), (-2, None, 'interrupted')]
        for code, payload, expected in cases:
            with self.subTest(code=code, payload=payload):
                self.assertEqual(runner.classify('qa', 'model-doctor', code, payload), expected)
        self.assertEqual(runner.classify('qa', 'assess', 1, {'summary': {'decisions': {'NEEDS_REVIEW': 1}}}), 'findings')

    def test_malformed_assessment_is_reported_instead_of_rendering_a_broken_page(self):
        self.pack_run()
        file=next((self.root/'run').rglob('assessment.jsonl'))
        value=json.loads(file.read_text());value['result']['findings']=[None]
        file.write_text(json.dumps(value)+'\n')
        runner.collect(self.root/'run',self.root/'result.zip')
        review.review(self.root/'result.zip',self.root/'review')
        html=(self.root/'review/index.html').read_text()
        self.assertIn('assessment_review_error',html)
        self.assertFalse((self.root/'review/assessment-review/assess/report.html').exists())

    def test_malformed_waveform_in_result_package_is_not_rendered(self):
        self.pack_run()
        path = next((self.root / 'run').rglob('assessment.jsonl'))
        value = json.loads(path.read_text())
        value['waveform']['channels'] = [[['<script>', 1]]]
        path.write_text(json.dumps(value) + '\n')
        runner.collect(self.root / 'run', self.root / 'result.zip')
        review.review(self.root / 'result.zip', self.root / 'review')
        self.assertIn('assessment_review_error', (self.root / 'review/index.html').read_text())
        self.assertFalse((self.root / 'review/assessment-review/assess/report.html').exists())
