from dataclasses import replace
import unittest
from unittest.mock import patch

import numpy as np

from voice_tools.audio.io import Audio
from voice_tools.tools.recording_qa.assessment import Policy, assess

HASH = 'a'*64


def recording(user=((.5,1.5),), agent=((2,3),), duration=8, rate=8000):
    data = np.zeros((round(duration*rate),2),dtype=np.float32)
    for channel, spans in enumerate((user,agent)):
        for a,b in spans:
            start,stop=round(a*rate),round(b*rate)
            data[start:stop,channel]=.2*np.sin(2*np.pi*(337 if channel==0 else 220)*np.arange(stop-start)/rate)
    return Audio(data,rate)


def evidence(audio, user=((.5,1.5),), agent=((2,3),)):
    return {'name':'test-model','version':'fixture','sha256':'b'*64,'status':'completed',
            'coverage_s':audio.duration_s,'speech':[list(user),list(agent)]}


class AssessmentTests(unittest.TestCase):
    def setUp(self):
        self.policy=Policy(channels_verified=True,ai_start_s=0,audit_percent=0)

    def run_case(self,user=((.5,1.5),),agent=((2,3),),duration=8,metadata=None,policy=None,model=None):
        audio=recording(user,agent,duration)
        return assess(audio,HASH,metadata or {},policy or self.policy,model or evidence(audio,user,agent))

    def test_normal_is_automatic_with_complete_independent_evidence(self):
        result=self.run_case()
        self.assertEqual(result['decision'],'AUTO_PASS')
        self.assertFalse(result['review_required'])
        self.assertEqual(result['turns'][0]['decision'],'PASS')

    def test_missing_and_late_response_are_machine_anomalies(self):
        for agent,kind in [((), 'no_response'), (((7,7.6),),'late_response')]:
            with self.subTest(kind=kind):
                result=self.run_case(agent=agent,duration=9)
                self.assertEqual(result['decision'],'AUTO_ANOMALY')
                self.assertTrue(result['review_required'])
                self.assertIn(kind,[f['kind'] for f in result['findings']])

    def test_noise_without_model_speech_needs_review(self):
        audio=recording()
        result=assess(audio,HASH,{},self.policy,evidence(audio,agent=()))
        self.assertEqual(result['decision'],'NEEDS_REVIEW')
        self.assertEqual(result['findings'][0]['kind'],'non_speech_output')

    def test_short_window_and_short_response_do_not_pass(self):
        self.assertEqual(self.run_case(agent=(),duration=3)['decision'],'NEEDS_REVIEW')
        self.assertEqual(self.run_case(agent=((2,2.16),))['decision'],'NEEDS_REVIEW')

    def test_user_fragments_merge_into_one_turn_without_artificial_reviews(self):
        result=self.run_case(user=((.5,1),(1.6,2),(2.6,3)),agent=((3.5,4.5),))
        self.assertEqual(result['engineering']['opportunities_before_grouping'],3)
        self.assertEqual(len(result['turns']),1)
        self.assertEqual(result['decision'],'AUTO_PASS')

    def test_turn_merge_does_not_cross_an_agent_response(self):
        result=self.run_case(user=((.5,1),(1.4,2)),agent=((1.02,1.35),(2.2,3)))
        self.assertEqual(len(result['turns']),2)
        self.assertEqual(result['decision'],'AUTO_PASS')

    def test_normal_wait_for_the_next_user_turn_is_not_an_output_dropout(self):
        result=self.run_case(user=((.5,1.5),(6,7)),agent=((2,3),(7.5,8.5)),duration=10)
        self.assertEqual(result['decision'],'AUTO_PASS')
        self.assertEqual(len(result['turns']),2)
        self.assertEqual(len(result['engineering']['excluded_conversation_gaps']),1)

    def test_excluding_conversation_gap_does_not_hide_late_reply(self):
        result=self.run_case(user=((.5,1.5),(6,7)),agent=((2,3),(13,14)),duration=15)
        self.assertEqual(result['decision'],'AUTO_ANOMALY')
        self.assertIn('late_response',[item['kind'] for item in result['findings']])

    def test_unknown_roles_or_scope_require_review(self):
        for policy in [replace(self.policy,channels_verified=False),replace(self.policy,ai_start_s=None)]:
            with self.subTest(policy=policy):
                result=self.run_case(policy=policy)
                self.assertEqual(result['decision'],'NEEDS_REVIEW')
                self.assertTrue(result['blockers'])

    def test_user_speaking_to_end_is_not_a_pass(self):
        result=self.run_case(user=((.5,8),),agent=())
        self.assertEqual(result['decision'],'NEEDS_REVIEW')
        self.assertEqual(result['turns'][0]['reason'],'short_window')

    def test_explicit_wait_and_no_response_required_are_excluded(self):
        for metadata in [{'opportunities':[]},
                         {'opportunities':[{'id':'ack','at_s':1.5,'expects_response':False}]},
                         {'exclusions':[{'start_s':1.4,'end_s':8}]}]:
            with self.subTest(metadata=metadata):
                self.assertEqual(self.run_case(agent=(),metadata=metadata)['decision'],'AUTO_PASS')

    def test_explicit_greeting_can_require_response_without_user_speech(self):
        result=self.run_case(user=(),agent=((2,3),),metadata={'opportunities':[{'id':'hello','at_s':1}]})
        self.assertEqual(result['decision'],'AUTO_PASS')
        self.assertEqual(result['turns'][0]['source'],'business_event')

    def test_declared_user_speech_is_used_and_conflicts_require_review(self):
        matching={'user_speech':[{'start_s':.5,'end_s':1.5}]}
        self.assertEqual(self.run_case(metadata=matching)['decision'],'AUTO_PASS')
        self.assertEqual(self.run_case(metadata={'user_speech':[]})['decision'],'NEEDS_REVIEW')

    def test_engineering_partial_result_cannot_pass(self):
        from voice_tools.tools.recording_qa import assessment
        original=assessment.analyze_gaps
        def partial(*args,**kwargs):
            return {**original(*args,**kwargs),'processing_error':'fixture failure'}
        with patch.object(assessment,'analyze_gaps',side_effect=partial):
            result=self.run_case()
        self.assertEqual(result['decision'],'NEEDS_REVIEW')
        self.assertEqual(result['engineering']['status'],'partial_error')

    def test_model_failure_incomplete_or_invalid_times_cannot_pass(self):
        audio=recording()
        invalid=[{'status':'not_run'}, {'status':'error','error':'failure'},
                 {**evidence(audio),'coverage_s':4},
                 {**evidence(audio),'speech':[[[-1,1]],[[2,3]]]},
                 {**evidence(audio),'speech':[[[float('nan'),1]],[[2,3]]]},
                 {**evidence(audio),'speech':[[[1,2],[1.5,3]],[[2,3]]]},
                 {'status':'completed','speech':[]}]
        for model in invalid:
            with self.subTest(model=model):
                result=assess(audio,HASH,{},self.policy,model)
                self.assertEqual(result['decision'],'NEEDS_REVIEW')
                self.assertNotEqual(result['model']['status'],'completed')

    def test_silence_mono_and_duplicate_channels_need_review(self):
        for samples in [np.zeros((64000,2)),recording().samples[:,:1],np.tile(recording().samples[:,:1],(1,2))]:
            audio=Audio(samples,8000)
            model={'status':'completed','coverage_s':8,'speech':[[] for _ in range(samples.shape[1])]}
            result=assess(audio,HASH,{},self.policy,model)
            self.assertEqual(result['decision'],'NEEDS_REVIEW')

    def test_model_finds_no_turns_but_energy_does_is_not_pass(self):
        audio=recording()
        self.assertEqual(assess(audio,HASH,{},self.policy,evidence(audio,user=(),agent=()))['decision'],'NEEDS_REVIEW')

    def test_deterministic_audit_routes_normal_recording_without_changing_decision(self):
        policy=replace(self.policy,audit_percent=100)
        first=self.run_case(policy=policy);second=self.run_case(policy=policy)
        self.assertEqual(first,second)
        self.assertEqual(first['decision'],'AUTO_PASS')
        self.assertTrue(first['audit_selected'])
        self.assertTrue(first['review_windows'])

    def test_output_gap_needs_speech_or_aligned_event_support(self):
        result=self.run_case(agent=((2,3),(5.5,6.5)))
        self.assertEqual(result['decision'],'AUTO_ANOMALY')
        self.assertIn('long_output_gap',[item['kind'] for item in result['findings']])
        audio=recording(agent=((2,3),(5.5,6.5)))
        result=assess(audio,HASH,{},self.policy,evidence(audio,agent=((2,3),)))
        self.assertEqual(result['decision'],'NEEDS_REVIEW')

    def test_invalid_global_thresholds_are_rejected(self):
        for policy in [replace(self.policy,timeout_s=float('nan')),replace(self.policy,audit_percent=101),replace(self.policy,ai_start_s=-1)]:
            with self.subTest(policy=policy),self.assertRaises(ValueError):policy.validate()
