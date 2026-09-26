from pathlib import Path
import tempfile
import unittest

from voice_tools.tools.detect import service, store
from voice_tools.tools.detect.definition import fingerprint
from .fixtures import config, wav


class Library(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root/'library.sqlite3'
        self.path = wav(self.root/'sample.wav')
        with store.connect(self.db,create=True) as db:
            self.first = service.run(db,[self.path],[config()], 'old')
            self.row = store.query(db)[0]
            self.library_id=store.library_id(db)

    def decision(self, row=None, status='confirmed', **kwargs):
        row=row or self.row
        return dict(finding_id=row['id'],expected_revision=row['revision'],status=status,reviewer='tester',**kwargs)

    def test_immutable_config_and_batch(self):
        with self.assertRaisesRegex(ValueError,'新版本'), store.connect(self.db) as db:
            store.save_definition(db,config(threshold=-50))
        with self.assertRaisesRegex(ValueError,'新批次'), store.connect(self.db) as db:
            service.run(db,[self.path],[config()], 'old')
        with store.connect(self.db) as db:
            self.assertEqual(len(store.definitions(db)),1)
            self.assertEqual(len(store.batch_details(db)),1)

    def test_rerun_keeps_human_history_separate(self):
        with store.connect(self.db) as db:
            store.review(db,self.decision())
            service.run(db,[self.path],[config()], 'repeat')
            old=store.query(db,batch='old')[0]; new=store.query(db,batch='repeat')[0]
            self.assertEqual((old['status'],old['revision']),('confirmed',1))
            self.assertEqual((new['status'],new['revision']),('pending',0))
            self.assertNotEqual(old['id'],new['id'])
            self.assertEqual(len(store.history(db,old['id'])),1)

    def test_query_corrected_labels_multitag_sources_and_export_scope(self):
        second=config(label='另外标签'); second['id']='second'
        with store.connect(self.db) as db:
            service.run(db,[self.path],[config(),second], 'two')
            self.assertEqual(len(store.query(db,tags=['低电平','另外标签'],tag_mode='all')),2)
            corrected=store.review(db,self.decision(status='corrected',label='真实问题',start_s=1,end_s=2))
            self.assertEqual(corrected['automatic']['label'],'低电平')
            self.assertEqual(len(store.query(db,tags=['真实问题'],status='corrected',recording='SAMPLE.WAV',batch='old',version='1')),1)
            self.assertEqual(len(store.query(db,tags=['不存在'])),0)

    def test_duplicate_content_and_relocated_sources(self):
        moved=self.root/'copy.wav'; moved.write_bytes(self.path.read_bytes())
        with store.connect(self.db) as db:
            result=service.run(db,[moved,self.path],[config()], 'duplicates')
            self.assertEqual(result['recordings'],1)
            self.assertEqual(len(store.query(db,batch='duplicates')),1)
            self.assertEqual(len(store.query(db,batch='duplicates')[0]['sources']),2)

    def test_partial_errors_and_zero_hits_are_retained(self):
        broken=self.root/'broken.wav'; broken.write_text('bad wav')
        mono=wav(self.root/'mono.wav',channels=1)
        with store.connect(self.db) as db:
            result=service.run(db,[self.path,broken,mono],[config(threshold=-100,version='2')], 'partial')
            self.assertEqual(result['errors'],2)
            self.assertEqual(result['status'],'partial')
            rows=store.evaluation_rows(db,'partial')
            self.assertEqual({row['status'] for row in rows},{'ok','error'})
            self.assertEqual(len(store.query(db,batch='partial')),0)
            self.assertEqual(len(store.batch_details(db)[0]['errors']),1)
            self.assertEqual(len(store.batch_details(db)[0]['evaluation_issues']),1)
            self.assertEqual(store.batch_details(db)[0]['engine_version'],'detection-1')

    def test_atomic_import_conflict_and_wrong_library(self):
        packet={'schema_version':'1.0','library_id':self.library_id,'reviews':[self.decision(),self.decision()], 'manual':[]}
        with self.assertRaisesRegex(ValueError,'冲突'), store.connect(self.db) as db:
            store.import_reviews(db,packet)
        with store.connect(self.db) as db:
            self.assertEqual(store.current_finding(db,self.row['id'])['revision'],0)
        packet['library_id']='wrong'
        with self.assertRaisesRegex(ValueError,'身份'), store.connect(self.db) as db:
            store.import_reviews(db,packet)

    def test_review_bounds_unknown_status_and_correction_state(self):
        for extra in ({'status':'unknown'},{'status':'corrected','start_s':-1},
                      {'status':'corrected','end_s':9},{'status':'confirmed','label':'changed'},
                      {'status':'corrected','end_s':float('nan')}):
            item=self.decision(); item.update(extra)
            with self.subTest(extra=extra), self.assertRaises(ValueError), store.connect(self.db) as db:
                store.review(db,item)

    def test_history_append_and_stale_review(self):
        with store.connect(self.db) as db:
            one=store.review(db,self.decision())
            store.review(db,self.decision(one,status='false_positive',comment='听后修正'))
            self.assertEqual([row['revision'] for row in store.history(db,self.row['id'])],[1,2])
        with self.assertRaisesRegex(ValueError,'冲突'), store.connect(self.db) as db:
            store.review(db,self.decision())

    def manual(self, **changes):
        item={'id':'manual-1','batch_id':'old','recording_id':self.row['recording_id'],'config_hash':self.row['config_hash'],
              'label':'漏检问题','start_s':2,'end_s':4,'reviewer':'tester','comment':'人工试听补充'}
        item.update(changes)
        return item

    def test_manual_miss_separate_from_automatic_results(self):
        with store.connect(self.db) as db:
            row=store.add_manual(db,self.manual())
            self.assertEqual(row['origin'],'manual')
            self.assertEqual(row['status'],'confirmed')
            self.assertEqual(len(store.query(db)),2)
        with self.assertRaises(ValueError), store.connect(self.db) as db:
            store.add_manual(db,self.manual())
        with self.assertRaises(ValueError), store.connect(self.db) as db:
            store.add_manual(db,self.manual(id='manual-2',config_hash='missing'))

    def test_compare_resolved_false_positive_and_introduced_miss(self):
        with store.connect(self.db) as db:
            store.review(db,self.decision(status='false_positive'))
            service.run(db,[self.path],[config(threshold=-100,version='2')], 'new')
            result=service.compare(db,'old','new','level')
            self.assertEqual(len(result['resolved_false_positives']),1)
            self.assertEqual(result['introduced_misses'],[])
            updated=store.current_finding(db,self.row['id'])
            store.review(db,self.decision(updated,status='confirmed'))
            result=service.compare(db,'old','new','level')
            self.assertEqual(len(result['introduced_misses']),1)
            self.assertEqual(result['resolved_false_positives'],[])

    def test_compare_recovered_manual_miss_and_conflicts(self):
        with store.connect(self.db) as db:
            service.run(db,[self.path],[config(threshold=-100,version='2')], 'negative')
            cfg_hash=fingerprint(config(threshold=-100,version='2'))
            store.add_manual(db,self.manual(batch_id='negative',config_hash=cfg_hash,label='低电平'))
            result=service.compare(db,'negative','old','level')
            self.assertEqual(len(result['recovered_misses']),1)
            store.review(db,self.decision(status='false_positive'))
            result=service.compare(db,'negative','old','level')
            self.assertEqual(result['recovered_misses'],[])
            self.assertEqual(len(result['human_evidence_conflicts']),1)

    def test_compare_never_treats_missing_windows_as_clean(self):
        value=config(version='2'); value['scope']['start_s']=100
        with store.connect(self.db) as db:
            service.run(db,[self.path],[value], 'unknown')
            result=service.compare(db,'old','unknown','level')
            self.assertEqual(result['removed'],[])
            self.assertEqual(result['comparable_recordings'],0)
            self.assertEqual(len(result['not_comparable']),1)
