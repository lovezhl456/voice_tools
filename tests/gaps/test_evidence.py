import copy
import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from voice_tools.audio.io import write_wav
from voice_tools.core.files import read_json, write_json, sha256
from voice_tools.core.rtp_timeline import TimelineWriter
from voice_tools.tools.gaps.detector import analyze
from voice_tools.tools.gaps.evidence import associate, packet_metrics
from voice_tools.tools.gaps.review import check, label_row, FIELDS
from voice_tools.tools.gaps.service import analyze_batch
from tests.gaps.test_detection import signal, HASH

STREAM = {'src':'192.0.2.1','src_port':16000,'dst':'192.0.2.2','dst_port':24000,'ssrc':'0x1234'}


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.record={'tool':'gaps','schema_version':'1.0','input':'original.wav','audio_sha256':HASH,'result':analyze(signal(),HASH)}

    def manifest(self, **binding):
        path=self.root/'evidence.json'
        write_json(path,{'kind':'gap_evidence','schema_version':'1.0','recordings':[{'audio_sha256':HASH,**binding}]})
        return path

    def timeline(self, count=350, size=4000):
        writer=TimelineWriter(self.root/'timeline',size)
        for i in range(count):
            writer.write({**STREAM,'epoch':100+i*.02,'seq':i%65536,'timestamp':(i*160)%2**32,'pt':0})
        value=writer.finish({'first_epoch':100,'last_epoch':100+(count-1)*.02,'packet_limit_reached':False,'truncated_packets':0,'out_of_order_capture_timestamps':0})
        value['sensor']='edge';write_json(self.root/'timeline/timeline.json',value)
        return {'timeline':'timeline/timeline.json','stream':STREAM,'sensor':'edge','recording_start_epoch':100,'alignment_verified':True}

    def test_chunked_rtp_mapping_and_missing_mapping(self):
        binding=self.timeline()
        result=associate(self.manifest(rtp=[binding]),self.record)['sources'][0]
        self.assertEqual(result['status'],'aligned')
        values=next(iter(result['intervals'].values()))
        self.assertGreater(values['observed_packets'],90)
        self.assertEqual(values['coverage'],'observed_window')
        binding['alignment_verified']=False
        self.assertEqual(associate(self.manifest(rtp=[binding]),self.record)['sources'][0]['status'],'unaligned')

    def test_tampered_and_missing_shard_are_errors(self):
        binding=self.timeline();manifest=self.manifest(rtp=[binding]);part=next((self.root/'timeline').glob('packets-*'))
        part.write_text(part.read_text()+'{}\n')
        self.assertEqual(associate(manifest,self.record)['status'],'error')
        part.unlink()
        self.assertEqual(associate(manifest,self.record)['status'],'error')

    def test_incomplete_and_different_sensor(self):
        binding=self.timeline();path=self.root/'timeline/timeline.json'
        data=read_json(path);data['complete']=False;data['packet_limit_reached']=True;write_json(path,data)
        result=associate(self.manifest(rtp=[binding]),self.record)['sources'][0]
        self.assertEqual(result['status'],'partial')
        binding['sensor']='other'
        self.assertEqual(associate(self.manifest(rtp=[binding]),self.record)['status'],'error')

    def test_sequence_wrap_duplicates_reorder_and_restart(self):
        packets=[(i*.02,s,(i*160)%2**32) for i,s in enumerate([65534,65535,0,2,1,2,9000])]
        result=packet_metrics(packets,0,1)
        self.assertEqual((result['reordered'],result['duplicates'],result['source_restart_candidates']),(1,1,1))
        self.assertEqual(result['forward_sequence_jump_candidates'],1)

    def test_timestamp_wrap_and_clock_jump_are_distinct(self):
        result = packet_metrics([(0, 65535, 2**32 - 80), (.02, 0, 80), (.04, 1, 240), (.06, 2, 100)], 0, .1)
        self.assertEqual(result['sequence_wraps'], 1)
        self.assertEqual(result['timestamp_wraps'], 1)
        self.assertEqual(result['timestamp_backwards_candidates'], 1)

    def test_interrupted_timeline_retains_manifest_and_rows(self):
        writer = TimelineWriter(self.root / 'partial')
        writer.write({**STREAM, 'epoch': 100, 'seq': 1, 'timestamp': 160})
        writer.abort()
        result = read_json(self.root / 'partial/timeline.json')
        self.assertFalse(result['complete'])
        self.assertTrue(result['interrupted'])
        self.assertEqual(result['chunks'][0]['rows'], 1)

    def test_malformed_provenance_remains_partial_error(self):
        (self.root / 'scores.jsonl').write_text('{}\n')
        binding = {'results': 'scores.jsonl', 'source_file': 'original.wav', 'channel': 'right', 'provenance': 'provenance.json'}
        write_json(self.root / 'provenance.json', [])
        result = associate(self.manifest(nisqa=[binding]), self.record)
        self.assertEqual(result['status'], 'error')

    def test_nisqa_segment_overlap_and_legacy(self):
        path=self.root/'scores.jsonl'
        rows=[{'schema_version':'1.0','file':'original.wav','channel':'right','segment_index':i,'start_seconds':i*3,'end_seconds':(i+1)*3,'status':'ok' if i==0 else 'insufficient_evidence','scores':{'mos':3} if i==0 else None} for i in range(2)]
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        binding={'results':'scores.jsonl','source_file':'original.wav','channel':'right'}
        result=associate(self.manifest(nisqa=[binding]),self.record)['sources'][0]
        self.assertEqual(result['status'],'unverified')
        self.assertEqual(len(next(iter(result['intervals'].values()))),2)
        provenance={'schema_version':'1.0','kind':'nisqa_provenance','results':'scores.jsonl','results_sha256':sha256(path),'sources':[{'file':'original.wav','sha256':HASH,'unchanged':True}]}
        write_json(self.root/'provenance.json',provenance);binding['provenance']='provenance.json'
        self.assertEqual(associate(self.manifest(nisqa=[binding]),self.record)['sources'][0]['status'],'aligned')
        provenance['sources'][0]['sha256']='b'*64;write_json(self.root/'provenance.json',provenance)
        self.assertEqual(associate(self.manifest(nisqa=[binding]),self.record)['status'],'error')

    def test_legacy_rtp_is_only_summary(self):
        write_json(self.root/'report.json',{'pcaps':[{'sensor':'edge','analysis':{'streams':[{**STREAM,'packets':42}]}}]})
        result=associate(self.manifest(rtp=[{'report':'report.json','sensor':'edge','stream':STREAM}]),self.record)['sources'][0]
        self.assertEqual(result['status'],'legacy_summary')
        self.assertNotIn('intervals',result)

    def test_review_identity_and_manual_interval_validation(self):
        results=self.root/'results.jsonl';results.write_text(json.dumps(self.record)+'\n')
        row=label_row(self.record,self.record['result']['gaps'][0]);row.update(decision='confirmed',reviewer='reviewer',reviewed_at='2026-09-20T12:00:00+08:00')
        path=self.root/'review.csv'
        def save(value):
            with path.open('w',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=FIELDS);writer.writeheader();writer.writerow(value)
        save(row);self.assertEqual(check(path,results)['labels'],1)
        changed={**row,'start_s':1};save(changed)
        with self.assertRaises(ValueError):check(path,results)
        manual={**row,'gap_id':'manual-123','origin':'manual','type':'manual_gap','start_s':.5,'end_s':.9};save(manual)
        self.assertEqual(check(path,results)['manual'],1)
        manual['reviewed_at']='2026-09-20T12:00:00';save(manual)
        with self.assertRaises(ValueError):check(path,results)

    @unittest.skipUnless(importlib.util.find_spec('soundfile'),'optional soundfile')
    def test_nisqa_provenance_keeps_scoring_rows_unchanged(self):
        from voice_tools.tools.nisqa.service import analyze as score
        from voice_tools.tools.nisqa.backend import SCORE_NAMES
        audio=signal();path=self.root/'call.wav';write_wav(path,audio.samples,audio.sample_rate)
        factory=Mock(return_value=lambda samples,rate:dict.fromkeys(SCORE_NAMES,3.0))
        score([path],self.root/'old',channel='right',segment_seconds=1,scorer_factory=factory)
        score([path],self.root/'new',channel='right',segment_seconds=1,scorer_factory=factory,provenance=True)
        self.assertEqual((self.root/'old/results.jsonl').read_bytes(),(self.root/'new/results.jsonl').read_bytes())
        self.assertEqual((self.root/'old/results.csv').read_bytes(),(self.root/'new/results.csv').read_bytes())
        self.assertEqual(read_json(self.root/'new/provenance.json')['sources'][0]['sha256'],sha256(path))
