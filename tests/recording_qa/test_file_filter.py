"""Directory selection contract shared by QA and output-gap review pages."""
from pathlib import Path
import subprocess
import unittest

from voice_tools.core.review.page import ASSETS


class FileFilterTests(unittest.TestCase):
    def test_directory_and_recording_selection(self):
        result = subprocess.run(
            ['node', '--test-reporter=tap', str(Path(__file__).with_name('file_filter.cjs')),
             str(ASSETS / 'file-filter.js')],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
