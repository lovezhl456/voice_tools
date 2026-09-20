import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import wave

import numpy as np

from tests.html_fixtures import Page
from voice_tools.audio.io import write_wav
from voice_tools.core.files import read_json
from voice_tools.tools.recording_qa.batch import analyze_batch
from voice_tools.tools.recording_qa.compare import compare
from voice_tools.tools.recording_qa.dataset import freeze
from voice_tools.tools.recording_qa.detector import Config
from voice_tools.tools.recording_qa.review import evaluate, extra_fields, promote
from voice_tools.tools.recording_qa.scenarios import fixture


class P0WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def recording(self, name):
        samples, events = fixture(name)
        path = self.root / (name + ".wav")
        write_wav(path, samples, 8000)
        if events is not None:
            path.with_suffix(".events.json").write_text(json.dumps(events))
        return path

    def label(self, output, choices):
        with (output / "review.csv").open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream); fields = reader.fieldnames; rows = list(reader)
        for row, choice in zip(rows, choices):
            row.update(decision=choice, reviewer="test-only", reviewed_at="2026-09-19T12:00:00Z")
        with (output / "review.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
        gold = self.root / (output.name + "-gold.json")
        promote(output / "review.csv", output / "results.jsonl", gold, "synthetic")
        return gold

    def test_workbench_assets_and_clips_preserve_mapping(self):
        output = self.root / "report"
        analyze_batch([self.recording("normal")], output, include_audio=True, hide_paths=True)
        record = json.loads((output / "results.jsonl").read_text())
        self.assertEqual(set(record["playback_sources"]), {"both", "left", "right"})
        for path in record["playback_sources"].values(): self.assertTrue((output / path).is_file())
        clip = record["clips"]["turn-1"]
        self.assertEqual((clip["original_start_s"], clip["original_end_s"]), (4, 18))
        self.assertEqual(read_json(output / clip["metadata"])["audio_sha256"], record["audio_sha256"])
        page = (output / "review.html").read_text()
        self.assertNotIn(str(self.root), page)
        self.assertNotIn("__REVIEW_DATA__", page)
        self.assertIn("beforeunload", page)
        self.assertNotIn("__WAVEFORM_", page)
        self.assertNotIn("__PLAYBACK_SCRIPT__", page)
        self.assertIn("createReviewPlayback", page)
        self.assertIn("WaveSurfer.js 7.12.12", page)
        self.assertIn("Redistribution and use", page)
        parsed = Page(page)
        external = [script['attrs']['src'] for script in parsed.scripts if 'src' in script['attrs']]
        self.assertEqual(external, [], "Offline review must embed its waveform dependencies")
        payloads = [script for script in parsed.scripts if script['attrs'].get('id') == 'reviewData']
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]['attrs']['type'], 'application/json')
        data = json.loads(payloads[0]['text'])
        self.assertEqual(len(data['records']), 1)
        embedded = data['records'][0]
        self.assertEqual(embedded['input'], 'normal.wav')
        for name in ('sample_id', 'audio_sha256', 'playback_sources', 'clips'):
            self.assertEqual(embedded[name], record[name])
        for role, name in embedded['playback_sources'].items():
            with wave.open(str(output / name)) as audio:
                self.assertEqual(audio.getnchannels(), 2 if role == 'both' else 1)
                self.assertEqual((audio.getframerate(), audio.getnframes()), (8000, 18 * 8000))
        with wave.open(str(output / clip['audio'])) as audio:
            self.assertEqual(audio.getnframes(), 14 * 8000)
        from voice_tools.core.review import page as shared_page
        vendor = Path(shared_page.__file__).parent / 'vendor'
        manifest = read_json(vendor / 'manifest.json')
        for name, digest in manifest['files'].items():
            content = (vendor / name).read_bytes()
            self.assertEqual(hashlib.sha256(content).hexdigest(), digest, name)
            self.assertTrue(content.decode().replace('</script', '<\\/script') in page, name)
        self.assertNotIn('__REVIEW_SCRIPT__', page)

    def test_freeze_keeps_windows_fixed_when_threshold_changes(self):
        samples, _ = fixture("missing")
        samples[80000:88000, 0] = .003 * np.sin(2*np.pi*440*np.arange(8000)/8000)
        source = self.root / "call.wav"; write_wav(source, samples, 8000)
        initial = self.root / "initial"; analyze_batch([source], initial)
        frozen = self.root / "frozen"; result = freeze(initial / "results.jsonl", frozen)
        self.assertIn("不是黄金", result["notice"])
        event = read_json(next(frozen.glob("*.events.json")))
        self.assertFalse(event["timeline_reviewed"])
        self.assertIn("user_speech", event)
        baseline, candidate = self.root / "before", self.root / "after"
        analyze_batch([frozen], baseline, Config(threshold_db=-45))
        analyze_batch([frozen], candidate, Config(threshold_db=-65))
        a = json.loads((baseline / "results.jsonl").read_text())["result"]["opportunities"]
        b = json.loads((candidate / "results.jsonl").read_text())["result"]["opportunities"]
        self.assertEqual([(op["at_s"],op["observed_until_s"]) for op in a], [(op["at_s"],op["observed_until_s"]) for op in b])
        gold = self.label(baseline, ["missing"])
        compare(baseline / "results.jsonl", candidate / "results.jsonl", gold, self.root / "comparison")

    def test_compare_detects_actual_improvement_on_fixed_events(self):
        source = self.recording("quiet")
        before, after = self.root / "before", self.root / "after"
        analyze_batch([source], before)
        analyze_batch([source], after, Config(threshold_db=-70))
        gold = self.label(before, ["audible"])
        result = compare(before / "results.jsonl", after / "results.jsonl", gold, self.root / "compare")
        self.assertEqual(result["changes"][0]["change"], "removed_candidate")
        self.assertEqual(result["baseline"]["counts"]["fp"], 1)
        self.assertEqual(result["candidate"]["counts"]["fp"], 0)
        self.assertIn("precision_ci95", result["candidate"])
        self.assertTrue((self.root / "compare/report.html").exists())

    def test_compare_refuses_unfixed_timeline_and_changed_sla(self):
        source = self.recording("no_events")
        before = self.root / "before"; analyze_batch([source], before)
        gold = self.label(before, ["missing"])
        with self.assertRaisesRegex(ValueError, "固定机会"):
            compare(before / "results.jsonl", before / "results.jsonl", gold, self.root / "bad")
        fixed = self.recording("missing")
        first, second = self.root / "first", self.root / "second"
        analyze_batch([fixed], first); analyze_batch([fixed], second, Config(timeout_s=4))
        gold = self.label(first, ["missing"])
        with self.assertRaisesRegex(ValueError, "迟答口径"):
            compare(first / "results.jsonl", second / "results.jsonl", gold, self.root / "bad2")
        self.assertFalse((self.root / "bad2").exists())

    def test_compare_refuses_missing_predictions_and_window_drift(self):
        output = self.root / "base"; analyze_batch([self.recording("missing")], output)
        gold = self.label(output, ["missing"])
        record = json.loads((output / "results.jsonl").read_text())
        record["result"]["opportunities"][0]["observed_until_s"] -= 1
        changed = self.root / "changed.jsonl"; changed.write_text(json.dumps(record)+"\n")
        with self.assertRaisesRegex(ValueError, "窗口"):
            compare(output / "results.jsonl", changed, gold, self.root / "bad")
        record["result"]["opportunities"] = []; changed.write_text(json.dumps(record)+"\n")
        with self.assertRaisesRegex(ValueError, "集合不一致"):
            compare(output / "results.jsonl", changed, gold, self.root / "bad")

    def test_abstentions_not_counted_as_success(self):
        output = self.root / "base"; analyze_batch([self.recording("hangup")], output)
        gold = self.label(output, ["audible"])
        metrics = evaluate(gold, output / "results.jsonl")
        self.assertEqual(metrics["evaluated"], 0)
        self.assertEqual(metrics["abstained"], 1)
        self.assertEqual(metrics["counts"]["tn"], 0)
        self.assertIsNone(metrics["precision"])

    def test_splits_and_timing_metadata_are_validated(self):
        row = {"at_s":6, "observed_until_s":18, "decision":"delayed", "first_audible_s":"12", "deadline_s":"5", "policy_id":"v1", "group_id":"call-1", "split":"validation"}
        self.assertEqual(extra_fields(row)["first_audible_s"], 12)
        with self.assertRaisesRegex(ValueError, "不一致"):
            extra_fields({**row, "first_audible_s":7})
        with self.assertRaisesRegex(ValueError, "policy_id"):
            extra_fields({**row, "policy_id":""})
        from voice_tools.tools.recording_qa.review import validate_splits
        with self.assertRaisesRegex(ValueError, "不能跨"):
            validate_splits([{"group_id":"a", "audio_sha256":"same", "split":"calibration"}, {"group_id":"b", "audio_sha256":"same", "split":"validation"}])

    def test_freeze_rejects_changed_original(self):
        source = self.recording("normal"); output = self.root / "run"
        analyze_batch([source], output); source.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "摘要变化"):
            freeze(output / "results.jsonl", self.root / "frozen")

    def test_wilson_zero_and_perfect_do_not_claim_certainty(self):
        from voice_tools.tools.recording_qa.metrics import wilson
        self.assertIsNone(wilson(0,0))
        self.assertLess(wilson(1,1)[0], .5)
        self.assertGreater(wilson(0,1)[1], .5)
