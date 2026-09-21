import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from voice_tools.audio.io import Audio
from voice_tools.tools.recording_qa import speech_model


class ModelContractTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.payload=b'fixture model'
        self.spec={**speech_model.manifest(),'bytes':len(self.payload),'sha256':hashlib.sha256(self.payload).hexdigest()}

    def test_download_checks_hash_then_cache_needs_no_network(self):
        with patch.object(speech_model,'manifest',return_value=self.spec),patch.object(speech_model,'urlopen',return_value=io.BytesIO(self.payload)):
            value=speech_model.download(self.root/'model')
        self.assertFalse(value['cached']);self.assertEqual(Path(value['model']).read_bytes(),self.payload)
        with patch.object(speech_model,'manifest',return_value=self.spec),patch.object(speech_model,'urlopen') as network:
            self.assertTrue(speech_model.download(self.root/'model')['cached']);network.assert_not_called()

    def test_bad_download_and_bad_existing_model_are_not_loaded_or_replaced(self):
        with patch.object(speech_model,'manifest',return_value=self.spec),patch.object(speech_model,'urlopen',side_effect=lambda *a,**kw:io.BytesIO(b'bad')):
            with self.assertRaises(ValueError):speech_model.download(self.root/'model')
        target=self.root/'model'/self.spec['filename'];self.assertFalse(target.exists())
        target.write_bytes(b'keep')
        with patch.object(speech_model,'manifest',return_value=self.spec),patch.object(speech_model,'urlopen') as network:
            with self.assertRaises(ValueError):speech_model.download(self.root/'model')
            network.assert_not_called()
        self.assertEqual(target.read_bytes(),b'keep')

    def fake(self,probability=.8):
        class Session:
            def __init__(self):self.states=[];self.shapes=[]
            def run(self,outputs,inputs):
                self.states.append(float(inputs['state'][0,0,0]));self.shapes.append(inputs['input'].shape)
                return np.array([[probability]],dtype=np.float32),inputs['state']+1
        model=object.__new__(speech_model.SpeechModel)
        model.session=Session();model.threshold=.5;model.identity={'name':'fake'}
        return model

    def test_state_resets_between_channels_and_recordings_and_tail_is_bounded(self):
        model=self.fake();audio=Audio(np.zeros((2700,2),dtype=np.float32),8000)
        with patch.object(speech_model,'urlopen',side_effect=AssertionError('inference must be offline')):
            first=model.predict(audio);second=model.predict(audio)
        self.assertEqual(first,second)
        self.assertEqual(model.session.states.count(0),4)
        self.assertEqual(set(model.session.shapes),{(1,288)})
        self.assertEqual(first['speech'],[[[0.0,audio.duration_s]],[[0.0,audio.duration_s]]])

    def test_resampling_uses_rational_ratio_and_preserves_original_duration(self):
        model=self.fake();calls=[]
        def resample(samples,up,down,axis):
            calls.append((up,down,axis));return np.zeros((round(len(samples)*up/down),2),dtype=np.float32)
        model.resample=resample
        audio=Audio(np.zeros((22050,2),dtype=np.float32),22050)
        result=model.predict(audio)
        self.assertEqual(calls,[(320,441,0)])
        self.assertEqual(result['coverage_s'],1)
        self.assertEqual(result['sample_rate'],16000)
        self.assertTrue(all(b<=1 for spans in result['speech'] for a,b in spans))

    def test_invalid_probability_and_nonfinite_audio_are_rejected(self):
        audio=Audio(np.zeros((8000,1),dtype=np.float32),8000)
        for probability in [float('nan'),1.1,-.1]:
            with self.subTest(probability=probability),self.assertRaises(ValueError):self.fake(probability).predict(audio)
        audio.samples[0,0]=float('nan')
        with self.assertRaises(ValueError):self.fake().predict(audio)

    def test_missing_model_doctor_is_actionable_and_offline(self):
        with patch.object(speech_model,'urlopen',side_effect=AssertionError('doctor must be offline')):
            result=speech_model.doctor(self.root/'missing')
        self.assertFalse(result['ready']);self.assertFalse(result['network_accessed'])
        self.assertIn('model-download',result['issues'][0])
