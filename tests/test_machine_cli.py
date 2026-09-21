import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tests.recording_qa.fixtures import write_case


class MachineCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def command(self, *args):
        process = subprocess.run([sys.executable, "-m", "voice_tools", *map(str, args)], capture_output=True, text=True)
        value = json.loads(process.stdout)
        return process, value

    def test_schema_is_offline_and_covers_actual_p0_commands(self):
        process, value = self.command("schema", "--tool", "qa")
        self.assertEqual(process.returncode, 0)
        self.assertEqual(set(value["cli"]["commands"]), {
            "analyze", "generate", "freeze", "promote", "evaluate", "compare",
            "assess", "model-download", "model-doctor", "assess-check", "assess-evaluate"})
        self.assertEqual(value["data_contracts"]["golden_write"], "1.1")
        self.assertEqual(value["data_contracts"]["qa_assessment"], "1.0")
        process, value = self.command("schema", "--tool", "homer")
        self.assertIn("trace", value["cli"]["commands"])

    def test_json_errors_are_one_parseable_object(self):
        for args in (("qa", "analyze"), ("audio", "inspect", self.root / "missing", "--out", self.root / "out"), ("unknown",)):
            with self.subTest(args=args):
                process, value = self.command("--json", *args)
                self.assertEqual(process.returncode, 2)
                self.assertFalse(value["ok"])
                self.assertEqual(value["error"]["code"], "INVALID_INPUT")
                self.assertNotIn("Traceback", process.stderr)

    def test_json_generator_preserves_all_scenarios(self):
        process, value = self.command("--json", "qa", "generate", "--out", self.root / "data")
        self.assertEqual(process.returncode, 0)
        self.assertEqual(value["summary"]["cases"], 20)
        self.assertEqual(len(list((self.root / "data").glob("*.wav"))), 20)

    def test_analysis_exit_codes_in_text_and_json(self):
        source = write_case(self.root / "data", "missing")
        (source.parent / "broken.wav").write_bytes(b"bad")
        cases = [
            ("completed", 0, ["qa", "analyze", source]),
            ("findings", 1, ["qa", "analyze", source, "--fail-on-findings"]),
            ("invalid", 2, ["qa", "analyze"]),
            ("partial_error", 3, ["qa", "analyze", source.parent, "--fail-on-findings"]),
        ]
        for mode in ("text", "json"):
            for status, code, args in cases:
                with self.subTest(mode=mode, status=status):
                    output = self.root / (mode + "-" + status)
                    prefix = ["--json"] if mode == "json" else []
                    argv = [*prefix, *args, "--out", output]
                    process = subprocess.run([sys.executable, "-m", "voice_tools", *map(str, argv)],
                                             capture_output=True, text=True)
                    self.assertEqual(process.returncode, code, process.stdout + process.stderr)
                    self.assertNotIn("Traceback", process.stderr)
                    if code == 2:
                        self.assertFalse(output.exists())
                    else:
                        self.assertTrue((output / "review.html").is_file())
                        records = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
                        good = next(row for row in records if Path(row["input"]) == source.resolve())
                        self.assertEqual(good["result"]["opportunities"][0]["status"], "NO_OUTPUT_CANDIDATE")
                    if mode == "json":
                        value = json.loads(process.stdout)
                        self.assertEqual(value["exit_code"], code)
                        self.assertEqual(value["ok"], code in (0, 1))
                        self.assertEqual(value["status"], "error" if code == 2 else status)
                        if code == 2:
                            self.assertEqual(value["error"]["code"], "INVALID_INPUT")
                        else:
                            self.assertEqual(Path(value["artifacts"]["review.html"]), (output / "review.html").resolve())
                        if code == 3:
                            self.assertEqual(value["error"]["code"], "PARTIAL_FAILURE")
                            self.assertEqual((value["summary"]["files"], value["summary"]["errors"]), (2, 1))

    def test_homer_native_json_remains_usable(self):
        process, value = self.command("--json", "homer", "schema")
        self.assertEqual(process.returncode, 0)
        self.assertEqual(value["command"], "schema")
        self.assertIn("trace", value["commands"])

    def test_whole_recording_cli_has_fail_closed_and_partial_exit_codes(self):
        source = write_case(self.root / 'calls', 'missing')
        output = self.root / 'assessment'
        process, value = self.command('--json', 'qa', 'assess', source, '--rules-only', '--out', output)
        self.assertEqual(process.returncode, 1, process.stderr)
        self.assertEqual(value['summary']['decisions']['NEEDS_REVIEW'], 1)
        self.assertEqual(value['summary']['decisions']['AUTO_PASS'], 0)
        self.assertTrue((output / 'recording-review.csv').is_file())
        (source.parent / 'bad.wav').write_bytes(b'broken')
        process, value = self.command('--json', 'qa', 'assess', source.parent, '--rules-only', '--out', self.root / 'partial')
        self.assertEqual(process.returncode, 3)
        self.assertEqual(value['summary']['errors'], 1)
        self.assertEqual(value['summary']['decisions']['NEEDS_REVIEW'], 2)
        process, value = self.command('--json', 'qa', 'assess', source, '--rules-only', '--timeout', '-1', '--out', self.root / 'invalid')
        self.assertEqual(process.returncode, 2)
        self.assertFalse((self.root / 'invalid').exists())
        process, value = self.command('--json', 'qa', 'model-doctor', '--model-dir', self.root / 'missing-model')
        self.assertEqual(process.returncode, 1)
        self.assertFalse(value['summary']['ready'])

    def test_whole_recording_review_cli_does_not_invent_human_labels(self):
        source = write_case(self.root / 'calls', 'missing')
        output = self.root / 'assessment'
        self.command('--json', 'qa', 'assess', source, '--rules-only', '--out', output)
        args = [output / 'recording-review.csv', '--results', output / 'assessment.jsonl']
        process, value = self.command('--json', 'qa', 'assess-check', *args)
        self.assertEqual(process.returncode, 0)
        self.assertEqual(value['summary']['human_labels'], 0)
        process, value = self.command('--json', 'qa', 'assess-evaluate', *args,
                                      '--dataset-kind', 'synthetic', '--out', self.root / 'metrics.json')
        self.assertEqual(process.returncode, 0)
        self.assertEqual(value['summary']['human_coverage'], 0)
        self.assertIsNone(value['summary']['automatic_pass_error_on_reviewed'])
