"""验证工具集入口保留独立 HOMER CLI 的调用协议。"""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from voice_tools.cli import main
from voice_tools.tools.homer import client


ROOT = Path(__file__).resolve().parents[2]
COMMANDS = {"init", "login", "logout", "doctor", "fields", "schema",
            "search", "trace", "export", "analyze", "message"}


class Entrypoints(unittest.TestCase):
    def invoke(self, entrypoint, argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = entrypoint(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_schema_matches_legacy_entrypoint(self):
        expected = self.invoke(client.main, ["schema"])
        actual = self.invoke(main, ["homer", "schema"])
        self.assertEqual(actual, expected)
        self.assertEqual(set(json.loads(actual[1])["commands"]), COMMANDS)

    def test_offline_analysis_matches_legacy_entrypoint(self):
        args = ["analyze", "--input", str(ROOT / "examples/homer/sample-trace.json")]
        expected = self.invoke(client.main, args)
        actual = self.invoke(main, ["homer", *args])
        self.assertEqual(actual, expected)
        self.assertEqual(json.loads(actual[1])["fragmentation"]["verdict"], "unknown")

    def test_json_errors_survive_dispatch(self):
        for args in ([], ["unknown"], ["--unknown"], ["schema", "--unknown"],
                     ["search", "--limit", "bad"], ["search", "--cal", "1001"]):
            with self.subTest(args=args):
                code, stdout, stderr = self.invoke(main, ["homer", *args])
                self.assertEqual((code, stdout), (2, ""))
                self.assertEqual(json.loads(stderr)["error"]["exit_code"], 2)

    def test_global_options_on_both_sides_of_action(self):
        before = self.invoke(main, ["homer", "--url", "https://example.net", "schema"])
        after = self.invoke(main, ["homer", "schema", "--url", "https://example.net"])
        self.assertEqual(before, after)
        self.assertEqual(before[0], 0)

    def test_homer_runs_without_site_packages(self):
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
        cp = subprocess.run([sys.executable, "-S", "-m", "voice_tools", "homer", "schema"],
                            env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertEqual(set(json.loads(cp.stdout)["commands"]), COMMANDS)

    def test_ai_runner_schema_and_operator_option_boundary(self):
        runner = [sys.executable, str(ROOT / "examples/homer/ai_runner.py")]
        cp = subprocess.run([*runner, "schema"], capture_output=True, text=True, timeout=10)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertEqual(set(json.loads(cp.stdout)["result"]["commands"]), COMMANDS)
        for args in (["login"], ["schema", "--url=https://example.net"],
                     ["analyze", "--input", "private.json"]):
            with self.subTest(args=args):
                cp = subprocess.run([*runner, *args], capture_output=True, text=True, timeout=10)
                self.assertEqual(cp.returncode, 2, cp.stdout + cp.stderr)
                self.assertEqual(json.loads(cp.stdout)["exit_code"], 2)
