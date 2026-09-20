import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from voice_tools.core.files import read_json, write_json
from voice_tools.tools.latency.runtime import doctor
from voice_tools.tools.latency.service import inspect_wav, process
from voice_tools.tools.task import bundle, runner, review

spec=importlib.util.spec_from_file_location('latency_demo',Path(__file__).resolve().parents[2]/'scripts/latency_demo.py')
demo=importlib.util.module_from_spec(spec);spec.loader.exec_module(demo)
ENGINE=os.environ.get('VOICE_TOOLS_TEST_LATENCY_DIR')


@unittest.skipUnless(ENGINE, 'set VOICE_TOOLS_TEST_LATENCY_DIR to test real isolated engine')
class RealEngineTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.counter=0

    def analyze(self, names, **kwargs):
        self.counter+=1;out=self.root/f'out-{self.counter}'
        result=process(names,out,system_channel='right',directory=ENGINE,**kwargs)
        rows=[json.loads(s) for s in (out/'files.jsonl').read_text().splitlines()]
        return result,rows,out

    def test_rates_and_no_drift(self):
        for rate in (8000,16000,24000,32000,44100,48000):
            with self.subTest(rate=rate):
                path=self.root/f'{rate}.wav'
                demo.recording(path,rate,human=((1,2),(60,61)),ai=((2.5,3.5),(61.5,62.5)),duration=66)
                result,rows,out=self.analyze([path])
                self.assertEqual(result['errors'],0,rows)
                pairs=read_json(out/rows[0]['detail'])['measurement']['pairs']
                forward=[p for p in pairs if p['direction']=='human_to_ai']
                self.assertEqual(len(forward),2)
                for pair in forward:self.assertLessEqual(abs(pair['latency_s']-.5),.02)
                self.assertLessEqual(abs(forward[-1]['response_start_s']-61.5),.02)
        bad=self.root/'unsupported.wav';demo.recording(bad,22050)
        with self.assertRaises(ValueError):inspect_wav(bad)
        import struct
        for rate, frames, reason in ((48000, 48000 * 3600, '512 MiB'), (8000, 8000 * 3601, '3600')):
            header = struct.pack('<4sI4s4sIHHIIHH4sI', b'RIFF', frames*4+36, b'WAVE', b'fmt ', 16,
                                 1, 2, rate, rate*4, 4, 16, b'data', frames*4)
            bad.write_bytes(header)
            with self.assertRaisesRegex(ValueError, reason): inspect_wav(bad)


    def test_short_long_silent_overlap_and_no_response(self):
        cases={'short':{'ai':((3,3.2),)},'long':{'ai':((14,15),),'duration':18},
               'silent':{'human':(),'ai':()},'duplicate':{'duplicate':True},'none':{'ai':()},
               'overlap':{'human':((1,4),),'ai':((2,5),),'duration':8}}
        for name,args in cases.items():
            path=self.root/f'{name}.wav';demo.recording(path,**args)
            result,rows,out=self.analyze([path]);detail=read_json(out/rows[0]['detail'])
            self.assertEqual(result['errors'],0,rows)
            if name=='long':self.assertEqual(result['statistics']['human_to_ai']['max_s'],12)
            else:
                self.assertEqual(rows[0]['measurement_status'],'insufficient_evidence')
                self.assertIsNone(result['statistics']['human_to_ai']['median_s'])
            if name=='silent':self.assertIsNone(result['coverage']['ratio'])
            if name=='short':self.assertIn('short_filtered',[s['incoming'] for s in detail['measurement']['segments']])
            if name=='overlap':self.assertIn('overlap',result['coverage']['dispositions'])

    def test_partial_failure_duplicate_and_identity(self):
        a=self.root/'a.wav';b=self.root/'b.wav';demo.recording(a);shutil.copyfile(a,b)
        result,rows,out=self.analyze([b,a,a,self.root/'missing.wav'])
        self.assertEqual(result['files'],3);self.assertEqual(result['errors'],1)
        self.assertEqual(rows[0]['recording_id'],rows[1]['recording_id'])
        self.assertEqual(rows[1]['duplicate_of_file_index'],1)
        self.assertEqual(result['statistics']['human_to_ai']['count'],2)
        self.assertFalse(any(out.rglob('audio.wav')))
        swapped = process([a], self.root/'left-role', system_channel='left', directory=ENGINE)
        self.assertEqual(swapped['statistics']['ai_to_human']['median_s'], .5)
        self.assertIsNone(swapped['statistics']['human_to_ai']['median_s'])
        with self.assertRaises(ValueError):process([a],out,system_channel='right',directory=ENGINE)

    def test_forward_superseded_and_reverse_long_delay(self):
        path=self.root/'turns.wav';demo.recording(path,human=((1,2),(5,6),(45,46)),ai=((7,8),),duration=50)
        result,rows,out=self.analyze([path])
        self.assertEqual(result['coverage']['dispositions']['superseded'],1)
        self.assertEqual(result['coverage']['eligible_human_segments'],3)
        self.assertEqual(result['statistics']['ai_to_human']['max_s'],37)

    def test_pack_remove_inputs_run_collect_review(self):
        source=self.root/'source';source.mkdir();demo.recording(source/'normal.wav')
        task={'schema_version':'1.0','id':'latency-move','title':'迁移','inputs':{'wav':'normal.wav'},'steps':[{'id':'measure','tool':'latency','action':'analyze','params':{'wav':{'input':'wav'},'system_channel':'right'}}]}
        write_json(source/'task.json',task);package=self.root/'task.zip';bundle.pack(source/'task.json',source,package)
        shutil.rmtree(source)
        config=self.root/'executor.json';write_json(config,{'schema_version':'1.0','network_allowed':False,'environments':{},'latency_dir':ENGINE})
        self.assertTrue(runner.check(package,config)['ready'])
        execution=self.root/'run';result=runner.run(package,execution,config)
        self.assertEqual(result['status'],'completed',result)
        archive=self.root/'result.zip';runner.collect(execution,archive)
        review.review(archive,self.root/'review')
        html=(self.root/'review/index.html').read_text()
        self.assertIn('"latency": [{',html)
        self.assertIn('"latency_summary": {',html)
        self.assertIn('"playback": "evidence/work/inputs/',html)
        self.assertTrue((self.root/'review/latency.js').is_file())
