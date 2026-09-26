import importlib.util
from pathlib import Path
import tempfile
import unittest
import wave

import numpy as np

from voice_tools.audio.io import Audio, read_wav, write_wav
from voice_tools.audio.activity import detect_activity


class AudioTests(unittest.TestCase):
    def test_stereo_roundtrip_and_roles(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = np.zeros((8000, 2), dtype=np.float32)
            source[:, 0] = 0.25
            path = Path(tmp) / "input.wav"
            write_wav(path, source, 8000)
            audio = read_wav(path)
            self.assertEqual(audio.sample_rate, 8000)
            np.testing.assert_array_equal(audio.samples, source)

    def test_invalid_format_and_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.wav"
            for content in (b"not a wav", b""):
                path.write_bytes(content)
                with self.assertRaises(ValueError):
                    read_wav(path)
            with wave.open(str(path), "wb") as stream:
                stream.setnchannels(2)
                stream.setsampwidth(1)
                stream.setframerate(8000)
                stream.writeframes(b"\x00" * 16000)
            with self.assertRaisesRegex(ValueError, "PCM16"):
                read_wav(path)

    def test_duration_limit_before_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "long.wav"
            write_wav(path, np.zeros((16000, 2)), 8000)
            with self.assertRaises(ValueError):
                read_wav(path, max_seconds=1)

    def test_dc_is_not_activity(self):
        activity, health = detect_activity(Audio(np.full((8000, 2), 0.1), 8000))
        self.assertEqual(activity, [[], []])
        self.assertGreater(health["max_dc_offset"], 0.09)

    def test_memory_limit_before_decoding(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bounded.wav"
            write_wav(path, np.zeros((8000, 2)), 8000)
            with self.assertRaisesRegex(ValueError, "内存上限"):
                read_wav(path, max_decoded_bytes=1024)

    def test_partial_frame_not_zero_padded(self):
        data = np.zeros((200, 2))
        data[160:, 0] = np.tile([0.5, -0.5], 20)
        activity, _ = detect_activity(Audio(data, 8000), minimum_s=0.001, gap_s=0)
        self.assertEqual(activity[0], [(0.02, 0.025)])

    @unittest.skipUnless(importlib.util.find_spec("webrtcvad"), "optional VAD extra not installed")
    def test_webrtc_cpu_backend(self):
        for rate in (8000, 16000, 32000, 48000):
            activity, _ = detect_activity(Audio(np.zeros((rate, 2)), rate), backend="webrtcvad")
            self.assertEqual(activity, [[], []])
        with self.assertRaisesRegex(ValueError, "重采样"):
            detect_activity(Audio(np.zeros((11025, 2)), 11025), backend="webrtcvad")
