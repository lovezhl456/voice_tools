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


ROOT = Path(__file__).resolve().parents[2]
COMMANDS = {"init", "login", "logout", "doctor", "fields", "schema",
            "search", "trace", "export", "analyze", "message"}


class Entrypoints(unittest.TestCase):
    def invoke(self, entrypoint, argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = entrypoint(argv)
        return code, stdout.getvalue(), stderr.getvalue()


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
