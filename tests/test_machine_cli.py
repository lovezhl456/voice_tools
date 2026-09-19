import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


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
        self.assertEqual(set(value["cli"]["commands"]), {"analyze", "generate", "freeze", "promote", "evaluate", "compare"})
        self.assertEqual(value["data_contracts"]["golden_write"], "1.1")
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

    def test_json_success_findings_and_partial_failure(self):
        process, value = self.command("--json", "qa", "generate", "--out", self.root / "data")
        self.assertEqual(process.returncode, 0)
        self.assertEqual(value["summary"]["cases"], 20)
        process, value = self.command("--json", "qa", "analyze", self.root / "data/missing.wav", "--out", self.root / "one", "--fail-on-findings")
        self.assertEqual(process.returncode, 1)
        self.assertTrue(value["ok"])
        self.assertEqual(value["status"], "findings")
        self.assertTrue(Path(value["artifacts"]["review.html"]).exists())
        (self.root / "data/broken.wav").write_bytes(b"bad")
        process, value = self.command("--json", "qa", "analyze", self.root / "data", "--out", self.root / "batch", "--fail-on-findings")
        self.assertEqual(process.returncode, 3)
        self.assertEqual(value["error"]["code"], "PARTIAL_FAILURE")
        self.assertEqual(value["summary"]["files"], 21)
        self.assertEqual(value["summary"]["errors"], 1)

    def test_homer_native_json_remains_usable(self):
        process, value = self.command("--json", "homer", "schema")
        self.assertEqual(process.returncode, 0)
        self.assertEqual(value["command"], "schema")
        self.assertIn("trace", value["commands"])
