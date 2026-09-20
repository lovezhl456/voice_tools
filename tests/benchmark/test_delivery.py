import shutil
import tempfile
import unittest
from pathlib import Path

from tests.sip.fixtures import tone
from voice_tools.core.files import read_json, write_json
from voice_tools.tools.benchmark.templates import case
from voice_tools.tools.task import bundle, runner, review


class TimingDeliveryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.source = self.root / "source"
        (self.source / "audio").mkdir(parents=True)
        tone(self.source / "audio/interrupt.wav")
        scenario = case("interrupt", {"backend": "energy"})
        scenario["steps"][2]["speech"] = [{"start_s": .1, "end_s": .3}]
        write_json(self.source / "scenario.json", scenario)
        self.task = {"schema_version": "1.0", "id": "zh-delivery", "title": "中文时序迁移",
            "inputs": {"scenario": "scenario.json"}, "steps": [{"id": "validate", "tool": "sip", "action": "validate",
            "params": {"scenario": {"input": "scenario"}}}]}

    def pack(self):
        write_json(self.source / "task.json", self.task)
        archive = self.root / "task.vtask.zip"
        bundle.pack(self.source / "task.json", self.source, archive)
        return archive

    def test_annotation_and_audio_survive_migration(self):
        archive = self.pack()
        shutil.rmtree(self.source)
        checked = runner.check(archive)
        self.assertTrue(checked["ready"], checked)
        self.assertFalse(checked["network_accessed"])
        result = runner.run(archive, self.root / "run")
        self.assertEqual(result["status"], "completed", result)
        migrated = read_json(self.root / "run/work/inputs/scenario.json")
        self.assertEqual(migrated["steps"][2]["speech"], [{"start_s": .1, "end_s": .3}])
        media = self.root / "run/work/inputs" / migrated["steps"][2]["file"]
        self.assertTrue(media.is_file())

    def test_network_disabled_blocks_actual_call(self):
        self.task["steps"][0]["action"] = "run"
        archive = self.pack()
        result = runner.check(archive)
        self.assertFalse(result["ready"])
        self.assertTrue(any("网络" in x for x in result["issues"]))
        self.assertFalse(result["network_accessed"])

    def test_new_workbench_contains_editor_and_offline_catalog(self):
        output = self.root / "workbench"
        review.workbench(output)
        html = (output / "index.html").read_text()
        self.assertIn('"benchmark.analyze"', html)
        self.assertIn('"benchmark_templates"', html)
        self.assertTrue((output / "benchmark.js").is_file())

    def test_offline_cli_never_imports_native_modules(self):
        import subprocess
        import sys
        script = '''
import importlib.abc, sys
class WithoutNative(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ('pjsua2', 'webrtcvad'):
            raise ImportError('native intentionally unavailable')
sys.meta_path.insert(0, WithoutNative())
from voice_tools.cli import main
raise SystemExit(main(sys.argv[1:]))
'''
        commands = [
            ["schema", "--tool", "benchmark"],
            ["benchmark", "init", "--out", str(self.root / "offline")],
            ["sip", "validate", str(self.source / "scenario.json")],
            ["sip", "run", str(self.source / "scenario.json"), "--dry-run", "--out", str(self.root / "preview")],
        ]
        for command in commands:
            result = subprocess.run([sys.executable, "-c", script] + command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
