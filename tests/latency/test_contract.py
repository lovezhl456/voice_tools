from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from voice_tools.tools.latency.contract import validate_parameters
from voice_tools.tools.latency.runtime import doctor, invoke, RESOURCES
from voice_tools.tools.latency.service import distribution, process
from voice_tools.tools.latency.install import installation_lock


class ContractTests(unittest.TestCase):

    def test_effective_values_and_rejections(self):
        values,sources=validate_parameters({'energy_threshold':100})
        self.assertEqual(values['energy_threshold'],100)
        self.assertEqual(sources['energy_threshold'],'explicit')
        self.assertEqual(sources['min_silence_ms'],'default')
        for value in (float('nan'),float('inf'),-1,False):
            with self.assertRaises(ValueError): validate_parameters({'energy_threshold':value})
        with self.assertRaises(ValueError): validate_parameters({'min_silence_ms':21})

    def test_empty_measurements_and_pooled_percentile(self):
        self.assertIsNone(distribution([])['median_s'])
        self.assertAlmostEqual(distribution([.1,.2,.3,10])['p95_s'],8.545)


    def test_missing_dependency_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            self.assertFalse(doctor(root/'missing')['ready'])
            with self.assertRaises(ValueError): process([root/'no.wav'],root/'out',system_channel='right',directory=root/'missing')
            self.assertFalse((root/'out').exists())

    def test_install_lock_refuses_concurrent_attempt(self):
        with tempfile.TemporaryDirectory() as folder:
            dest=Path(folder)/'prefix'
            with installation_lock(dest):
                with self.assertRaises(ValueError):
                    with installation_lock(dest): pass
            self.assertFalse(dest.with_name('prefix.install.lock').exists())

    def test_hash_lock_rejects_same_version_repacked_wheel(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            wheel = root / 'numpy-2.2.6-py3-none-any.whl'
            with zipfile.ZipFile(wheel, 'w') as archive:
                archive.writestr('numpy/__init__.py', '__version__ = "2.2.6"\n')
                archive.writestr('numpy-2.2.6.dist-info/METADATA',
                                 'Metadata-Version: 2.1\nName: numpy\nVersion: 2.2.6\n')
                archive.writestr('numpy-2.2.6.dist-info/WHEEL',
                                 'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
                archive.writestr('numpy-2.2.6.dist-info/RECORD', '')
            result = subprocess.run(
                [sys.executable, '-m', 'pip', '--isolated', 'download', '--no-index',
                 '--find-links', str(root), '--only-binary=:all:', '--require-hashes',
                 '-r', str(RESOURCES / 'requirements.txt'), '--dest', str(root / 'downloads')],
                capture_output=True, text=True, timeout=30,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('DO NOT MATCH THE HASHES', result.stderr)
            self.assertFalse(list((root / 'downloads').glob('*.whl')))


    def test_corrupt_ready_is_a_dependency_error(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'ready.json').write_text('[]')
            info = doctor(root)
            self.assertFalse(info['ready'])
            self.assertIn('JSON 对象', info['issues'][0])

    def test_engine_crash_is_not_silent_success(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'venv/bin').mkdir(parents=True)
            (root / 'venv/bin/python').symlink_to(sys.executable)
            (root / 'worker.py').write_text('import sys; sys.stderr.write("crash evidence"); sys.exit(9)')
            with self.assertRaisesRegex(ValueError, 'crash evidence'):
                invoke(root, [], 2)

    def test_timeout_kills_engine_and_descendant(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'venv/bin').mkdir(parents=True)
            (root/'venv/bin/python').symlink_to(sys.executable)
            (root/'worker.py').write_text("import subprocess,sys,time\nfrom pathlib import Path\nc=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)'])\nPath(sys.argv[1]+'/pid').write_text(str(c.pid))\ntime.sleep(120)\n")
            with self.assertRaises(subprocess.TimeoutExpired): invoke(root,[],.5)
            pid=(root/'pid').read_text()
            result=subprocess.run(['ps','-o','stat=','-p',pid],capture_output=True,text=True)
            self.assertTrue(not result.stdout.strip() or result.stdout.strip().startswith('Z'),result.stdout)
