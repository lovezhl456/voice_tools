"""Exercise the shared renderer and its two packaging paths without an engine."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from voice_tools.core.files import write_json
from voice_tools.tools.latency import report
from voice_tools.tools.task import bundle, review


class WebTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node 18+ is required for renderer tests')
    def test_renderer_numeric_validation_and_escaping(self):
        result = subprocess.run(
            ['node', '--test-reporter=tap', str(Path(__file__).with_name('web_renderer.cjs')),
             str(report.WEB / 'latency.js')],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('# pass 16', result.stdout)

    def test_checksum_valid_bundle_and_standalone_use_current_renderer(self):
        payload = '<img src="missing.png" onerror="document.body.dataset.latencyXss=1">'
        data = {
            'kind': 'latency_run', 'schema_version': '1.0',
            'coverage': {'paired': payload},
        }
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source'
            source.mkdir()
            write_json(source / 'receipt.json', {
                'kind': 'task_run', 'steps': [{'id': 'measure', 'tool': 'latency'}],
            })
            write_json(source / 'summary.json', data)
            package = root / 'result.zip'
            bundle.create_archive(package, {
                'run.json': source / 'receipt.json',
                'steps/measure/run.json': source / 'summary.json',
            }, 'voice_result')
            output = root / 'review'
            review.review(package, output)  # Verifies the complete manifest.
            html = (output / 'index.html').read_text()
            embedded = json.loads(html.split('window.VT_DATA=', 1)[1].split(';</script>', 1)[0])
            self.assertEqual(embedded['steps'][0]['latency_summary'], data)
            self.assertNotIn('<img', html)
            self.assertEqual((output / 'latency.js').read_bytes(), (report.WEB / 'latency.js').read_bytes())
            standalone = root / 'standalone'
            standalone.mkdir()
            report.render(standalone, data, [])
            self.assertNotIn('<img', (standalone / 'index.html').read_text())
            self.assertEqual((standalone / 'latency.js').read_bytes(), (report.WEB / 'latency.js').read_bytes())
