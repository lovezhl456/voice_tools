import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from voice_tools.core.files import sha256, write_json
from voice_tools.tools.sessions.store import build, connect


class IndexInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.scans = []

        def scan(path, emit, ports, max_packets, tshark):
            self.scans.append((Path(path), ports))
            emit({'call_id': Path(path).stem, 'epoch': 1})
            return {'limited': False, 'first_epoch': 1, 'last_epoch': 1}

        self.scan = self.enter_patch('voice_tools.tools.sessions.store.scan', side_effect=scan)

    def enter_patch(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def capture(self, name):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
        return path

    def snapshot(self, name, call_id='snapshot-call'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {'schema_version': '1.0', 'call_id': call_id, 'observed_at': '2026-09-15T08:00:00Z'}
        path.write_text(json.dumps(row) + '\n')
        return path

    def manifest(self, folder, captures, **extra):
        write_json(folder / 'host.json', {
            'schema_version': '1.0', 'tool': 'capture-batch-host',
            'name': 'manifest-host', 'status': 'complete',
            'files': [{'file': p.name, 'sha256': sha256(p)} for p in captures],
            **extra,
        })

    def test_resolved_duplicate_overrides_metadata_without_moving_source(self):
        first = self.capture('a.pcap')
        second = self.capture('b.pcap')
        event = self.snapshot('events.jsonl')
        (self.root / 'sub').mkdir()
        alias = self.root / 'sub/../a.pcap'
        merge = self.enter_patch('voice_tools.core.packets.merge_packets')

        result = build(self.root / 'index', pcaps=[alias], events=[event],
                       pcap_groups=[('sensor', [first, second])])

        self.assertEqual([s['path'] for s in result['sources']], list(map(str, [first, second, event])))
        self.assertEqual(result['sources'][0]['host'], alias.parent.name)
        self.assertIsNone(result['sources'][0]['details']['sensor_group'])
        self.assertEqual(result['sources'][1]['host'], 'sensor')
        merge.assert_not_called()

    def test_manifest_overrides_direct_inputs_and_discovers_snapshots(self):
        capture = self.capture('host/call.pcap')
        event = self.snapshot('host/events.jsonl', 'event-call')
        snapshot = self.snapshot('host/sessions.jsonl')
        self.manifest(capture.parent, [capture], status='recovered', sip_ports=[5070],
                      sensor_id='sensor-A', warnings=['retention note'], warnings_informational=True,
                      events={'file': event.name, 'sha256': sha256(event)})

        result = build(self.root / 'index', pcaps=[capture], events=[event], batches=[capture.parent])

        self.assertEqual([s['path'] for s in result['sources']], list(map(str, [event, capture, snapshot])))
        self.assertEqual([s['host'] for s in result['sources']], ['manifest-host'] * 3)
        self.assertEqual(result['sources'][1]['details']['sensor_id'], 'sensor-A')
        self.assertEqual(self.scans, [(capture, [5070])])
        self.assertEqual(result['warnings'], ['retention note'])
        self.assertFalse(result['partial'])

    def test_batch_and_event_gaps_keep_warning_order(self):
        snapshot = self.snapshot('valid.jsonl')
        host = self.root / 'host'
        host.mkdir()
        self.manifest(host, [], events={'file': 'missing.jsonl'})
        write_json(self.root / 'batch.json', {
            'schema_version': '1.0', 'tool': 'capture-batch', 'status': 'partial',
            'hosts': [{'manifest': 'absent/host.json'}, {'manifest': 'host/host.json'}],
        })

        result = build(self.root / 'index', snapshots=[snapshot], batches=[self.root])

        self.assertEqual((result['partial'], result['errors'], result['observations']), (True, 0, 1))
        self.assertEqual(result['warnings'], [
            f'批量抓包 {self.root.name} 状态为 partial，证据可能不完整。',
            f'缺少主机清单：{self.root / "absent/host.json"}',
            '主机事件清单存在，但映射文件缺失。',
        ])


    def test_merge_failure_falls_back_to_each_original_in_order(self):
        first, second = [self.capture(name) for name in ('a.pcap', 'b.pcap')]
        self.enter_patch('voice_tools.core.packets.merge_packets', side_effect=OSError('merge unavailable'))

        result = build(self.root / 'index', pcap_groups=[('sensor', [first, second])])

        self.assertEqual([s['path'] for s in result['sources']], [str(first), str(second)])
        self.assertEqual([s['status'] for s in result['sources']], ['ok', 'ok'])
        self.assertEqual((result['partial'], result['errors'], result['observations']), (True, 0, 2))
        self.assertEqual(result['warnings'], ['分片组无法连续重组，降级为逐文件索引：merge unavailable'])

    def test_manifest_digest_failure_prevents_merge_and_isolates_bad_source(self):
        first, second = [self.capture('host/' + name) for name in ('a.pcap', 'b.pcap')]
        self.manifest(first.parent, [first, second])
        first.write_bytes(b'changed after manifest')
        merge = self.enter_patch('voice_tools.core.packets.merge_packets')

        result = build(self.root / 'index', batches=[first.parent])

        merge.assert_not_called()
        self.assertEqual([s['status'] for s in result['sources']], ['error', 'ok'])
        self.assertEqual((result['partial'], result['errors'], result['observations']), (True, 1, 1))
        self.assertEqual(self.scans, [(second, [5060])])
        self.assertIn('源分片摘要与清单不一致', result['warnings'][0])
        self.assertIn('SHA-256', result['sources'][0]['error'])

    def test_invalid_manifests_fail_before_output_creation(self):
        for manifest in (
            {'schema_version': 'bad', 'tool': 'capture-batch-host'},
            {'schema_version': '1.0', 'tool': 'capture-batch-host',
             'files': [{'file': '../outside.pcap', 'sha256': 'unused'}]},
        ):
            with self.subTest(manifest=manifest):
                folder = self.root / 'host'
                folder.mkdir(exist_ok=True)
                write_json(folder / 'host.json', manifest)
                with self.assertRaises(ValueError):
                    build(self.root / 'index', batches=[folder])
                self.assertFalse((self.root / 'index').exists())

    def test_partial_source_rows_roll_back_but_other_sources_and_warnings_remain(self):
        broken = self.snapshot('broken.jsonl', 'must-roll-back')
        with broken.open('a') as stream:
            stream.write(json.dumps({'schema_version': '1.0', 'evidence': 'fs_event_gap',
                                     'reason': 'disconnect'}) + '\nnot JSON\n')
        valid = self.snapshot('valid.jsonl', 'keep-this-call')

        result = build(self.root / 'index', snapshots=[broken, valid])

        self.assertEqual([s['status'] for s in result['sources']], ['error', 'ok'])
        self.assertEqual((result['errors'], result['sessions'], result['observations']), (1, 1, 1))
        self.assertEqual(result['warnings'], ['FS 事件缺口：disconnect'])
        with connect(self.root / 'index') as db:
            self.assertEqual([r['call_id'] for r in db.execute('SELECT call_id FROM observations')],
                             ['keep-this-call'])
            self.assertEqual([r['status'] for r in db.execute('SELECT status FROM sources ORDER BY id')],
                             ['error', 'ok'])
