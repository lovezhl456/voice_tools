import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from voice_tools.tools.detect import store, workspace
from voice_tools.tools.detect.definition import fingerprint
from voice_tools.tools.detect.server import EditorApplication
from voice_tools.tools.detect.service import run
from .fixtures import config, wav


class StandardsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.audio = wav(self.root / 'sample.wav')
        self.database = self.root / 'library.sqlite3'
        with store.connect(self.database, create=True) as db:
            workspace.initialize(db)
            run(db, [self.audio], [config()], 'first')
            self.finding = store.query(db)[0]

    def standard(self, **changes):
        return {'id': 'sample-1', 'expected_revision': 0, 'recording_id': self.finding['recording_id'],
                'label': '低电平', 'start_s': 0, 'end_s': 8, 'verdict': 'problem',
                'reviewer': 'tester', 'comment': '已试听', 'checked': True, **changes}

    def packet(self, **changes):
        decision = {'finding_id': self.finding['id'], 'expected_revision': 0,
                    'status': 'confirmed', 'reviewer': 'tester', 'comment': '已试听', **changes}
        with store.connect(self.database) as db:
            return {'schema_version': '1.0', 'library_id': store.library_id(db), 'reviews': [decision], 'manual': []}

    def test_online_review_accumulates_standard_and_keeps_history(self):
        with store.connect(self.database) as db:
            workspace.save_reviews(db, self.packet())
        with store.connect(self.database) as db:
            self.assertEqual(store.current_finding(db, self.finding['id'])['revision'], 1)
            self.assertEqual(workspace.standards(db)[0]['verdict'], 'problem')
            workspace.save_reviews(db, self.packet(expected_revision=1, status='false_positive', comment='提示音，不是问题'))
        with store.connect(self.database) as db:
            self.assertEqual(workspace.standards(db)[0]['verdict'], 'normal')
            self.assertEqual(len(store.history(db, self.finding['id'])), 2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM standards').fetchone()[0], 2)

    def test_missing_reason_and_stale_review_are_rejected_atomically(self):
        with self.assertRaises(ValueError), store.connect(self.database) as db:
            workspace.save_reviews(db, self.packet(status='false_positive', comment=''))
        with store.connect(self.database) as db:
            workspace.save_reviews(db, self.packet())
        with self.assertRaises(ValueError), store.connect(self.database) as db:
            workspace.save_reviews(db, self.packet(status='false_positive', comment='误报'))
        with store.connect(self.database) as db:
            self.assertEqual(len(store.history(db, self.finding['id'])), 1)
            self.assertEqual(workspace.standards(db)[0]['verdict'], 'problem')

    def test_normal_requires_explicit_inspection_and_conflicts_rejected(self):
        for update in ({'checked': False}, {'comment': ''}, {'end_s': 9}, {'reviewer': ''}):
            with self.subTest(update=update), self.assertRaises(ValueError), store.connect(self.database) as db:
                workspace.save_standard(db, self.standard(verdict='normal', **update))
        with store.connect(self.database) as db:
            workspace.save_standard(db, self.standard())
        with self.assertRaisesRegex(ValueError, '冲突'), store.connect(self.database) as db:
            workspace.save_standard(db, self.standard(id='normal', verdict='normal'))

    def test_frozen_samples_do_not_change_when_standard_is_revised(self):
        with store.connect(self.database) as db:
            workspace.save_standard(db, self.standard())
            frozen = workspace.freeze(db, 'fixed', ['sample-1'])
            workspace.save_standard(db, self.standard(expected_revision=1, end_s=4))
        with store.connect(self.database) as db:
            self.assertEqual(workspace.standards(db)[0]['end_s'], 4)
            loaded = workspace.saved_items(db, 'sample_sets')[0]
            self.assertEqual(loaded['hash'], frozen['hash'])
            self.assertEqual(loaded['samples'][0]['end_s'], 8)
            with self.assertRaises(ValueError):
                workspace.freeze(db, 'empty', [])

    def test_compare_records_regression_and_missing_source_not_success(self):
        with store.connect(self.database) as db:
            workspace.save_standard(db, self.standard())
            dataset = workspace.freeze(db, 'fixed', ['sample-1'])
            store.save_definition(db, config(version='2', threshold=-60))
            result = workspace.compare_set(db, dataset['id'], 'level@1', 'level@2', [self.audio])
            self.assertEqual(result['totals']['before']['hits'], 1)
            self.assertEqual(result['totals']['after']['misses'], 1)
            missing = workspace.compare_set(db, dataset['id'], 'level@1', 'level@2', [])
            self.assertEqual(missing['status'], 'incomplete')
            self.assertEqual(missing['comparable'], 0)
            with self.assertRaises(ValueError):
                workspace.compare_set(db, dataset['id'], 'level@1', 'level@1', [self.audio])

    def test_no_windows_and_changed_content_are_not_passes(self):
        with store.connect(self.database) as db:
            workspace.save_standard(db, self.standard())
            dataset = workspace.freeze(db, 'fixed', ['sample-1'])
            newer = config(version='2')
            newer['window'] = {'kind': 'sliding', 'length_s': 20, 'step_s': 20, 'include_partial': False}
            store.save_definition(db, newer)
            result = workspace.compare_set(db, dataset['id'], 'level@1', 'level@2', [self.audio])
            self.assertEqual(result['status'], 'incomplete')
        wav(self.audio, level=.2)
        with store.connect(self.database) as db:
            result = workspace.compare_set(db, dataset['id'], 'level@1', 'level@2', [self.audio])
            self.assertEqual(result['comparable'], 0)

    def test_interval_comparison_detects_bad_locations_and_leaves_unknown(self):
        samples = [self.standard(start_s=0, end_s=1), self.standard(id='n', start_s=3, end_s=4, verdict='normal')]
        findings = [{'label':'低电平','start_s':0,'end_s':.1}, {'label':'低电平','start_s':3,'end_s':4}, {'label':'其他','start_s':5,'end_s':6}]
        result = workspace.score_intervals(samples, findings)['counts']
        self.assertEqual(result, {'hits':0,'misses':1,'false_alarms':1,'boundary_errors':1,'unjudged':1,'normal_correct':0})

    def test_sliding_duplicate_findings_merge_before_comparison(self):
        findings = [{'label':'低电平','start_s':0,'end_s':5}, {'label':'低电平','start_s':3,'end_s':8}]
        result = workspace.score_intervals([self.standard()], findings)['counts']
        self.assertEqual(result['hits'], 1)
        self.assertEqual(result['boundary_errors'], 0)

    def test_maximum_manual_ids_import_and_keep_one_standard_revision_chain(self):
        with store.connect(self.database) as db:
            packet = {'schema_version':'1.0','library_id':store.library_id(db),'reviews':[], 'manual':[]}
            for size in (72, 73, 80):
                packet['manual'].append({'id':'m' * size,'batch_id':'first','recording_id':self.finding['recording_id'],
                    'config_hash':self.finding['config_hash'],'label':'低电平','start_s':0,'end_s':8,
                    'reviewer':'tester','comment':'确认漏检'})
            workspace.save_reviews(db, packet)
            initial = workspace.standards(db)
            self.assertEqual(len(initial), 3)
            self.assertEqual(len({row['id'] for row in initial}), 3)
            self.assertTrue(all(len(row['id']) <= 80 for row in initial))
            self.assertIn('finding-' + 'm' * 72, {row['id'] for row in initial})
            packet['manual'] = []
            packet['reviews'] = [{'finding_id':'m' * 80,'expected_revision':1,'status':'corrected',
                                 'reviewer':'tester','comment':'调整边界','end_s':7}]
            workspace.save_reviews(db, packet)
            updated = next(row for row in workspace.standards(db) if row['source'] == 'm' * 80)
            original = next(row for row in initial if row['source'] == 'm' * 80)
            self.assertEqual(updated['id'], original['id'])
            self.assertEqual(updated['revision'], 2)
            self.assertEqual(updated['end_s'], 7)

    def test_saved_summaries_backfill_once_and_selected_details_are_independent(self):
        with store.connect(self.database) as db:
            workspace.save_standard(db, self.standard())
            frozen = workspace.freeze(db, 'fixed', ['sample-1'])
            store.save_definition(db, config(version='2'))
            comparison = workspace.compare_set(db, frozen['id'], 'level@1', 'level@2', [self.audio])
            db.execute('DROP TABLE saved_summaries')  # The original PR library format.
        with store.connect(self.database) as db:
            workspace.initialize(db)
            self.assertEqual(workspace.saved_summaries(db,'sample_sets')[0]['samples'], 1)
            self.assertNotIn('details', workspace.saved_summaries(db,'rule_comparisons')[0])
            with patch.object(workspace.json, 'loads', side_effect=AssertionError('already migrated history was decoded')):
                workspace.initialize(db)
            self.assertEqual(workspace.saved_item(db,'sample_sets',frozen['id'])['hash'], frozen['hash'])
            self.assertEqual(workspace.saved_item(db,'rule_comparisons',comparison['id'])['details'], comparison['details'])
            db.execute('INSERT INTO rule_comparisons VALUES (?, ?, ?)', ('unrelated','later','invalid-json'))
            # Opening one comparison must not decode unrelated history payloads.
            self.assertEqual(workspace.saved_item(db,'rule_comparisons',comparison['id'])['details'], comparison['details'])

    def test_summary_failure_rolls_back_complete_history(self):
        with store.connect(self.database) as db:
            workspace.save_standard(db, self.standard())
        with self.assertRaises(ValueError), store.connect(self.database) as db:
            with patch.object(workspace, 'save_summary', side_effect=ValueError('summary write failed')):
                workspace.freeze(db, 'fixed', ['sample-1'])
        with store.connect(self.database) as db:
            self.assertEqual(workspace.saved_items(db,'sample_sets'), [])
            self.assertEqual(workspace.saved_summaries(db,'sample_sets'), [])


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.inputs = self.root / 'inputs'; self.inputs.mkdir()
        self.audio = wav(self.inputs / 'one.wav')
        self.database = self.root / 'library.sqlite3'; self.output = self.root / 'service'
        self.app = EditorApplication(self.database, [self.inputs], self.output)
        self.workbench = self.app.workbench
        self.app.save(config())

    def finish(self, job_id):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            job = next(item for item in self.workbench.state()['jobs'] if item['id'] == job_id)
            if job['status'] != 'running':
                self.assertEqual(job['status'], 'completed', job)
                return job['result']
            time.sleep(.02)
        self.fail('job timed out')

    def test_settings_restart_scan_incremental_and_new_rule(self):
        state = self.workbench.state()
        self.workbench.dispatch('/api/workspace/settings', {'name':'每天质检', 'definitions':['level@1'], 'expected_revision':state['settings']['revision']})
        restored = EditorApplication(self.database, [], self.output)
        self.assertEqual(restored.workbench.state()['settings']['definitions'], ['level@1'])
        self.finish(self.workbench.run({'definitions':['level@1'],'mode':'all'})['job_id'])
        self.assertTrue(self.workbench.run({'definitions':['level@1'],'mode':'new'})['skipped'])
        wav(self.inputs / 'two.wav', level=.1)
        second = self.finish(self.workbench.run({'definitions':['level@1'],'mode':'new'})['job_id'])
        self.assertEqual(second['summary']['files'], 1)
        self.app.save(config(version='2'))
        changed = self.finish(self.workbench.run({'definitions':['level@2'],'mode':'new'})['job_id'])
        self.assertEqual(changed['summary']['files'], 2)
        report = json.loads((self.output / changed['report_url'].lstrip('/')).with_name('results.json').read_text())
        self.assertEqual(len(report['batches']), 1)

    def test_trial_selection_and_scope_restrictions(self):
        wav(self.inputs / 'two.wav', level=.1)
        self.workbench.scan()
        selected = self.workbench.state()['inputs'][1]['id']
        result = self.finish(self.workbench.run({'definitions':['level@1'],'mode':'trial','selected':[selected]})['job_id'])
        self.assertEqual(result['summary']['files'], 1)
        with self.assertRaises(ValueError):
            self.workbench.run({'definitions':['level@1'],'mode':'trial','selected':['../../outside']})
        with self.assertRaises(ValueError):
            self.workbench.run({'definitions':['level@1'],'mode':'trial','limit':0})
        outside = wav(self.root / 'outside.wav')
        (self.inputs / 'escape.wav').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, '授权'):
            self.workbench.scan()

    def test_settings_conflict_and_duplicate_versions_are_rejected(self):
        value = {'name':'daily','definitions':['level@1'],'expected_revision':1}
        self.workbench.dispatch('/api/workspace/settings', value)
        with self.assertRaisesRegex(ValueError, '变化'):
            self.workbench.dispatch('/api/workspace/settings', value)
        self.app.save(config(version='2'))
        with self.assertRaises(ValueError):
            self.workbench.run({'definitions':['level@1','level@2'],'mode':'all'})

    def test_review_standards_conflict_rolls_back_the_review(self):
        self.finish(self.workbench.run({'definitions':['level@1'],'mode':'all'})['job_id'])
        with store.connect(self.database) as db:
            finding = store.query(db)[0]
            workspace.save_standard(db, {'id':'human-normal','expected_revision':0,'recording_id':finding['recording_id'],
                'label':'低电平','start_s':0,'end_s':8,'verdict':'normal','reviewer':'a','comment':'试听确认','checked':True})
            packet = {'schema_version':'1.0','library_id':store.library_id(db),'reviews':[{
                'finding_id':finding['id'],'expected_revision':0,'status':'confirmed','reviewer':'b','comment':'有问题'}],'manual':[]}
        with self.assertRaisesRegex(ValueError,'冲突'):
            self.workbench.dispatch('/api/workspace/reviews', packet)
        with store.connect(self.database) as db:
            self.assertEqual(store.current_finding(db,finding['id'])['revision'],0)

    def test_refresh_and_completed_jobs_return_summaries_while_details_stay_available(self):
        result = self.finish(self.workbench.run({'definitions':['level@1'],'mode':'all'})['job_id'])
        with store.connect(self.database) as db:
            finding = store.query(db)[0]
            packet = {'schema_version':'1.0','library_id':store.library_id(db),'manual':[], 'reviews':[{
                'finding_id':finding['id'],'expected_revision':0,'status':'confirmed','reviewer':'test','comment':'已试听'}]}
            workspace.save_reviews(db, packet)
            frozen = workspace.freeze(db, 'fixed', [item['id'] for item in workspace.standards(db)])
        self.app.save(config(version='2'))
        result = self.finish(self.workbench.dispatch('/api/workspace/compare',
            {'set_id':frozen['id'],'before':'level@1','after':'level@2'})['job_id'])
        self.assertNotIn('details', result)
        with patch.object(workspace, 'saved_items', side_effect=AssertionError('full history loaded')):
            state = self.workbench.state()
        self.assertEqual(state['sets'][0]['samples'], 1)
        self.assertNotIn('details', state['comparisons'][0])
        self.assertNotIn('details', state['jobs'][-1]['result'])
        with store.connect(self.database) as db:
            detail = workspace.saved_item(db, 'rule_comparisons', state['comparisons'][0]['id'])
        self.assertEqual(len(detail['details']), 1)
