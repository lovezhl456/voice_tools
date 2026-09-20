"""安装器的文件保护与超时测试，不联网也不伪造已完成原生安装。"""
import hashlib
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('visqol_installer', Path(__file__).resolve().parents[2] / 'scripts/install_visqol.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_qemu_workaround_rejects_unknown_binary_before_compiling(self):
        binary = self.root / 'unverified-bazel'
        binary.write_bytes(b'not the official release')
        with patch.object(installer.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, '官方 Bazel'):
                installer.qemu_bazel_launcher(binary, self.root)
            run.assert_not_called()
        self.assertEqual(binary.read_bytes(), b'not the official release')

    def test_bazel_startup_error_keeps_diagnostic_without_implicit_workaround(self):
        error = "Failed to open '/proc/self/exe' as a zip file: Bad file descriptor"
        result = subprocess.CompletedProcess(['bazel'], 36, '', error)
        with patch.object(installer.subprocess, 'run', return_value=result), \
                patch.object(installer, 'qemu_bazel_launcher') as fallback:
            with self.assertRaisesRegex(ValueError, 'Bad file descriptor'):
                installer.check_bazel(Path('/bazel'), self.root)
            fallback.assert_not_called()

    def test_qemu_option_does_not_mask_unrelated_startup_error(self):
        result = subprocess.CompletedProcess(['bazel'], 7, '', 'unrelated failure')
        with patch.object(installer.subprocess, 'run', return_value=result), \
                patch.object(installer.platform, 'system', return_value='Linux'), \
                patch.object(installer, 'qemu_bazel_launcher') as fallback:
            with self.assertRaisesRegex(ValueError, 'unrelated failure'):
                installer.check_bazel(Path('/bazel'), self.root, allow_qemu=True)
            fallback.assert_not_called()

    def archive(self, entries):
        dest = self.root / 'source.tar.gz'
        with tarfile.open(dest, 'w:gz') as bundle:
            for name, payload, kind in entries:
                item = tarfile.TarInfo(name)
                item.type = kind
                item.size = len(payload) if kind == tarfile.REGTYPE else 0
                if kind == tarfile.SYMTYPE:item.linkname = '/tmp/elsewhere'
                bundle.addfile(item, io.BytesIO(payload) if kind == tarfile.REGTYPE else None)
        return dest

    def test_verified_archive_resume_and_local_edit_protection(self):
        archive = self.archive([('visqol-' + installer.COMMIT + '/src/code.cc', b'original', tarfile.REGTYPE)])
        with patch.object(installer, 'SOURCE_SHA256', installer.digest(archive)):
            source = self.root / 'source'
            installer.extract_verified(archive, source)
            installer.extract_verified(archive, source)
            target = source / 'src/code.cc'; target.write_text('user edit')
            with self.assertRaisesRegex(ValueError, '不覆盖'):
                installer.extract_verified(archive, source)
            self.assertEqual(target.read_text(), 'user edit')

    def test_checksum_failure_never_extracts(self):
        archive = self.archive([])
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            installer.extract_verified(archive, self.root / 'source')
        self.assertFalse((self.root / 'source').exists())

    def test_traversal_and_symlink_members_are_rejected(self):
        for entry in [('visqol-' + installer.COMMIT + '/../escape', b'x', tarfile.REGTYPE),
                      ('/absolute', b'x', tarfile.REGTYPE),
                      ('visqol-' + installer.COMMIT + '/link', b'', tarfile.SYMTYPE)]:
            archive = self.archive([entry])
            with patch.object(installer, 'SOURCE_SHA256', installer.digest(archive)):
                with self.assertRaises(ValueError):installer.extract_verified(archive, self.root / 'source')
        self.assertFalse((self.root / 'escape').exists())

    def test_unknown_source_files_are_not_compiled_or_removed(self):
        archive = self.archive([('visqol-' + installer.COMMIT + '/known', b'ok', tarfile.REGTYPE)])
        source = self.root / 'source'; source.mkdir(); (source / 'user.cc').write_text('keep')
        with patch.object(installer, 'SOURCE_SHA256', installer.digest(archive)):
            with self.assertRaisesRegex(ValueError, '非归档'):
                installer.extract_verified(archive, source)
        self.assertEqual((source / 'user.cc').read_text(), 'keep')

    def test_valid_cached_download_needs_no_network(self):
        p = self.root / 'cache'; p.write_bytes(b'verified')
        with patch.object(installer.subprocess, 'run') as run:
            installer.download('https://example.invalid/file', p, installer.digest(p))
            run.assert_not_called()
        with self.assertRaisesRegex(ValueError, '不覆盖'):
            installer.download('https://example.invalid/file', p, '0' * 64)
        self.assertEqual(p.read_bytes(), b'verified')

    def test_tflite_patch_is_exact_and_idempotent(self):
        target = self.root / 'bazel-cache/unit/external/org_tensorflow/tensorflow/lite/kernels/elementwise.cc'
        target.parent.mkdir(parents=True)
        original = b'prefix\n' + installer.TF_ABS_OLD + b'\nsuffix'
        target.write_bytes(original)
        expected = hashlib.sha256(original).hexdigest()
        with patch.object(installer, 'TF_ELEMENTWISE_SHA256', expected):
            first = installer.patch_tflite_for_mac(self.root)
            second = installer.patch_tflite_for_mac(self.root)
            self.assertEqual(first, second)
            self.assertIn(installer.TF_ABS_NEW, target.read_bytes())
            self.assertNotIn(installer.TF_ABS_OLD, target.read_bytes())

    def test_tflite_unknown_edits_are_preserved(self):
        target = self.root / 'bazel-cache/unit/external/org_tensorflow/tensorflow/lite/kernels/elementwise.cc'
        target.parent.mkdir(parents=True)
        target.write_bytes(b'user modification ' + installer.TF_ABS_OLD)
        before = target.read_bytes()
        with self.assertRaisesRegex(ValueError, '固定版本'):
            installer.patch_tflite_for_mac(self.root)
        self.assertEqual(target.read_bytes(), before)

    def test_build_timeout_and_nonzero_are_preserved(self):
        with (self.root / 'log').open('wb') as log:
            with self.assertRaises(subprocess.TimeoutExpired):
                installer.run_build([sys.executable, '-c', 'import time;time.sleep(20)'], self.root, None, log, .1)
            with self.assertRaises(subprocess.CalledProcessError):
                installer.run_build([sys.executable, '-c', 'raise SystemExit(7)'], self.root, None, log, 10)


if __name__ == '__main__':unittest.main()
