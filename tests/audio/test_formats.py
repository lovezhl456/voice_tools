import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import numpy as np

from voice_tools.audio.formats import load_for_inspection
from voice_tools.audio.health import inspect_health
from voice_tools.audio.io import Audio, read_wav, write_wav
from voice_tools.core.files import read_json, sha256
from voice_tools.tools.audio.service import process
from voice_tools.tools.recording_qa.batch import analyze_batch


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.wav"
        self.samples = np.zeros((24000, 2), dtype=np.float32)
        self.samples[2400:8000, 0] = .2 * np.sin(2 * np.pi * 300 * np.arange(5600) / 8000)
        self.samples[12000:20000, 1] = .1 * np.sin(2 * np.pi * 600 * np.arange(8000) / 8000)
        write_wav(self.source, self.samples, 8000)

    def records(self, directory):
        return [json.loads(x) for x in (directory / "results.jsonl").read_text().splitlines()]

    def test_health_is_per_channel_and_activity_level_ignores_silence(self):
        samples = self.samples.copy()
        samples[1000:1100, 1] = 1
        health = inspect_health(Audio(samples, 8000))
        self.assertEqual(health["channels"][0]["clipping_fraction"], 0)
        self.assertGreater(health["channels"][1]["clipping_fraction"], 0)
        self.assertTrue(health["channels"][1]["dc_spans"])
        self.assertGreater(health["channels"][0]["active_median_dbfs"], health["channels"][0]["median_dbfs"])
        silent = inspect_health(Audio(np.zeros((8000, 2)), 8000))
        self.assertIsNone(silent["correlation"])
        self.assertTrue(silent["duplicate_channels"])

    def test_native_inspection_needs_no_ffmpeg(self):
        from unittest.mock import patch
        with patch("voice_tools.audio.formats.shutil.which", return_value=None):
            result = process([self.source], self.root / "inspect", "inspect")
            self.assertEqual(result["errors"], 0)

    def test_bad_file_isolated_and_output_protected(self):
        bad = self.root / "empty.wav"; bad.write_bytes(b"")
        result = process([bad, self.source], self.root / "inspect", "inspect")
        self.assertEqual((result["files"], result["errors"]), (2, 1))
        with self.assertRaisesRegex(ValueError, "覆盖"):
            process([self.source], self.root / "inspect", "inspect")

    def test_native_mono_not_duplicated(self):
        source = self.root / "mono.wav"; write_wav(source, self.samples[:, 0], 8000)
        output = self.root / "prepared"
        process([source], output, "prepare", sample_rate=8000)
        record = self.records(output)[0]
        self.assertEqual(read_wav(output / record["output"]).samples.shape[1], 1)

    def test_invalid_event_cleans_generated_audio(self):
        self.source.with_suffix(".events.json").write_text("[]")
        output = self.root / "prepared"
        self.assertEqual(process([self.source], output, "prepare", sample_rate=8000)["errors"], 1)
        self.assertEqual(list(output.glob("*.wav")), [])

    def test_truncated_wave_rejected(self):
        self.source.write_bytes(self.source.read_bytes()[:-100])
        self.assertEqual(process([self.source], self.root / "prepared", "prepare", sample_rate=8000)["errors"], 1)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "optional FFmpeg unavailable")
    def test_formats_and_resampling_preserve_tracks_and_origin(self):
        before = sha256(self.source)
        variants = [("pcm.wav", "pcm_s16le"), ("audio.flac", "flac"), ("audio.mp3", "libmp3lame"),
                    ("audio.m4a", "aac"), ("alaw.wav", "pcm_alaw"), ("mulaw.wav", "pcm_mulaw")]
        for index, (filename, codec) in enumerate(variants):
            with self.subTest(codec=codec):
                path = self.root / filename
                subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(self.source), "-c:a", codec, str(path)], check=True)
                output = self.root / f"out-{index}"
                summary = process([path], output, "prepare", sample_rate=16000)
                self.assertEqual(summary["errors"], 0, self.records(output))
                record = self.records(output)[0]
                audio = read_wav(output / record["output"])
                self.assertEqual((audio.sample_rate, audio.samples.shape[1]), (16000, 2))
                self.assertGreater(np.max(np.abs(audio.samples[5000:12000, 0])), .1)
                self.assertLess(np.max(np.abs(audio.samples[5000:12000, 1])), .01)
                self.assertGreater(np.max(np.abs(audio.samples[26000:38000, 1])), .04)
                if codec in ("pcm_s16le", "flac", "pcm_alaw", "pcm_mulaw"):
                    self.assertTrue(record["time_mapping"]["verified"])
                    for channel, expected in ((0, .3), (1, 1.5)):
                        first = np.flatnonzero(np.abs(audio.samples[:, channel]) > .03)[0] / 16000
                        self.assertLess(abs(first - expected), .02)
                else:
                    self.assertFalse(record["time_mapping"]["verified"])
                inspected, _ = load_for_inspection(path)
                self.assertEqual(inspected.samples.shape[1], 2)
        self.assertEqual(sha256(self.source), before)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "optional FFmpeg unavailable")
    def test_raw_requires_contract_and_preserves_mono(self):
        source = self.root / "input.pcm"
        source.write_bytes((self.samples[:, 0] * 32768).astype("<i2").tobytes())
        self.assertEqual(process([source], self.root / "bad", "prepare")["errors"], 1)
        output = self.root / "ok"
        result = process([source], output, "prepare", raw={"format": "s16le", "sample_rate": 8000, "channels": 1})
        self.assertEqual(result["errors"], 0, self.records(output))
        self.assertEqual(self.records(output)[0]["output_channels"], 1)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "optional FFmpeg unavailable")
    def test_compressed_events_require_explicit_manual_alignment(self):
        source = self.root / "lossy.mp3"
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(self.source), str(source)], check=True)
        events = {"schema_version": "1.0", "channel_verified": True, "ai_start_s": 0,
                  "opportunities": [{"id": "one", "at_s": 1}], "user_speech": []}
        source.with_suffix(".events.json").write_text(json.dumps(events))
        output = self.root / "prepared"
        process([source], output, "prepare")
        record = self.records(output)[0]; target = output / record["output"]
        self.assertFalse(target.with_suffix(".events.json").exists())
        target.with_suffix(".events.json").write_text(json.dumps(events))
        self.assertEqual(analyze_batch([target], self.root / "refused")["errors"], 1)
        events["alignment"] = {"audio_sha256": sha256(target), "reviewer": "test", "reviewed_at": "2026-09-19T12:00:00Z"}
        target.with_suffix(".events.json").write_text(json.dumps(events))
        self.assertEqual(analyze_batch([target], self.root / "aligned")["errors"], 0)
