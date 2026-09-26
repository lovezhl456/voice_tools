"""A published timeline must describe the bytes read from every grouped PCAP."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from voice_tools.core.files import read_json, sha256
from voice_tools.core.rtp_timeline import TimelineWriter
from voice_tools.tools.report.service import build


class TimelineProvenanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.sources = [self.root / name for name in ('first.pcap', 'second.pcap')]
        for index, source in enumerate(self.sources):
            source.write_bytes(bytes([index]) * 32)

    def run_report(self, mutate=None, enabled=True):
        def analyze(*args, timeline_output=None):
            result = {'first_epoch': 100, 'last_epoch': 101, 'packet_limit_reached': False,
                      'truncated_packets': 0, 'out_of_order_capture_timestamps': 0,
                      'media': {'audio_bytes': 0}}
            if timeline_output is not None:
                writer = TimelineWriter(timeline_output)
                writer.write({'epoch': 100})
                result['timeline'] = writer.finish(result)
            if mutate:
                mutate()
            return result

        with patch('voice_tools.tools.report.service.pcap.analyze_group', side_effect=analyze), \
                patch('voice_tools.tools.report.service.render', return_value='report'):
            return build(self.root / 'report', pcap_groups=[('edge', self.sources)], rtp_timeline=enabled)

    def test_unchanged_group_publishes_original_hashes(self):
        expected = [{'name': p.name, 'sha256': sha256(p)} for p in self.sources]
        result = self.run_report()
        self.assertFalse(result['partial'])
        manifest = read_json(self.root / 'report' / result['pcaps'][0]['rtp_timeline'])
        self.assertTrue(manifest['complete'])
        self.assertEqual(manifest['sources'], expected)

    def test_changed_second_source_does_not_publish_manifest(self):
        result = self.run_report(lambda: self.sources[1].write_bytes(b'replaced capture'))
        self.assertTrue(result['partial'])
        self.assertIn('PCAP', result['pcaps'][0]['error'])
        self.assertNotIn('rtp_timeline', result['pcaps'][0])
        self.assertEqual(list((self.root / 'report').rglob('timeline.json')), [])
        self.assertTrue((self.root / 'report/report.json').is_file())

    def test_removed_source_keeps_error_report_without_timeline(self):
        result = self.run_report(self.sources[0].unlink)
        self.assertEqual(result['errors'], 1)
        self.assertEqual(list((self.root / 'report').rglob('timeline.json')), [])
