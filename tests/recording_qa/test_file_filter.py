"""Directory selection contract shared by QA and output-gap review pages."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from voice_tools.core.review.page import ASSETS, render_page


class FileFilterTests(unittest.TestCase):
    def test_directory_and_recording_selection(self):
        result = subprocess.run(
            ['node', '--test-reporter=tap', str(Path(__file__).with_name('file_filter.cjs')),
             str(ASSETS / 'file-filter.js')],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_shared_page_embeds_filter_before_business_script(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'review.html'
            render_page(path, {'records': []}, '/* business script */')
            html = path.read_text()
        self.assertIn((ASSETS / 'file-filter.js').read_text(), html)
        self.assertLess(html.index('id="directoryFilter"'), html.index('id="fileFilter"'))
        self.assertLess(html.index('function createReviewFileFilter'), html.index('/* business script */'))
        self.assertNotIn('__FILE_FILTER_SCRIPT__', html)
