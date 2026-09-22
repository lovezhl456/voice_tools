import copy
import math
import unittest

import numpy as np

from voice_tools.tools.detect.definition import validate, fingerprint
from voice_tools.tools.detect.engine import analyze, evaluate
from voice_tools.tools.detect.metrics import measure, parameters
from .fixtures import config, audio, example


class Definitions(unittest.TestCase):
    def test_examples_and_normalized_identity(self):
        for kind in ('ai-silence', 'low-volume'):
            definition = example(kind)
            self.assertEqual(definition, validate(definition))
            self.assertEqual(fingerprint(definition), fingerprint(validate(definition)))

    def test_rejects_typo_unknown_metric_invalid_values_and_refs(self):
        changes = [lambda c: c.update(typo=1), lambda c: c.update(version=1),
                   lambda c: c['metrics']['level'].update(kind='python:exec'),
                   lambda c: c['metrics']['level'].update(channel=True),
                   lambda c: c['metrics']['level']['params'].update(threshold=1),
                   lambda c: c['rules'][0]['when'].update(metric='missing'),
                   lambda c: c['rules'][0]['when'].update(value=float('nan')),
                   lambda c: c['rules'][0]['when'].update(value=True),
                   lambda c: c.update(window={'kind':'sliding','step_s':0}),
                   lambda c: c.update(scope={'start_s':4, 'end_s':2}),
                   lambda c: c.update(scope={'exclude':[{'start_s':1,'end_s':1}]}),
                   lambda c: c['rules'].append(copy.deepcopy(c['rules'][0]))]
        for change in changes:
            value = config(); change(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate(value)

    def test_limits_recursive_conditions(self):
        value = config()
        rule = value['rules'][0]['when']
        for _ in range(10):
            rule = {'not':rule}
        value['rules'][0]['when'] = rule
        with self.assertRaises(ValueError):
            validate(value)
        value['rules'][0]['when'] = {'all':[{'metric':'level','op':'lt','value':0}] * 129}
        with self.assertRaises(ValueError):
            validate(value)

    def test_boolean_logic_and_exclusion(self):
        value = config()
        leaf = value['rules'][0]['when']
        value['rules'][0]['when'] = {'all':[leaf, {'any':[{'not':{'metric':'level','op':'ge','value':-30}},leaf]}]}
        self.assertTrue(analyze(audio(),validate(value))['findings'])
        value['rules'][0]['unless'] = leaf
        self.assertEqual(analyze(audio(),validate(value))['findings'], [])


class Measurements(unittest.TestCase):
    def test_amplitude_and_silence_are_finite(self):
        samples = audio().samples[:,1]
        value = measure('rms_dbfs', samples, 8000, parameters('rms_dbfs', {}))
        self.assertAlmostEqual(value,20*math.log10(.005/math.sqrt(2)), places=4)
        silence = np.zeros(800)
        self.assertEqual(measure('rms_dbfs',silence,8000,parameters('rms_dbfs',{})),-120)
        self.assertEqual(measure('silence_ratio',silence,8000,parameters('silence_ratio',{})),1)
        self.assertEqual(measure('activity_count',silence,8000,parameters('activity_count',{})),0)

    def test_duration_count_cumulative_and_tail(self):
        signal = np.zeros(8100)
        signal[:1600] = .5*np.sin(2*np.pi*400*np.arange(1600)/8000)
        signal[4000:6400] = .5*np.sin(2*np.pi*400*np.arange(2400)/8000)
        params = parameters('activity_total_s', {'min_duration_s':.1,'join_gap_s':0})
        self.assertEqual(measure('activity_count',signal,8000,params),2)
        self.assertAlmostEqual(measure('activity_total_s',signal,8000,params),.5)
        self.assertAlmostEqual(measure('silence_total_s',signal,8000,params),.5125)
        self.assertAlmostEqual(measure('activity_ratio',signal,8000,params),.5/1.0125)
        params['join_gap_s']=.4
        self.assertEqual(measure('activity_count',signal,8000,params),1)
        self.assertAlmostEqual(measure('activity_longest_s',signal,8000,params),.8)

    def test_dc_option_and_clipping(self):
        samples = np.ones(800)*.5
        self.assertEqual(measure('rms_dbfs',samples,8000,parameters('rms_dbfs',{})),-120)
        self.assertAlmostEqual(measure('rms_dbfs',samples,8000,parameters('rms_dbfs',{'remove_dc':False})),-6.0206,places=4)
        self.assertEqual(measure('clipping_ratio',np.ones(800),8000,parameters('clipping_ratio',{'remove_dc':False})),1)


class Windows(unittest.TestCase):
    def test_scope_exclusions_split_windows_and_preserve_timestamps(self):
        value = config(); value['scope']={'start_s':1,'end_s':7,'skip_first_s':2,'skip_last_s':0,'exclude':[{'start_s':3,'end_s':5}]}
        result=analyze(audio(),validate(value))
        self.assertEqual([(r['start_s'],r['end_s']) for r in result['findings']],[(2,3),(5,7)])

    def test_partial_windows_are_explicit(self):
        value=config(); value['window']={'kind':'sliding','length_s':3,'step_s':3}
        full=analyze(audio(),validate(value))
        self.assertEqual(full['windows'],2)
        value['window']['include_partial']=True
        partial=analyze(audio(),validate(value))
        self.assertEqual(partial['windows'],3)
        self.assertEqual(partial['findings'][-1]['end_s'],8)

    def test_after_activity_and_user_continuation_exclusion(self):
        sample=audio(level=0)
        sample.samples[:,0]=0
        sample.samples[8000:16000,0]=.1*np.sin(2*np.pi*400*np.arange(8000)/8000)
        result=analyze(sample,example())
        self.assertEqual(result['windows'],1)
        self.assertEqual((result['findings'][0]['start_s'],result['findings'][0]['end_s']),(2,5))
        sample.samples[20000:40000,0]=.1*np.sin(2*np.pi*400*np.arange(20000)/8000)
        result=analyze(sample,example())
        self.assertTrue(all(row['start_s'] >= 5 for row in result['findings']))

    def test_no_windows_missing_channel_and_excessive_windows(self):
        sample=audio(seconds=1)
        value=config(); value['window']={'kind':'sliding','length_s':2}
        self.assertEqual(analyze(sample,validate(value))['status'],'no_windows')
        with self.assertRaisesRegex(ValueError,'声道'):
            analyze(audio(channels=1),config())
        value=config(); value['window']={'kind':'sliding','length_s':.02,'step_s':.02}
        with self.assertRaisesRegex(ValueError,'10000'):
            analyze(audio(seconds=201),validate(value))

    def test_low_volume_does_not_tag_silence(self):
        self.assertEqual(analyze(audio(level=0),example('low-volume'))['findings'],[])
        self.assertTrue(analyze(audio(),example('low-volume'))['findings'])
