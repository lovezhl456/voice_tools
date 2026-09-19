import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from voice_tools.core.files import read_json, write_json
from voice_tools.tools.sip.batch import load_queue, run_queue
from voice_tools.tools.sip.load import export_package, run_load, validate_request, compile_xml, dtmf_pcap
from voice_tools.tools.sip.scenario import template


class BatchLoadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.spec = template(); self.spec['target_uri'] = 'sip:peer@127.0.0.1:5090'
        self.queue = {'schema_version': '1.0', 'kind': 'sip_batch', 'concurrency': 2,
                      'jobs': [{'name': 'one', 'repeat': 4, 'scenario': self.spec}]}
        self.request = {'schema_version': '1.0', 'kind': 'sipp_load', 'scenario': self.spec,
                        'calls': 4, 'concurrency': 2, 'rate': 2, 'timeout_s': 5}

    def queue_path(self):
        write_json(self.root / 'queue.json', self.queue); return self.root / 'queue.json'

    def package(self):
        write_json(self.root / 'load.json', self.request)
        export_package(self.root / 'load.json', self.root / 'package')
        return self.root / 'package'

    def test_dry_run_is_offline_and_assigns_nonoverlapping_ports(self):
        with patch('subprocess.Popen', side_effect=AssertionError('must not launch')):
            result = run_queue(self.queue_path(), self.root / 'out', True)
        self.assertEqual(result['counts']['planned'], 4)
        self.assertFalse(result['network_accessed'])
        nets = [read_json(self.root / 'out' / r['scenario'])['network'] for r in result['jobs']]
        self.assertEqual([n['rtp_port'] for n in nets], [4000, 4004, 4000, 4004])
        self.assertTrue((self.root / 'out' / 'summary.csv').exists())

    def test_preflight_rejects_entire_queue_before_spawning(self):
        self.queue['jobs'].append({'name': 'invalid', 'scenario': {**self.spec, 'steps': [{'action': 'play', 'file': 'absent.wav'}]}})
        with patch('subprocess.Popen', side_effect=AssertionError('must not launch')):
            with self.assertRaises(OSError): run_queue(self.queue_path(), self.root / 'out')
        self.assertFalse((self.root / 'out').exists())

    def test_caps_and_port_pool_boundaries(self):
        for key, value in [('concurrency', True), ('concurrency', 33), ('concurrency', 0), ('sip_port_base', 4000), ('rtp_port_base', 4001), ('rtp_port_base', 65000)]:
            with self.subTest(key=key, value=value):
                original = self.queue.copy(); self.queue[key] = value
                with self.assertRaises(ValueError): load_queue(self.queue_path())
                self.queue = original
        self.queue['jobs'] *= 11
        for job in self.queue['jobs']: job['repeat'] = 1000
        with self.assertRaises(ValueError): load_queue(self.queue_path())

    def fake_worker(self, fail=False):
        original = subprocess.Popen
        active = []; self.peak = 0; self.started = []
        def launch(command, **kwargs):
            self.peak = max(self.peak, sum(p.poll() is None for p in active) + 1)
            dest = Path(command[-1]); self.started.append(dest)
            failed = fail and len(self.started) == 1
            code = ('import json,time,pathlib,sys; time.sleep(.12); p=pathlib.Path(sys.argv[1]);p.mkdir();'
                    ' (p/"result.json").write_text(json.dumps({"status":sys.argv[2]}));sys.exit(int(sys.argv[3]))')
            process = original([sys.executable, '-c', code, str(dest), 'failed' if failed else 'completed', '3' if failed else '0'], **kwargs)
            active.append(process); return process
        return launch, active

    def test_real_child_processes_are_bounded_and_failures_do_not_block_later_jobs(self):
        launch, active = self.fake_worker(True)
        with patch('voice_tools.tools.sip.batch.subprocess.Popen', side_effect=launch):
            result = run_queue(self.queue_path(), self.root / 'out')
        self.assertEqual(self.peak, 2); self.assertEqual(len(self.started), 4)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['counts']['failed'], 1); self.assertEqual(result['counts']['completed'], 3)
        self.assertTrue(all(p.poll() is not None for p in active))

    def test_interrupt_cleans_children_and_cancels_pending(self):
        launch, active = self.fake_worker()
        sleep = time.sleep
        first = [True]
        def interrupt_once(seconds):
            if first.pop() if first else False: raise KeyboardInterrupt
            sleep(seconds)
        with patch('voice_tools.tools.sip.batch.subprocess.Popen', side_effect=launch), patch('voice_tools.tools.sip.batch.time.sleep', side_effect=interrupt_once):
            result = run_queue(self.queue_path(), self.root / 'out')
        self.assertEqual(result['status'], 'interrupted')
        self.assertEqual(result['counts']['interrupted'], 2)
        self.assertEqual(result['counts']['cancelled'], 2)
        self.assertTrue(all(p.poll() is not None for p in active))

    def test_invalid_receipt_and_launch_failure_are_not_success(self):
        with patch('voice_tools.tools.sip.batch.subprocess.Popen', side_effect=OSError('launch failed')):
            result = run_queue(self.queue_path(), self.root / 'out')
        self.assertEqual(result['counts']['failed'], 4)
        self.assertFalse(result['network_accessed'])

    def test_sipp_offline_package_xml_and_rate_limits(self):
        package = self.package()
        ET.parse(package / 'scenario.xml')
        self.assertIn('play_pcap_audio="dtmf-1.pcap"', (package / 'scenario.xml').read_text())
        result = run_load(package, self.root / 'plan', dry_run=True)
        self.assertEqual(result['status'], 'planned'); self.assertFalse(result['network_accessed'])
        for flag, value in [('-l', '2'), ('-m', '4'), ('-r', '2')]:
            self.assertEqual(result['command'][result['command'].index(flag)+1], value)
        self.assertIn('-timeout_error', result['command'])

    def test_sipp_refuses_tampered_xml_and_pcap(self):
        package = self.package(); xml = (package / 'scenario.xml').read_text()
        (package / 'scenario.xml').write_text(xml.replace('play_pcap_audio="dtmf-1.pcap"', 'command="touch /tmp/pwn"'))
        with self.assertRaisesRegex(ValueError, 'scenario.xml'): run_load(package, self.root / 'out', dry_run=True)
        (package / 'scenario.xml').write_text(xml); (package / 'dtmf-1.pcap').write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError, 'pcap'): run_load(package, self.root / 'out', dry_run=True)

    def test_no_silent_dropping_of_unsupported_features(self):
        for change in [{'record_early': True}, {'account': {'auth': {'username': 'u', 'password_env': 'PW'}}}, {'steps': [{'action': 'unknown'}]}]:
            with self.subTest(change=change):
                self.request['scenario'] = {**self.spec, **change}
                with self.assertRaises(ValueError): validate_request(self.request, self.root)
        self.request['scenario'] = self.spec
        self.request['targets'] = ['sip:a@127.0.0.1:5060', 'sip:b@127.0.0.1:5061']
        with self.assertRaisesRegex(ValueError, '相同'): validate_request(self.request, self.root)

    def test_browser_export_is_byte_identical_and_zip_is_consumable(self):
        import base64
        import io
        import zipfile
        js = Path(__file__).resolve().parents[2] / 'docs/sip-studio/batch-core.js'
        for method in ('rfc4733', 'sip_info'):
            self.spec['steps'][1]['method'] = method
            self.spec['steps'][1]['digits'] = '11#ABCD'
            self.spec['steps'][1]['duration_ms'] = 40
            script = "const C=require(process.argv[1]),r=JSON.parse(process.argv[2]);const f=C.files(C.request(r.scenario,r));console.log(Buffer.from(C.zip(f)).toString('base64'))"
            response = subprocess.run(['node', '-e', script, str(js), json.dumps(self.request)], capture_output=True, text=True, check=True)
            archive = zipfile.ZipFile(io.BytesIO(base64.b64decode(response.stdout)))
            self.assertIsNone(archive.testzip()); directory = self.root / method; archive.extractall(directory)
            _, plan, _ = validate_request(self.request, self.root)
            self.assertEqual((directory / 'scenario.xml').read_text(), compile_xml(plan))
            if method == 'rfc4733': self.assertEqual((directory / 'dtmf-1.pcap').read_bytes(), dtmf_pcap(plan['steps'][1]))
            self.assertEqual(run_load(directory, self.root / (method+'-plan'), dry_run=True)['status'], 'planned')

    def test_sipp_requires_complete_statistics_even_when_exit_zero(self):
        package = self.package()
        for suffix, stats, expected in [('missing', '', 'failed'), ('partial', '2;2;0;0', 'failed'), ('complete', '4;4;0;0', 'completed')]:
            fake = self.root / ('sipp-' + suffix)
            code = "#!" + sys.executable + "\nfrom pathlib import Path\n"
            if stats:
                code += "Path('statistics.csv').write_text(" + repr('TotalCallCreated;SuccessfulCall(C);FailedCall(C);CurrentCall\n' + stats + '\n') + ")\n"
            fake.write_text(code); fake.chmod(0o700)
            result = run_load(package, self.root / suffix, str(fake))
            self.assertEqual(result['status'], expected)
            self.assertEqual(result['exit_code'], 0)

    @unittest.skipUnless(shutil.which('tshark'), 'optional independent PCAP decoder')
    def test_generated_dtmf_decodes_as_rfc4733_with_repeat_keys_and_duration(self):
        self.spec['steps'][1]['digits'] = '11#'
        package = self.package()
        decoded = subprocess.run(['tshark', '-r', str(package / 'dtmf-1.pcap'), '-d', 'udp.port==6000,rtp',
                                  '-d', 'rtp.pt==101,rtpevent', '-T', 'fields', '-e', 'rtp.timestamp',
                                  '-e', 'rtpevent.event_id', '-e', 'rtpevent.end_of_event', '-e', 'rtpevent.duration'],
                                 capture_output=True, text=True, check=True)
        ended = {}
        for line in decoded.stdout.splitlines():
            stamp, digit, end, duration = line.split('\t')
            if end == 'True': ended.setdefault(stamp, []).append((digit, duration))
        self.assertEqual(list(ended), ['0', '2080', '4160'])
        self.assertEqual(list(ended.values()), [[('1', '1280')]*3, [('1', '1280')]*3, [('11', '1280')]*3])

    def test_sipp_launch_error_still_writes_failure_receipt(self):
        package = self.package()
        with patch('voice_tools.tools.sip.load.subprocess.Popen', side_effect=OSError('spawn failure')):
            with self.assertRaises(OSError): run_load(package, self.root / 'out', sys.executable)
        self.assertEqual(read_json(self.root / 'out/result.json')['status'], 'failed')

    def test_existing_output_preserved(self):
        output = self.root / 'out'; output.mkdir(); (output / 'keep').write_text('keep')
        with self.assertRaises(ValueError): run_queue(self.queue_path(), output, True)
        self.assertEqual((output / 'keep').read_text(), 'keep')


if __name__ == '__main__': unittest.main()
