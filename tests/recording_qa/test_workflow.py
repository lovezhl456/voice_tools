import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from voice_tools.core.files import read_json, write_json
from voice_tools.tools.recording_qa.batch import analyze_batch
from voice_tools.tools.recording_qa.review import evaluate, promote
from voice_tools.tools.recording_qa.scenarios import generate


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = self.root / "data"
        self.output = self.root / "report"
        generate(self.data)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "voice_tools", *map(str, args)], capture_output=True, text=True)

    def review_rows(self):
        with (self.output / "review.csv").open(encoding="utf-8-sig", newline="") as stream:
            return list(csv.DictReader(stream))

    def save_reviews(self, rows):
        with (self.output / "review.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

    def test_cli_full_batch_with_portable_audio(self):
        result = self.run_cli("qa", "analyze", self.data, "--out", self.output, "--include-audio")
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = read_json(self.output / "run.json")
        self.assertEqual(summary["files"], 20)
        self.assertEqual(summary["errors"], 0)
        records = [json.loads(line) for line in (self.output / "results.jsonl").read_text().splitlines()]
        expected = {case["id"]: case["expected_statuses"] for case in read_json(self.data / "manifest.json")["cases"]}
        for record in records:
            self.assertTrue((self.output / record["audio_copy"]).is_file())
            self.assertEqual([op["status"] for op in record["result"]["opportunities"]], expected[Path(record["input"]).stem])
        self.assertTrue((self.output / "report.html").is_file())
        self.assertEqual(len(self.review_rows()), 19)

    def test_bad_recording_does_not_drop_good_results(self):
        (self.data / "bad.wav").write_bytes(b"broken")
        result = self.run_cli("qa", "analyze", self.data, "--out", self.output)
        self.assertEqual(result.returncode, 3)
        summary = read_json(self.output / "run.json")
        self.assertEqual((summary["files"], summary["errors"]), (21, 1))

    def test_invalid_event_sidecar_and_safe_html(self):
        name = '<script>alert("x")</script>'.replace("/", "_")
        source = self.data / "missing.wav"
        source.rename(self.data / (name + ".wav"))
        (self.data / "normal.events.json").write_text('{"ai_start_s":NaN}')
        summary = analyze_batch([self.data], self.output)
        self.assertEqual(summary["errors"], 1)
        report = (self.output / "report.html").read_text()
        self.assertNotIn('<script>alert', report)
        self.assertIn('&lt;script&gt;', report)

    def test_refuse_overwrite_and_empty_input(self):
        analyze_batch([self.data], self.output)
        with self.assertRaisesRegex(ValueError, "覆盖"):
            analyze_batch([self.data], self.output)
        with self.assertRaises(ValueError):
            generate(self.data)
        empty = self.root / "empty"
        empty.mkdir()
        with self.assertRaises(ValueError):
            analyze_batch([empty], self.root / "other")

    def test_fail_on_findings_exit_code(self):
        result = self.run_cli("qa", "analyze", self.data / "missing.wav", "--out", self.output, "--fail-on-findings")
        self.assertEqual(result.returncode, 1)

    def test_help_and_invalid_cli(self):
        self.assertEqual(self.run_cli("--help").returncode, 0)
        self.assertEqual(self.run_cli("qa", "--help").returncode, 0)
        self.assertEqual(self.run_cli("qa", "analyze", self.data, "--out", self.output, "--timeout", "nan").returncode, 2)

    def test_unreviewed_rows_cannot_be_gold(self):
        analyze_batch([self.data], self.output)
        with self.assertRaisesRegex(ValueError, "没有人工复核"):
            promote(self.output / "review.csv", self.output / "results.jsonl", self.root / "gold.json", "synthetic")

    def test_manual_review_promotion_and_metrics(self):
        analyze_batch([self.data], self.output)
        rows = self.review_rows()
        # 测试夹具中的人工记录仅用于测试，不作为交付黄金集。
        selected = [row for row in rows if row["status"] == "NO_OUTPUT_CANDIDATE"][:1]
        selected += [row for row in rows if row["status"] == "OUTPUT_NEEDS_REVIEW"][:1]
        for row, decision in zip(selected, ("missing", "audible")):
            row.update(decision=decision, reviewer="test-reviewer", reviewed_at="2026-09-16T12:00:00+00:00")
        self.save_reviews(rows)
        result = self.run_cli("qa", "promote", self.output / "review.csv", "--results", self.output / "results.jsonl", "--out", self.root / "gold.json", "--dataset-kind", "synthetic")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_cli("qa", "evaluate", self.root / "gold.json", "--results", self.output / "results.jsonl", "--out", self.root / "metrics.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        metrics = read_json(self.root / "metrics.json")
        self.assertEqual(metrics["evaluated"], 2)
        self.assertEqual(metrics["counts"], {"tp": 1, "tn": 1, "fp": 0, "fn": 0, "excluded_or_uncertain": 0})
        self.assertEqual(metrics["dataset_kind"], "synthetic")

    def test_tampered_hash_and_missing_reviewer_rejected(self):
        analyze_batch([self.data], self.output)
        rows = self.review_rows()
        rows[0].update(decision="missing", reviewed_at="2026-09-16T12:00:00Z")
        self.save_reviews(rows)
        with self.assertRaisesRegex(ValueError, "reviewer"):
            promote(self.output / "review.csv", self.output / "results.jsonl", self.root / "gold.json", "synthetic")
        rows[0].update(reviewer="test-reviewer", audio_sha256="wrong")
        self.save_reviews(rows)
        with self.assertRaisesRegex(ValueError, "摘要"):
            promote(self.output / "review.csv", self.output / "results.jsonl", self.root / "gold.json", "synthetic")

    def test_time_mismatch_and_naive_timestamp_rejected(self):
        analyze_batch([self.data], self.output)
        rows = self.review_rows()
        rows[0].update(decision="missing", reviewer="test", reviewed_at="2026-09-16T12:00:00")
        self.save_reviews(rows)
        with self.assertRaisesRegex(ValueError, "时区"):
            promote(self.output / "review.csv", self.output / "results.jsonl", self.root / "gold.json", "synthetic")
        rows[0].update(reviewed_at="2026-09-16T12:00:00Z", at_s="99")
        self.save_reviews(rows)
        with self.assertRaisesRegex(ValueError, "窗口"):
            promote(self.output / "review.csv", self.output / "results.jsonl", self.root / "gold.json", "synthetic")

    def test_evaluation_refuses_missing_predictions(self):
        analyze_batch([self.data], self.output)
        rows = self.review_rows()
        rows[0].update(decision="missing", reviewer="test", reviewed_at="2026-09-16T12:00:00Z")
        self.save_reviews(rows)
        promote(self.output / "review.csv", self.output / "results.jsonl", self.root / "gold.json", "synthetic")
        empty = self.root / "empty.jsonl"
        empty.write_text("")
        with self.assertRaisesRegex(ValueError, "缺少对应预测"):
            evaluate(self.root / "gold.json", empty)

    def test_seed_and_hash_reproducibility(self):
        other = self.root / "again"
        generate(other)
        self.assertEqual(read_json(other / "manifest.json"), read_json(self.data / "manifest.json"))

    def test_duplicate_inputs_are_processed_once(self):
        result = analyze_batch([self.data / "missing.wav", self.data / "missing.wav"], self.output)
        self.assertEqual(result["files"], 1)

    def test_event_channels_used_unless_explicit_override(self):
        result = analyze_batch([self.data / "swapped.wav"], self.output, use_event_channel=False)
        self.assertEqual(result["errors"], 1)

    def test_noise_labeled_missing_counts_as_false_negative(self):
        analyze_batch([self.data / "noise.wav"], self.output)
        rows = self.review_rows()
        rows[0].update(decision="missing", reviewer="test", reviewed_at="2026-09-16T12:00:00Z")
        self.save_reviews(rows)
        promote(self.output / "review.csv", self.output / "results.jsonl", self.root / "gold.json", "synthetic")
        result = evaluate(self.root / "gold.json", self.output / "results.jsonl")
        self.assertEqual(result["counts"]["fn"], 1)
        self.assertEqual(result["recall"], 0)
        self.assertIsNone(result["precision"])

    def test_partial_review_and_duplicate_labels_rejected(self):
        analyze_batch([self.data], self.output)
        rows = self.review_rows()
        rows[0].update(notes="尚未试听")
        self.save_reviews(rows)
        with self.assertRaisesRegex(ValueError, "半成品"):
            promote(self.output / "review.csv", self.output / "results.jsonl", self.root / "gold.json", "synthetic")
        rows[0].update(decision="missing", reviewer="test", reviewed_at="2026-09-16T12:00:00Z")
        rows.append(dict(rows[0]))
        self.save_reviews(rows)
        with self.assertRaisesRegex(ValueError, "重复"):
            promote(self.output / "review.csv", self.output / "results.jsonl", self.root / "gold.json", "synthetic")

    def test_uncertain_only_labels_have_no_accuracy(self):
        analyze_batch([self.data], self.output)
        rows = self.review_rows()
        rows[0].update(decision="uncertain", reviewer="test", reviewed_at="2026-09-16T12:00:00Z")
        self.save_reviews(rows)
        promote(self.output / "review.csv", self.output / "results.jsonl", self.root / "gold.json", "synthetic")
        with self.assertRaisesRegex(ValueError, "没有可计分"):
            evaluate(self.root / "gold.json", self.output / "results.jsonl")

    def test_malformed_results_return_clear_error(self):
        analyze_batch([self.data], self.output)
        bad = self.root / "bad.jsonl"
        bad.write_text('[1,2,3]\n')
        result = self.run_cli("qa", "promote", self.output / "review.csv", "--results", bad, "--out", self.root / "gold.json", "--dataset-kind", "synthetic")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Traceback", result.stderr)
