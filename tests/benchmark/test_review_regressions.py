"""PR #25: exercise invalid-input envelopes and real task dependency execution."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from voice_tools.core.files import read_json, write_json
from voice_tools.tools.benchmark.analysis import analyze
from voice_tools.tools.benchmark.config import number
from voice_tools.tools.benchmark.summary import batch_jobs
from voice_tools.tools.benchmark.templates import case
from voice_tools.tools.sip.scenario import template
from voice_tools.tools.task import bundle, runner
from tests.benchmark.fixtures import write_evidence


class ReviewRegressionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def command(self, *args):
        result = subprocess.run([sys.executable, "-m", "voice_tools", "--json", *map(str, args)],
                                capture_output=True, text=True, timeout=30)
        self.assertNotIn("Traceback", result.stderr)
        return result.returncode, json.loads(result.stdout)

    def receipt(self, jobs=None):
        if jobs is None:
            jobs = [{"id": "job-1", "status": "completed", "output": "job-1/run"}]
        return {"schema_version": "1.0", "kind": "sip_batch_result", "status": "completed", "jobs": jobs}

    def test_findings_do_not_skip_dependent_task_steps(self):
        for action in ("analyze", "summarize"):
            with self.subTest(action=action):
                source = self.root / action / "source"
                evidence = source / "input" if action == "analyze" else source / "input/job-1/run"
                write_evidence(evidence)
                if action == "summarize":
                    write_json(source / "input/batch-result.json", self.receipt())
                parameter = "run_dir" if action == "analyze" else "batch_dir"
                task = {"schema_version": "1.0", "id": "findings-flow", "title": "声学超限后继续复查",
                        "inputs": {"evidence": "input"}, "steps": [
                            {"id": "measure", "tool": "benchmark", "action": action,
                             "params": {parameter: {"input": "evidence"}}},
                            {"id": "after", "tool": "benchmark", "action": "init", "depends_on": ["measure"], "params": {}}]}
                write_json(source / "task.json", task)
                archive = self.root / action / "task.vtask.zip"
                bundle.pack(source / "task.json", source, archive)
                result = runner.run(archive, self.root / action / "execution")
                self.assertEqual(result["status"], "findings", result)
                self.assertEqual([step["status"] for step in result["steps"]], ["findings", "completed"])
                self.assertEqual(result["steps"][0]["exit_code"], 1)
                self.assertTrue((self.root / action / "execution/steps/after/data/recording-checklist.json").is_file())

    def test_classification_keeps_actual_failures_and_partial_results(self):
        for action in ("analyze", "summarize"):
            findings = {"ok": True, "status": "findings", "exit_code": 1}
            self.assertEqual(runner.classify("benchmark", action, 1, findings), "findings")
            for payload in (None, [], {}, {"ok": False, "status": "error", "exit_code": 1},
                            dict(findings, ok=False), dict(findings, status="error"), dict(findings, exit_code=2)):
                with self.subTest(action=action, payload=payload):
                    self.assertEqual(runner.classify("benchmark", action, 1, payload), "failed")
            self.assertEqual(runner.classify("benchmark", action, 2, {}), "failed")
            self.assertEqual(runner.classify("benchmark", action, 3, {}), "partial")
        self.assertEqual(runner.classify("benchmark", "init", 1, {}), "failed")

    def test_all_numeric_rejections_are_value_errors(self):
        for value in (10**400, -(10**400), float("inf"), -float("inf"), float("nan"), True, "20", None):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    number(value, 20, 1000, "minimum_ms")
        for value in (20, 60.5, 1000):
            self.assertEqual(number(value, 20, 1000, "minimum_ms"), value)

    def test_oversized_json_numbers_keep_invalid_input_envelope(self):
        overrides = [
            {"detector": {"minimum_ms": 10**400}},
            {"detector": {"threshold_db": -(10**400)}},
            {"windows": [{"id": "opening", "start_s": 0, "end_s": 10**400}]},
            {"expectations": {"first_audio_max_ms": 10**400}},
        ]
        for override in overrides:
            scenario = case("greeting")
            scenario["benchmark"].update(override)
            write_json(self.root / "scenario.json", scenario)
            code, payload = self.command("sip", "validate", self.root / "scenario.json")
            self.assertEqual(code, 2)
            self.assertEqual(payload["error"]["code"], "INVALID_INPUT")

    def test_oversized_frame_time_is_insufficient_evidence(self):
        write_evidence(self.root / "call")
        frames = self.root / "call/media-frames.jsonl"
        rows = [json.loads(line) for line in frames.read_text().splitlines()]
        rows[0]["at_s"] = 10**400
        frames.write_text("".join(json.dumps(row) + "\n" for row in rows))
        report = analyze(self.root / "call")
        self.assertEqual(report["status"], "insufficient_evidence")
        self.assertEqual(report["metrics"], [])

    def test_rejects_wrong_and_incomplete_batch_receipts(self):
        invalid = [[], {}, self.receipt([])]
        valid = self.receipt()
        invalid.append({key: value for key, value in valid.items() if key != "jobs"})
        invalid.extend(dict(valid, **change) for change in (
            {"schema_version": "2.0"}, {"kind": "voice_benchmark"}, {"status": "unknown"},
            {"jobs": None}, {"jobs": "bad"}, {"jobs": [None]}, {"jobs": [{}]},
            {"jobs": [{"id": "job-1", "status": "completed"}]},
            {"jobs": [{"id": "job-1", "status": "failed", "output": 12}]},
            {"jobs": [valid["jobs"][0], valid["jobs"][0]]},
        ))
        path = self.root / "batch-result.json"
        for receipt in invalid:
            with self.subTest(receipt=receipt):
                write_json(path, receipt)
                with self.assertRaises(ValueError):
                    batch_jobs(path)

    def test_invalid_batch_cli_cannot_emit_successful_summary(self):
        write_json(self.root / "batch-result.json", {})
        output = self.root / "summary"
        code, payload = self.command("benchmark", "summarize", self.root, "--out", output)
        self.assertEqual(code, 2)
        self.assertEqual(payload["error"]["code"], "INVALID_INPUT")
        self.assertFalse((output / "benchmark-summary.json").exists())

    def test_cancelled_and_missing_evidence_jobs_stay_in_denominator(self):
        receipt = self.receipt([
            {"id": "cancelled", "status": "cancelled"},
            {"id": "failed", "status": "failed"},
            {"id": "missing", "status": "completed", "output": "missing/run"},
        ])
        receipt["status"] = "interrupted"
        write_json(self.root / "batch-result.json", receipt)
        code, payload = self.command("benchmark", "summarize", self.root, "--out", self.root / "summary")
        self.assertEqual(code, 3)
        self.assertEqual(payload["summary"]["calls"], 3)
        self.assertEqual(payload["summary"]["counts"], {"valid": 0, "failed": 2, "invalid": 0, "insufficient_evidence": 1})

    def test_runtime_and_snapshot_describe_supported_scenario_versions(self):
        code, schema = self.command("schema", "--tool", "benchmark")
        self.assertEqual(code, 0)
        contracts = schema["data_contracts"]
        self.assertEqual(contracts["sip_scenario_read"], ["1.0", "1.1"])
        self.assertEqual(contracts["sip_scenario_write"], template()["schema_version"])
        self.assertEqual(contracts["sip_scenario"], contracts["sip_scenario_write"])
        self.assertEqual(contracts["benchmark_scenario_write"], case("greeting")["schema_version"])
        self.assertIn("benchmark", schema["exit_codes"]["1"])
        snapshot = read_json(Path(__file__).resolve().parents[2] / "docs/cli-schema.json")
        self.assertEqual(snapshot["data_contracts"], contracts)
