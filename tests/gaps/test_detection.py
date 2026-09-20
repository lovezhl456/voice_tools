import copy
from dataclasses import replace
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import numpy as np

from voice_tools.audio.io import Audio, write_wav
from voice_tools.core.files import sha256, write_json
from voice_tools.tools.gaps.detector import analyze, Config
from voice_tools.tools.gaps.service import analyze_batch

HASH = 'a'*64


def signal(duration=7, agent=((1, 2), (4, 5)), caller=((0, .5),), rate=16000):
    data = np.zeros((round(duration*rate), 2), dtype=np.float32)
    for channel, intervals in ((0, caller), (1, agent)):
        for start, end in intervals:
            a, b = round(start*rate), round(end*rate)
            data[a:b, channel] = .3*np.sin(2*np.pi*(337 if channel == 0 else 220)*np.arange(b-a)/rate)
    return Audio(data, rate)


def metadata(events, verified=True):
    return {'schema_version':'1.0','system_channel':1,'channel_verified':True,
            'output_events':{'schema_version':'1.0','audio_sha256':HASH,'alignment_verified':verified,
                             'source':'test-expected-playback','events':events}}


def expected(end=6, ident='u1', start=1):
    return {'id':'expected-'+ident,'type':'tts_expected','utterance_id':ident,'start_s':start,'end_s':end}


def candidates(result, kind=None):
    return [g for g in result['gaps'] if g['status']=='CANDIDATE' and (kind is None or g['type']==kind)]


