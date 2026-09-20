import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import wave
import zipfile

from voice_tools.core.files import write_json, read_json, sha256
from voice_tools.tools.task import bundle, runner, review
from voice_tools.tools.task.contract import validate


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'author'; self.source.mkdir()
        (self.source / 'assets').mkdir()
        with wave.open(str(self.source / 'assets/call.wav'), 'wb') as w:
            w.setparams((1,2,8000,0,'NONE','not compressed')); w.writeframes(b'\0\0'*8000)
        self.task = {'schema_version':'1.0', 'id':'review-call', 'title':'跨目录回环',
                     'inputs':{'audio':'assets/call.wav'}, 'steps':[{'id':'inspect','tool':'audio','action':'inspect','params':{'inputs':[{'input':'audio'}]}}]}

    def pack(self):
        write_json(self.source/'task.json', self.task)
        target=self.root/'task.vtask.zip'
        bundle.pack(self.source/'task.json',self.source,target)
        return target

    def test_roundtrip_without_source_directory(self):
        original=sha256(self.source/'assets/call.wav')
        package=self.pack(); shutil.rmtree(self.source)
        self.assertTrue(runner.check(package)['ready'])
        result=runner.run(package,self.root/'executor'/'run')
        self.assertEqual(result['status'],'completed')
        archive=self.root/'result.vresult.zip'
        runner.collect(self.root/'executor'/'run',archive)
        shutil.rmtree(self.root/'executor')
        page=review.review(archive,self.root/'review')
        self.assertTrue(Path(page['index']).is_file())
        self.assertEqual(sha256(self.root/'review/evidence/original/inputs/assets/call.wav'),original)
        with zipfile.ZipFile(archive) as z: self.assertNotIn('executor-summary.json',z.namelist())

    def test_transitive_sip_absolute_media_relocation(self):
        scenario={'schema_version':'1.0','target_uri':'sip:test@127.0.0.1','steps':[{'action':'play','file':str(self.source/'assets/call.wav')},{'action':'hangup'}]}
        write_json(self.source/'scenario.json',scenario)
        self.task['inputs']={'scenario':'scenario.json'}
        self.task['steps']=[{'id':'validate','tool':'sip','action':'validate','params':{'scenario':{'input':'scenario'}}}]
        package=self.pack(); shutil.rmtree(self.source)
        with zipfile.ZipFile(package) as archive:
            mapping=json.loads(archive.read('input-map.json'))
        self.assertIn(scenario['steps'][0]['file'],mapping['sources'])
        self.assertTrue(runner.check(package)['ready'])
        self.assertEqual(runner.run(package,self.root/'run')['status'],'completed')
        original=read_json(self.root/'run/original/inputs/scenario.json')
        working=read_json(self.root/'run/work/inputs/scenario.json')
        self.assertEqual(original,scenario); self.assertNotEqual(original,working)

    def test_missing_dependency_blocks_pack(self):
        write_json(self.source/'scenario.json',{'steps':[{'action':'play','file':'missing.wav'}]})
        self.task['inputs']={'scenario':'scenario.json'}
        self.task['steps'][0]['params']['inputs']=[{'input':'scenario'}]
        with self.assertRaisesRegex(ValueError,'缺少'): self.pack()

    def test_unsafe_inputs_and_secret_rejected(self):
        (self.source/'assets/link').symlink_to('/etc/hosts')
        self.task['inputs']={'audio':'assets'}
        with self.assertRaisesRegex(ValueError,'链接'): self.pack()
        (self.source/'assets/link').unlink()
        write_json(self.source/'assets/secret.json',{'password':'not-a-real-secret'})
        with self.assertRaisesRegex(ValueError,'秘密'): self.pack()

    def test_task_reference_and_types(self):
        self.task['steps'][0]['params']['inputs']=[{'step':'future'}]
        with self.assertRaisesRegex(ValueError,'前序'): validate(self.task)
        self.task['steps'][0]['params']={'inputs':[{'input':'audio'}], 'out':'bad'}
        with self.assertRaisesRegex(ValueError,'执行端'): validate(self.task)

    def test_manifest_tamper_and_traversal(self):
        package=self.pack()
        tamper=self.root/'tamper.zip'
        with zipfile.ZipFile(package) as z, zipfile.ZipFile(tamper,'w') as dest:
            for name in z.namelist(): dest.writestr(name,b'bad' if name.endswith('.wav') else z.read(name))
        with self.assertRaisesRegex(ValueError,'大小|摘要'): bundle.unpack(tamper,self.root/'bad')
        self.assertFalse((self.root/'bad').exists())
        with zipfile.ZipFile(self.root/'escape.zip','w') as z:
            z.writestr('../escape','bad');z.writestr('manifest.json',json.dumps({'schema_version':'1.0','kind':'voice_task','tool_version':'0.12.1','files':[{'path':'../escape','bytes':3,'sha256':'0'*64}]}))
        with self.assertRaises(ValueError): bundle.unpack(self.root/'escape.zip',self.root/'bad2')

    def test_mapping_cannot_escape_even_with_valid_checksums(self):
        package=self.pack(); unpacked=self.root/'unpacked'; bundle.unpack(package,unpacked)
        write_json(unpacked/'input-map.json',{'inputs':{'audio':'/etc/hosts'},'sources':{}})
        malicious=self.root/'mapping.zip'
        bundle.create_archive(malicious,{str(p.relative_to(unpacked)):p for p in unpacked.rglob('*') if p.is_file() and p.name!='manifest.json'},'voice_task')
        with self.assertRaisesRegex(ValueError,'映射'): runner.check(malicious)

    def test_network_preflight_does_not_execute(self):
        self.task['inputs']={}
        self.task['steps']=[{'id':'homer','tool':'homer','action':'search','params':{}}]
        package=self.pack()
        result=runner.check(package)
        self.assertFalse(result['ready']);self.assertFalse(result['network_accessed'])

    def test_failure_skips_dependent_but_runs_independent(self):
        self.task['steps'] += [{'id':'dependent','tool':'audio','action':'inspect','depends_on':['inspect'],'params':{'inputs':[{'input':'audio'}]}},
                               {'id':'independent','tool':'audio','action':'inspect','params':{'inputs':[{'input':'audio'}]}}]
        package=self.pack()
        with patch('voice_tools.tools.task.runner.classify',side_effect=['failed','completed']):
            result=runner.run(package,self.root/'run')
        self.assertEqual([s['status'] for s in result['steps']],['failed','skipped','completed'])
        self.assertEqual(result['status'],'failed')
        self.assertEqual(runner.collect(self.root/'run',self.root/'partial.zip')['kind'],'voice_result')

    def test_status_semantics_and_redaction(self):
        self.assertEqual(runner.classify('qa','analyze',1,{}),'findings')
        self.assertEqual(runner.classify('nisqa','analyze',1,{}),'insufficient_evidence')
        self.assertEqual(runner.classify('capture','ring-start',0,{}),'remote_running')
        (self.root/'stderr.log').write_text('secret=EXAMPLE_VALUE')
        with patch.dict(os.environ,VT_TEST_SECRET='EXAMPLE_VALUE'):
            runner.redact_logs(self.root,{'secret_env':['VT_TEST_SECRET']})
        self.assertNotIn('EXAMPLE_VALUE',(self.root/'stderr.log').read_text())

    def test_refusing_collect_active_run(self):
        write_json(self.root/'run.json',{'kind':'task_run','status':'running'})
        with self.assertRaisesRegex(ValueError,'尚未结束'): runner.collect(self.root,self.root/'result.zip')

    def test_grouped_pcap_references(self):
        task={**self.task,'steps':[{'id':'report','tool':'report','action':'build','params':{'pcap_group':[['sensor',{'input':'audio'}]]}}]}
        validate(task)
        value=runner.resolve(task['steps'][0]['params']['pcap_group'],self.source,{'inputs':{'audio':'assets/call.wav'}},{})
        self.assertEqual(value,[['sensor',str(self.source/'assets/call.wav')]])

    def test_timeout_and_cancel_preserve_receipt(self):
        import signal
        import subprocess
        import sys
        import threading
        self.task['steps'][0]['timeout_s']=1
        package=self.pack()
        original=subprocess.Popen
        children=[]
        def slow(*args, **kwargs):
            child=original([sys.executable,'-c','import time; time.sleep(60)'],**kwargs)
            children.append(child); return child
        with patch('voice_tools.tools.task.runner.subprocess.Popen',side_effect=slow):
            # check uses no process for audio; catalog was cached during pack.
            result=runner.run(package,self.root/'timeout')
        self.assertEqual(result['steps'][0]['status'],'interrupted')
        self.assertIsNotNone(children[0].poll())
        self.assertIn('时限',result['steps'][0]['reason'])
        timer=threading.Timer(.3,lambda:os.kill(os.getpid(),signal.SIGTERM))
        with patch('voice_tools.tools.task.runner.subprocess.Popen',side_effect=slow):
            timer.start()
            try: result=runner.run(package,self.root/'cancel')
            finally: timer.cancel()
        self.assertEqual(result['status'],'interrupted')
        self.assertIsNotNone(children[-1].poll())
        runner.collect(self.root/'cancel',self.root/'cancel.zip')

    def test_invalid_zip_returns_input_error(self):
        path=self.root/'invalid.zip';path.write_bytes(b'not a zip')
        with self.assertRaisesRegex(ValueError,'压缩包'): runner.check(path)

    def test_visqol_csv_collects_both_pair_files(self):
        import csv
        shutil.copyfile(self.source/'assets/call.wav',self.source/'assets/reference.wav')
        with (self.source/'pairs.csv').open('w') as stream:
            writer=csv.writer(stream);writer.writerow(['reference','degraded']);writer.writerow(['assets/reference.wav',str(self.source/'assets/call.wav')])
        self.task['inputs']={'pairs':'pairs.csv'}
        self.task['steps']=[{'id':'score','tool':'visqol','action':'batch','params':{'pairs':{'input':'pairs'}}}]
        package=self.pack();shutil.rmtree(self.source)
        root=self.root/'copied';bundle.unpack(package,root)
        mapping=bundle.task_mapping(root,self.task);bundle.relocate_inputs(root,mapping)
        with (root/'inputs/pairs.csv').open() as stream:row=next(csv.DictReader(stream))
        self.assertTrue((root/'inputs'/row['reference']).is_file())
        self.assertTrue(Path(row['degraded']).is_file())

    def test_renamed_session_database_preserves_hash_and_relocates_source(self):
        import sqlite3
        source=self.source/'assets/call.wav';digest=sha256(source)
        with sqlite3.connect(self.source/'renamed.db') as db:
            db.execute('CREATE TABLE sources(path TEXT,sha256 TEXT)')
            db.execute('INSERT INTO sources VALUES(?,?)',(str(source),digest))
        self.task['inputs']={'audio':'renamed.db'}
        package=self.pack();shutil.rmtree(self.source)
        root=self.root/'copied';bundle.unpack(package,root)
        bundle.relocate_inputs(root,bundle.task_mapping(root,self.task))
        with sqlite3.connect(root/'inputs/renamed.db') as db: path,original=db.execute('SELECT path,sha256 FROM sources').fetchone()
        self.assertEqual(sha256(path),original);self.assertEqual(original,digest)

    def test_models_and_existing_output_are_not_overwritten(self):
        (self.source/'weights.pt').write_bytes(b'not a model')
        self.task['inputs']={'audio':'weights.pt'}
        with self.assertRaisesRegex(ValueError,'模型'):self.pack()
        self.task['inputs']={'audio':'assets/call.wav'}
        package=self.pack();digest=sha256(package)
        with self.assertRaisesRegex(ValueError,'存在'):self.pack()
        self.assertEqual(sha256(package),digest)

    def test_executor_override_is_checked_before_execution(self):
        package=self.pack()
        config=self.root/'executor.json'
        write_json(config,{'schema_version':'1.0','environments':{'default':{'params':{'audio':{'threshold_db':'not-a-number'}}}}})
        result=runner.check(package,config)
        self.assertFalse(result['ready'])
        self.assertTrue(any('数值' in issue for issue in result['issues']))
