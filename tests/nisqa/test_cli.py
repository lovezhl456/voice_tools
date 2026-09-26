import contextlib
import importlib.metadata
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from voice_tools.cli import main
from voice_tools.tools.nisqa.backend import doctor


class CliTests(unittest.TestCase):
    def test_discovery_imports_no_optional_runtime(self):
        code = '''
import sys
from voice_tools.cli import main
main(["schema", "--tool", "nisqa"])
assert not {"torch", "torchmetrics", "librosa", "soundfile"}.intersection(sys.modules)
'''
        process = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        value = json.loads(process.stdout)
        self.assertEqual(set(value["cli"]["commands"]), {"download", "doctor", "analyze"})

    def test_missing_dependencies_doctor_is_offline(self):
        with tempfile.TemporaryDirectory() as directory, patch("importlib.metadata.version", side_effect=importlib.metadata.PackageNotFoundError):
            result = doctor(directory)
        self.assertFalse(result["ready"])
        self.assertFalse(any(result["versions"].values()))
        self.assertFalse(result["model"]["valid"])

    def test_doctor_json_reports_not_ready_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()) as output:
            code = main(["--json", "nisqa", "doctor", "--model-dir", directory])
        self.assertEqual(code, 1)
        result = json.loads(output.getvalue())
        self.assertFalse(result["summary"]["ready"])

    def test_invalid_options_and_missing_input_json(self):
        with tempfile.TemporaryDirectory() as root:
            for arguments in (["analyze"], ["analyze", root, "--out", str(Path(root) / "out"), "--segment-seconds", "nan"]):
                with self.subTest(arguments=arguments), contextlib.redirect_stdout(io.StringIO()) as output:
                    code = main(["--json", "nisqa", *arguments])
                self.assertEqual(code, 2)
                self.assertEqual(json.loads(output.getvalue())["error"]["code"], "INVALID_INPUT")
