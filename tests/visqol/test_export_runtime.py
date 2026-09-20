"""导出文件保护测试；使用假后端，不替代真实 Linux 镜像评分。"""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from voice_tools.core.files import sha256
from voice_tools.tools.visqol.service import MODELS, require_backend

spec = importlib.util.spec_from_file_location(
    "export_runtime", Path(__file__).resolve().parents[2] / "scripts/visqol_export_runtime.py")
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


class RuntimeExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.cache = self.root / "build-cache"
        self.cache.mkdir()
        (self.source / "model").mkdir(parents=True)
        (self.source / "bazel-bin").symlink_to(self.cache, target_is_directory=True)
        (self.cache / "visqol").write_text("#!/bin/sh\nexit 0\n")
        (self.cache / "visqol").chmod(0o755)
        for name in MODELS.values():
            (self.source / "model" / name).write_text("fixture model")
        (self.source / "LICENSE").write_text("fixture license")
        (self.root / "installation.json").write_text(json.dumps({"status": "built", "fixture": True}))
        self.output = self.root / "bundle"

    def run_export(self):
        return exporter.main(["--visqol-dir", str(self.source), "--out", str(self.output)])

    def test_bundle_uses_regular_files_and_survives_build_cache_removal(self):
        expected = sha256(self.cache / "visqol")
        self.assertEqual(self.run_export(), 0)
        binary = self.output / "source/bazel-bin/visqol"
        self.assertFalse(binary.is_symlink())
        self.assertEqual(sha256(binary), expected)
        shutil.rmtree(self.cache)
        self.assertTrue(require_backend(self.output / "source", "speech")["ready"])
        self.assertTrue(require_backend(self.output / "source", "audio")["ready"])
        self.assertEqual((self.output / "source/LICENSE").read_text(), "fixture license")
        record = json.loads((self.output / "installation.json").read_text())
        self.assertEqual(record["source_installation"]["fixture"], True)

    def test_existing_results_are_preserved(self):
        self.output.mkdir()
        (self.output / "keep").write_text("user data")
        with self.assertRaises(ValueError):
            self.run_export()
        self.assertEqual((self.output / "keep").read_text(), "user data")

    def test_missing_audio_model_fails_before_export(self):
        (self.source / "model" / MODELS["audio"]).unlink()
        with self.assertRaises(ValueError):
            self.run_export()
        self.assertFalse(self.output.exists())
