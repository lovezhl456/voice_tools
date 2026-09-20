import json
import re
from pathlib import Path
import tempfile
import unittest

from voice_tools.audio.io import write_wav
from voice_tools.core.files import read_json, write_json, sha256
from voice_tools.core.rtp_timeline import TimelineWriter
from voice_tools.core.output_events import rebind_prepared
from voice_tools.tools.task import bundle, runner, review
from voice_tools.tools.gaps.service import analyze_batch
from voice_tools.tools.recording_qa.batch import analyze_batch as qa_batch
from voice_tools.tools.recording_qa.dataset import freeze
from tests.gaps.test_detection import signal, metadata, expected, HASH
from tests.gaps.test_evidence import STREAM


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.source=self.root/'source';self.source.mkdir()
        self.audio=self.source/'call.wav';audio=signal();write_wav(self.audio,audio.samples,audio.sample_rate)

    def task(self, evidence=False):
        value={'schema_version':'1.0','id':'gap-test','title':'间隙跨机验证','inputs':{'audio':'call.wav'},
            'steps':[{'id':'detect','tool':'gaps','action':'analyze','params':{'inputs':[{'input':'audio'}],'include_audio':True,'fail_on_findings':True}}]}
        if evidence:
            value['inputs']['evidence']='evidence.json';value['steps'][0]['params']['evidence']={'input':'evidence'}
        path=self.source/'task.json';write_json(path,value);return path

    def test_task_execution_and_trusted_review(self):
        package=self.root/'task.zip';bundle.pack(self.task(),self.source,package)
        result=runner.run(package,self.root/'run')
        self.assertEqual(result['steps'][0]['status'],'findings')
        results=self.root/'result.zip';runner.collect(self.root/'run',results)
        review.review(results,self.root/'review')
        page=self.root/'review/gap-review/detect/review.html'
        self.assertTrue(page.exists())
        self.assertIn('输出间隙',page.read_text())
        self.assertIn('gaps_review',(self.root/'review/index.html').read_text())
        data = json.loads(re.search(r'<script id="reviewData" type="application/json">(.*?)</script>', page.read_text(), re.S)[1])
        for record in data['records']:
            for channel in ('both', 'left', 'right'):
                self.assertTrue((page.parent / record['playback_sources'][channel]).is_file())
        self.assertIn('href="../../index.html"', page.read_text())

    def test_event_source_paths_do_not_rewrite_evidence(self):
        events = metadata([expected()])
        events['output_events'].update(audio_sha256=sha256(self.audio), source=str(self.audio))
        event_path = self.audio.with_suffix('.events.json')
        write_json(event_path, events)
        original_hash = sha256(event_path)
        package = self.root / 'task.zip'
        bundle.pack(self.task(), self.source, package)
        runner.run(package, self.root / 'run')
        migrated = self.root / 'run/work/inputs/call.events.json'
        self.assertEqual(sha256(migrated), original_hash)

    def test_evidence_protected_from_path_rewriting(self):
        nisqa=self.source/'scores.jsonl';nisqa.write_text(json.dumps({'file':str(self.audio),'scores':{'mos':3}})+'\n')
        provenance={'schema_version':'1.0','kind':'nisqa_provenance','results':'scores.jsonl','results_sha256':sha256(nisqa),'sources':[{'file':str(self.audio),'sha256':sha256(self.audio),'unchanged':True}]}
        write_json(self.source/'provenance.json',provenance)
        evidence={'schema_version':'1.0','kind':'gap_evidence','recordings':[{'audio_sha256':sha256(self.audio),'nisqa':[{'results':'scores.jsonl','provenance':'provenance.json','source_file':str(self.audio),'channel':'right'}]}]}
        write_json(self.source/'evidence.json',evidence)
        task=self.task(True);package=self.root/'task.zip';bundle.pack(task,self.source,package)
        target=self.root/'elsewhere';bundle.unpack(package,target,'voice_task')
        mapping=bundle.task_mapping(target,read_json(target/'task.json'));bundle.relocate_inputs(target,mapping)
        self.assertEqual(sha256(nisqa),sha256(target/'inputs/scores.jsonl'))
        self.assertEqual((self.source/'evidence.json').read_bytes(),(target/'inputs/evidence.json').read_bytes())

    def test_large_timeline_shards_fit_bundle(self):
        writer=TimelineWriter(self.source/'timeline')
        row={**STREAM,'epoch':100.0,'seq':0,'timestamp':0,'pt':0}
        for i in range(130000):
            row.update(epoch=100+i*.02,seq=i%65536,timestamp=i*160);writer.write(row)
        value=writer.finish({'first_epoch':100,'last_epoch':2700,'packet_limit_reached':False,'truncated_packets':0,'out_of_order_capture_timestamps':0})
        value['sensor']='edge';write_json(self.source/'timeline/timeline.json',value)
        self.assertGreater(sum(c['bytes'] for c in value['chunks']),16*1024*1024)
        self.assertTrue(all(c['bytes']<=8*1024*1024 for c in value['chunks']))
        write_json(self.source/'evidence.json',{'kind':'gap_evidence','schema_version':'1.0','recordings':[{'audio_sha256':sha256(self.audio),'rtp':[{'timeline':'timeline/timeline.json'}]}]})
        bundle.pack(self.task(True),self.source,self.root/'large.zip')
        self.assertTrue((self.root/'large.zip').exists())

    def test_freeze_and_verified_prepare_preserve_extension(self):
        events=metadata([expected(5)]);events['output_events']['audio_sha256']=sha256(self.audio)
        write_json(self.audio.with_suffix('.events.json'),events)
        qa_batch([self.audio],self.root/'qa')
        frozen=self.root/'frozen';freeze(self.root/'qa/results.jsonl',frozen)
        output=next(frozen.glob('*.events.json'))
        self.assertEqual(read_json(output)['output_events'],events['output_events'])
        rebound=rebind_prepared(events,sha256(self.audio),'b'*64,sha256(self.audio.with_suffix('.events.json')))
        self.assertEqual(rebound['output_events']['audio_sha256'],'b'*64)
        self.assertEqual(events['output_events']['audio_sha256'],sha256(self.audio))
