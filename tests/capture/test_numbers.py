import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from voice_tools.core.files import read_json, sha256, write_json
from voice_tools.tools.capture import numbers
from voice_tools.tools.capture import number_remote
from voice_tools.core.capture_contract import selectors
from voice_tools.tools.sessions.number_bundle import process, select_calls
from voice_tools.tools.capture.remote import Agent
from voice_tools.tools.sessions.store import initialize, add
from tests.sessions.fixtures import CALL_A, CALL_B, T0, packet, pcap, sip
from tests.capture.fixtures import invite, seed_capture
from tests.report.fixtures import rtp


class NumberCaptureTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def test_role_selection_is_exact_and_does_not_combine_different_observations(self):
        path = self.root / 'index'; path.mkdir()
        db = initialize(path / 'sessions.sqlite')
        db.execute("INSERT INTO sources(id,kind) VALUES(1,'pcap'),(2,'fs')")
        records = [
            (1, dict(call_id='initial', caller='1001', callee='1002', method='INVITE')),
            (1, dict(call_id='reverse', caller='1001', callee='1002', method='BYE')),
            (1, dict(call_id='reinvite', caller='1001', callee='1002', method='INVITE', to_tag='dialog')),
            (1, dict(call_id='prefix', caller='10010', callee='1002', method='INVITE')),
            (2, dict(call_id='fs', caller='1001', callee='1002', uuid='uid')),
            (2, dict(call_id='mixed', caller='1001', callee='9999')),
            (2, dict(call_id='mixed', caller='9999', callee='1002'))]
        for source, data in records: add(db, source, dict(epoch=T0, **data))
        db.commit(); db.close()
        rows, total, eligible = select_calls(path, selectors(['1001'], ['1002']), 100)
        self.assertEqual({r['call_id'] for r in rows}, {'initial', 'fs'})
        self.assertEqual(total, 2); self.assertEqual(eligible, 5)
        self.assertEqual(select_calls(path, selectors([], ['1002']), 1)[1], 4)

    def test_server_split_contains_only_selected_call_and_media(self):
        root, config = seed_capture(self.root); process(root, config)
        status = read_json(root / 'number-status.json')
        manifest = numbers.verify_archive(root / 'sessions.zip', status['archive'], 8 * 1048576)
        self.assertEqual(manifest['status'], 'complete')
        self.assertEqual(manifest['matched_sessions'], 1)
        self.assertEqual(manifest['sessions'][0]['call_id'], CALL_A)
        with zipfile.ZipFile(root / 'sessions.zip') as bundle:
            captures = [p for p in bundle.namelist() if p.endswith('.pcapng')]
            self.assertEqual(len(captures), 1)
            path = self.root / 'selected.pcapng'; path.write_bytes(bundle.read(captures[0]))
            raw = subprocess.check_output(['tshark', '-r', str(path), '-T', 'fields', '-e', 'sip.Call-ID', '-e', 'udp.srcport'], text=True)
            self.assertIn(CALL_A, raw); self.assertNotIn(CALL_B, raw)
            self.assertIn('16000', raw); self.assertNotIn('16002', raw)
            self.assertFalse(any(p.endswith('config.json') or '/spool/' in p for p in bundle.namelist()))

    def test_cross_file_capture_exports_one_file_per_call(self):
        root, config = seed_capture(self.root, [(0, packet(invite(CALL_A))), (.01, packet(invite(CALL_B, port=16002)))])
        pcap(root/'spool/b.pcap', [(1, packet(rtp(1),16000,24000)), (1.1, packet(rtp(2),16002,24002))])
        agent = Agent(root, config); agent.started=T0; agent.finished=T0+2
        state = agent.status(); state['status']='complete'; write_json(root/'status.json',state)
        process(root,config)
        with zipfile.ZipFile(root/'sessions.zip') as bundle:
            manifest=json.loads(bundle.read('manifest.json'))
            self.assertEqual(manifest['exported_sessions'],2)
            for session in manifest['sessions']:
                self.assertEqual(len([f for f in session['files'] if f['file'].endswith('.pcapng')]),1)

    def test_capture_partial_and_archive_quota_propagate(self):
        root,config=seed_capture(self.root)
        state=read_json(root/'status.json');state['status']='partial';write_json(root/'status.json',state)
        process(root,config)
        self.assertEqual(read_json(root/'number-status.json')['status'],'partial')
        other=self.root/'quota';shutil.copytree(root,other);shutil.rmtree(other/'number-work')
        config['bundle_mib']=1
        process(other,config)
        result=read_json(other/'number-status.json')
        self.assertEqual((result['status'],result['exported_sessions']),('partial',0))
        self.assertLessEqual((other/'sessions.zip').stat().st_size,1048576)

    def test_archive_write_failure_never_publishes_incomplete_zip(self):
        root,config=seed_capture(self.root)
        with patch.object(zipfile.ZipFile,'write',side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError,'disk full'):process(root,config)
        self.assertFalse((root/'sessions.zip').exists())
        self.assertNotIn('archive',read_json(root/'number-status.json'))
        self.assertTrue((root/'spool/a.pcap').exists())

    def test_missing_remote_dependency_fails_before_capture(self):
        root,config=seed_capture(self.root)
        write_json(root/'config.json',config)
        with patch.object(sys,'argv',['worker','check',str(root)]), patch.object(number_remote.shutil,'which',return_value=None), patch.object(number_remote,'Agent') as agent:
            with self.assertRaisesRegex(ValueError,'缺少工具'):number_remote.main()
            agent.assert_not_called()

    def test_processing_timeout_stops_process_group_and_keeps_capture(self):
        root,config=seed_capture(self.root);write_json(root/'config.json',config)
        with patch.object(sys,'argv',['worker','run',str(root)]), patch.object(number_remote,'check'), patch.object(number_remote,'Agent'), patch.object(number_remote.subprocess,'Popen') as spawn, patch.object(number_remote.os,'killpg') as kill:
            spawn.return_value.pid=12345
            spawn.return_value.wait.side_effect=[subprocess.TimeoutExpired('worker',30),0]
            number_remote.main()
            kill.assert_called_once_with(12345,number_remote.signal.SIGKILL)
        self.assertIn('超时',read_json(root/'number-status.json')['error'])
        self.assertTrue((root/'spool/a.pcap').exists())

    def test_session_limit_is_partial_and_no_matches_is_explicit(self):
        root, config = seed_capture(self.root, [(0, packet(invite(CALL_A))), (.01, packet(invite(CALL_B, port=16002)))])
        config['max_sessions'] = 1; process(root, config)
        state = read_json(root / 'number-status.json')
        self.assertEqual((state['status'], state['matched_sessions'], state['exported_sessions']), ('partial', 2, 1))
        other = self.root / 'nomatch'; shutil.copytree(root, other)
        shutil.rmtree(other / 'number-work'); (other / 'host.json').unlink()
        config['selection'] = selectors([], ['absent']); process(other, config)
        state = read_json(other / 'number-status.json')
        self.assertEqual((state['status'], state['matched_sessions']), ('complete', 0))
        with zipfile.ZipFile(other / 'sessions.zip') as bundle:
            self.assertEqual(bundle.namelist(), ['manifest.json'])

    def test_fs_matching_without_initial_sip_and_unsafe_call_id_filename(self):
        root, config = seed_capture(self.root, [(0, packet(rtp(1), 16000, 24000))])
        event = dict(schema_version='1.0', call_id='../../escape;$(cmd)', caller='1001', callee='1002',
                     uuid='11111111-2222-3333-4444-555555555555', evidence='fs_snapshot',
                     observed_at='2026-09-15T08:00:00+00:00', window_seconds=10,
                     flow=dict(local_ip='192.0.2.1', local_port=16000, remote_ip='192.0.2.2', remote_port=24000))
        (root / 'events.jsonl').write_text(json.dumps(event) + '\n')
        process(root, config)
        state = read_json(root / 'number-status.json')
        manifest = numbers.verify_archive(root / 'sessions.zip', state['archive'], 8 * 1048576)
        self.assertEqual(manifest['exported_sessions'], 1)
        self.assertEqual(manifest['sessions'][0]['basis'], 'fs_roles')
        self.assertNotIn('escape', manifest['sessions'][0]['directory'])

    def test_archive_rejects_tampering_and_unsafe_paths(self):
        path = self.root / 'bad.zip'
        with zipfile.ZipFile(path, 'w') as bundle: bundle.writestr('../escape', 'bad')
        info = dict(file='sessions.zip', bytes=path.stat().st_size, sha256=sha256(path))
        with self.assertRaisesRegex(ValueError, '非会话路径'): numbers.verify_archive(path, info, 2048)
        info['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'SHA-256'): numbers.verify_archive(path, info, 2048)

    def test_no_identity_evidence_is_partial_and_active_capture_is_rejected(self):
        root, config = seed_capture(self.root, [(0, packet(sip(CALL_A, 'BYE', 0)))])
        process(root, config)
        self.assertEqual(read_json(root / 'number-status.json')['status'], 'partial')
        write_json(root / 'status.json', {'status': 'running'})
        with self.assertRaisesRegex(ValueError, '尚未停止'): process(root, config)

    def test_dry_run_requires_selector_and_has_no_remote_side_effects(self):
        inventory = self.root / 'hosts.json'
        write_json(inventory, {'schema_version': '1.0', 'hosts': [{'name': 'fs-a', 'host': 'fs-a',
                   'addresses': ['192.0.2.1'], 'rtp_ranges': [[16000, 24000]]}]})
        with patch.object(numbers, 'launch', side_effect=AssertionError('remote access')):
            data = numbers.start(inventory, self.root / 'plan', callers=['1001'], dry_run=True)
        self.assertEqual(data['status'], 'planned')
        self.assertEqual(data['plans'][0]['selection'], selectors(['1001'], []))
        with self.assertRaises(ValueError): numbers.start(inventory, self.root / 'invalid', dry_run=True)
        self.assertFalse((self.root / 'invalid').exists())
        with self.assertRaises(ValueError): numbers.fetch(self.root / 'plan', self.root / 'download')

    def test_multiple_hosts_keep_success_when_one_fails(self):
        data={'selection':selectors(['1001'],[]),'hosts':[{'name':'good'},{'name':'bad'}]}
        def finish(row,*args):
            if row['name']=='bad':raise OSError('host failed')
            return {'name':'good','status':'complete','exported_sessions':2}
        with patch.object(numbers,'collect_host',side_effect=finish):
            result=numbers.collect(data,self.root,False,None)
        self.assertEqual(result['status'],'partial')
        self.assertEqual({h['name']:h['status'] for h in result['hosts']},{'good':'complete','bad':'failed'})

    def test_wait_can_recover_a_running_job_after_lost_launch_reply(self):
        root,config=seed_capture(self.root);process(root,config)
        ready=read_json(root/'number-status.json')
        row=dict(name='fs-a',host='fs-a',remote_dir='/tmp/voice-tools-'+'a'*32,
                 seconds=1,processing_seconds=30,bundle_mib=8,status='failed',error='lost launch reply')
        output=self.root/'recovery';output.mkdir()
        class LocalSSH:
            def __init__(self,*args):pass
            def copy(self,remote,local,stop=None):shutil.copyfile(root/'sessions.zip',local)
        with patch.object(numbers,'remote_state',side_effect=[{'status':'capturing'},ready]):
            result=numbers.collect_host(row,output,threading.Event(),True,LocalSSH)
        self.assertEqual(result['status'],'complete',result)
        self.assertNotIn('error',result)

    def test_cli_contract_dry_run_and_validation(self):
        output=self.root/'cli'
        result=subprocess.run([sys.executable,'-m','voice_tools','--json','capture','by-number',
            '--inventory','examples/capture/hosts.example.json','--caller','+861001','--dry-run','--out',str(output)],
            capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        result=json.loads(result.stdout)
        self.assertEqual(result['summary']['selection']['callers'],['+861001'])
        self.assertIn('job.json',result['artifacts'])
        result=subprocess.run([sys.executable,'-m','voice_tools','--json','capture','by-number',
            '--inventory','examples/capture/hosts.example.json','--dry-run','--out',str(self.root/'badcli')],
            capture_output=True,text=True)
        self.assertEqual(result.returncode,2)
        self.assertEqual(json.loads(result.stdout)['error']['code'],'INVALID_INPUT')

    def test_deployed_runtime_lifecycle_and_failed_download_recovery(self):
        binary = self.root / 'bin'; binary.mkdir()
        raw = pcap(self.root / 'seed.pcap', [(0, packet(invite(CALL_A))), (.1, packet(rtp(1),16000,24000))])
        fake = binary / 'dumpcap'
        fake.write_text('#!' + sys.executable + '\nimport base64,sys,time\nfrom pathlib import Path\n'
            'p=Path(sys.argv[sys.argv.index("-w")+1]);p.write_bytes(base64.b64decode(' + repr(base64.b64encode(raw).decode()) + '))\n'
            'time.sleep(1.1)\nprint(str(p),flush=True)\n'
            'print("Packets received/dropped on interface \'any\': 2/0 (pcap:0/dumpcap:0/flushed:0/ps_ifdrop:0)",file=sys.stderr)\n')
        fake.chmod(0o700)
        env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ['PATH'])
        copied = []
        class LocalSSH:
            fail = True
            def __init__(self, *args): pass
            def checked(self, args, timeout=30):
                result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)
                if result.returncode: raise ValueError(result.stderr[-1000:])
                return result.stdout.strip()
            def upload(self, local, remote): shutil.copyfile(local, remote)
            def copy(self, remote, local, stop=None):
                copied.append(remote)
                if self.fail: raise ValueError('simulated SCP interruption')
                shutil.copyfile(remote, local)
        inventory = self.root / 'hosts.json'
        write_json(inventory, {'schema_version':'1.0','hosts':[{'name':'fs-a','host':'fs-a',
            'addresses':['192.0.2.1'],'rtp_ranges':[[16000,24000]]}]})
        result = numbers.start(inventory, self.root/'job', callers=['1001'], seconds=1, max_mib=8,
                               snapshot_seconds=0, processing_seconds=30, ssh_factory=LocalSSH)
        job = read_json(self.root/'job/job.json'); remote = Path(job['hosts'][0]['remote_dir'])
        self.addCleanup(shutil.rmtree,remote,True)
        self.assertEqual(result['status'],'partial',result)
        self.assertIn('SCP interruption',result['hosts'][0].get('error',''),result)
        self.assertFalse((self.root/'job/fs-a/sessions.zip').exists())
        LocalSSH.fail = False
        recovered = numbers.fetch(self.root/'job',self.root/'recovered',ssh_factory=LocalSSH)
        self.assertEqual(recovered['status'],'complete',recovered)
        self.assertEqual(recovered['hosts'][0]['exported_sessions'],1)
        self.assertTrue(all(p.endswith('/sessions.zip') for p in copied))
        self.assertTrue((remote/'spool/capture.pcap').exists())


if __name__ == '__main__': unittest.main()