class DetectionTests(unittest.TestCase):
    def test_gap_and_response_delay_are_distinct(self):
        result = analyze(signal(), HASH)
        self.assertEqual([(g['type'],g['start_s'],g['end_s']) for g in candidates(result)], [('dead_air',2.0,4.0)])
        self.assertEqual(candidates(analyze(signal(9, ((7,8),), ((.5,1.5),)), HASH)), [])

    def test_high_speech_occupancy_does_not_move_threshold(self):
        for rate in (8000,16000,48000):
            result=analyze(signal(17,((0,8),(9,17)),(),rate),HASH)
            self.assertEqual(len(candidates(result,'dead_air')),1)

    def test_micro_and_cluster_do_not_double_count(self):
        result=analyze(signal(4,((1,1.4),(1.5,1.8),(1.9,2.2),(2.3,2.7))),HASH)
        self.assertEqual(len(candidates(result,'micro_dropout')),3)
        cluster=candidates(result,'clustered_short_gaps')[0]
        self.assertEqual(len(cluster['members']),3)
        self.assertEqual(result['candidate_count'],3)

    def test_noise_and_normal_pauses_are_not_digital_holes(self):
        audio=signal(4,((1,1.5),(1.6,2.1)))
        audio.samples[24000:25600,1]=np.random.default_rng(4).normal(0,.01,1600)
        self.assertEqual(candidates(analyze(audio,HASH)),[])
        self.assertEqual(candidates(analyze(signal(4,((1,1.5),(2,2.5))),HASH)),[])

    def test_exact_deadline_and_dc_offset(self):
        for gap, count in ((.78,0),(.8,1)):
            audio=signal(5,((1,2),(2+gap,4)))
            audio.samples[:,1]+=.15
            self.assertEqual(len(candidates(analyze(audio,HASH),'dead_air')),count)

    def test_user_reaction_preserves_preceding_silence(self):
        result=analyze(signal(8,((1,2),(5,6)),((0,.5),(3,4))),HASH,config=Config(channels_verified=True))
        self.assertEqual([(g['start_s'],g['end_s']) for g in candidates(result)],[(2.,3.)])
        self.assertTrue(any(g['status']=='EXCLUDED' for g in result['gaps']))
        result=analyze(signal(7,((1,2),(4,5)),((0,.5),(1.9,3))),HASH,config=Config(channels_verified=True))
        self.assertEqual(candidates(result),[])

    def test_unverified_role_cannot_exclude(self):
        result = analyze(signal(7, ((1, 2), (4, 5)), ((1.9, 3),)), HASH)
        self.assertEqual(len(candidates(result)), 1)

    def test_earlier_interrupt_does_not_exclude_later_output(self):
        event = {'id': 'earlier', 'type': 'user_interrupt', 'start_s': .1, 'end_s': .5}
        self.assertEqual(len(candidates(analyze(signal(), HASH, metadata([event])))), 1)

    def test_gap_crossing_turn_boundary_keeps_each_turn_identity(self):
        events = [expected(3, 'first', 1), expected(5, 'second', 3)]
        result = analyze(signal(), HASH, metadata(events))
        gaps = candidates(result, 'dead_air')
        self.assertEqual([(g['start_s'], g['end_s'], g['utterance_id']) for g in gaps], [(2, 3, 'first'), (3, 4, 'second')])

    def test_expected_window_clips_instead_of_discarding(self):
        result = analyze(signal(), HASH, metadata([expected(3, start=1)]))
        self.assertTrue(any(g['start_s'] == 2 and g['end_s'] == 3 for g in candidates(result)))
        self.assertTrue(any(g['status'] == 'EXCLUDED' for g in result['gaps']))

    def test_playback_after_end_invalidates_event_filter(self):
        events = [expected(6), {'id': 'end', 'type': 'tts_end', 'utterance_id': 'u1', 'at_s': 2},
                  {'id': 'play', 'type': 'tts_playback', 'utterance_id': 'u1', 'start_s': 4, 'end_s': 5}]
        result = analyze(signal(), HASH, metadata(events))
        self.assertEqual(len(candidates(result)), 1)
        self.assertEqual(candidates(result)[0]['evidence_level'], 'acoustic_only')

    def test_nonfinite_audio_is_rejected_and_correlation_is_only_hint(self):
        audio = signal()
        audio.samples[5, 0] = float('nan')
        with self.assertRaises(ValueError):
            analyze(audio, HASH)
        audio = signal()
        audio.samples[:, 0] = audio.samples[:, 1] * .7
        result = analyze(audio, HASH)
        self.assertAlmostEqual(result['health']['correlation'], 1)
        self.assertNotEqual(result['status'], 'INSUFFICIENT_EVIDENCE')

    def test_only_authorized_waits_filter(self):
        for allowed, count in ((False,1),(True,0)):
            events=[{'id':'wait','type':'tool_wait','start_s':2,'end_s':4,'allows_silence':allowed}]
            result=analyze(signal(),HASH,metadata(events))
            self.assertEqual(len(candidates(result)),count)
        result=analyze(signal(),HASH,metadata(events,False))
        self.assertEqual(len(candidates(result)),1)
        self.assertEqual(candidates(result)[0]['evidence_level'],'acoustic_only')

    def test_terminal_requires_complete_expected_window(self):
        audio=signal(7,((1,2),))
        self.assertEqual(candidates(analyze(audio,HASH)),[])
        self.assertEqual(len(candidates(analyze(audio,HASH,metadata([expected(6)])),'terminal_interruption')),1)
        partial=analyze(audio,HASH,metadata([expected(8)]))
        self.assertEqual(candidates(partial),[])
        self.assertEqual(partial['gaps'][0]['status'],'CENSORED')

    def test_cancel_and_tts_chunks(self):
        events=[expected(),{'id':'cancel','type':'tts_cancel','utterance_id':'u1','at_s':2.1}]
        self.assertEqual(candidates(analyze(signal(7,((1,2),)),HASH,metadata(events))),[])
        events=[expected(5),{'id':'part1','type':'tts_playback','utterance_id':'u1','start_s':1,'end_s':2},
                {'id':'part2','type':'tts_playback','utterance_id':'u1','start_s':4,'end_s':5}]
        result=analyze(signal(),HASH,metadata(events))
        self.assertEqual(len(candidates(result,'dead_air')),1)

    def test_conflicts_degrade_instead_of_hiding_candidate(self):
        events=[expected(5),expected(6,'u2',2),{'id':'wait','type':'tool_wait','start_s':2,'end_s':4,'allows_silence':True}]
        result=analyze(signal(),HASH,metadata(events))
        self.assertEqual(len(candidates(result)),1)
        self.assertEqual(candidates(result)[0]['evidence_level'],'acoustic_only')

    def test_identity_changes_with_rules_not_path(self):
        first=analyze(signal(),HASH)
        second=analyze(signal(),HASH,config=Config(dead_air_ms=900))
        self.assertNotEqual(first['fingerprint'],second['fingerprint'])
        self.assertEqual(first,analyze(signal(),HASH))
        event = metadata([expected()])
        original = analyze(signal(), HASH, event)
        event['source'] = '/another/machine/call.wav'
        event['output_events']['source'] = '/another/machine/events.json'
        self.assertEqual(original['fingerprint'], analyze(signal(), HASH, event)['fingerprint'])

    def test_invalid_events_and_config(self):
        event=metadata([expected()]);event['output_events']['audio_sha256']='b'*64
        for broken in (event, [], {'channel_verified':'yes'}, {'system_channel':True}):
            with self.assertRaises(ValueError):analyze(signal(),HASH,broken)
        with self.assertRaises(ValueError):analyze(signal(),HASH,config=Config(dead_air_ms=float('nan')))

    def test_unreliable_inputs_and_swapped_channels(self):
        for data in (np.zeros((16000,2),dtype=np.float32),np.zeros((16000,1),dtype=np.float32)):
            self.assertEqual(analyze(Audio(data,16000),HASH)['status'],'INSUFFICIENT_EVIDENCE')
        audio=signal();audio.samples[:]=audio.samples[:,::-1]
        result=analyze(audio,HASH,config=Config(system_channel=0))
        self.assertEqual(len(candidates(result)),1)

    def test_batch_error_and_evidence_error_keep_other_results(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);good=root/'good.wav';bad=root/'bad.wav'
            audio=signal();write_wav(good,audio.samples,audio.sample_rate);bad.write_bytes(b'broken')
            result=analyze_batch([good,bad],root/'run',include_audio=True)
            self.assertEqual((result['files'],result['errors'],result['candidates']),(2,1,1))
            evidence=root/'evidence.json';write_json(evidence,{'wrong':True})
            result=analyze_batch([good],root/'with-evidence',evidence=evidence)
            self.assertEqual((result['errors'],result['candidates']),(1,1))

    def test_interrupt_keeps_completed_records_and_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('a', 'b'):
                audio = signal()
                write_wav(root / (name + '.wav'), audio.samples, audio.sample_rate)
            original = analyze(signal(), HASH)
            with patch('voice_tools.tools.gaps.service.analyze', side_effect=[original, KeyboardInterrupt()]):
                result = analyze_batch([root], root / 'run')
            self.assertTrue(result['interrupted'])
            self.assertEqual((result['files'], result['errors']), (2, 1))
            self.assertTrue((root / 'run/review.html').exists())
