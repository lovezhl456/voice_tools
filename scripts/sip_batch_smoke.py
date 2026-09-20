#!/usr/bin/env python3
"""Opt-in, loopback-only batch/SIPp acceptance. No external SIP destinations."""
import argparse
import json
from pathlib import Path
import shutil
import socket
import subprocess
import time

from voice_tools.core.files import new_output, write_json
from voice_tools.tools.sip.batch import run_queue
from voice_tools.tools.sip.load import export_package, run_load
from voice_tools.tools.sip.scenario import template


def port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sipp', default='sipp')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--include-pcap', action='store_true', help='Also exercise native RFC4733 PCAP; requires raw socket permission')
    args = parser.parse_args()
    binary = shutil.which(args.sipp)
    if not binary: parser.error('SIPp executable not found')
    root = new_output(args.out).resolve()
    matrix = [('functional', True, None), ('signalling', False, None), ('info', False, 'sip_info')]
    if args.include_pcap: matrix.append(('rfc4733', False, 'rfc4733'))
    summary = {}
    for name, functional, method in matrix:
        sip_port = port()
        command = [binary, '-sn', 'uas', '-i', '127.0.0.1', '-p', str(sip_port), '-mp', '32100',
                   '-rtp_echo', '-m', '4', '-timeout', '20s', '-timeout_error', '-nostdin']
        if method == 'sip_info':
            # -sd exits 99 after printing the built-in scenario. It is not a call failure.
            dump = subprocess.run([binary, '-sd', 'uas'], capture_output=True, text=True, timeout=10)
            if dump.returncode not in (0, 99): raise ValueError('Unable to dump built-in UAS scenario')
            insertion = '''<recv request="INFO"/><send><![CDATA[
SIP/2.0 200 OK
[last_Via:]
[last_From:]
[last_To:]
[last_Call-ID:]
[last_CSeq:]
Content-Length: 0

]]></send>'''
            if '<recv request="BYE">' not in dump.stdout: raise ValueError('Unsupported built-in UAS template')
            uas = root / (name + '-uas.xml')
            uas.write_text(dump.stdout.replace('<recv request="BYE">', insertion + '\n<recv request="BYE">'))
            command[1:3] = ['-sf', str(uas)]
        with (root / (name + '-uas.log')).open('wb') as log:
            peer = subprocess.Popen(command, stdout=log, stderr=log)
            try:
                time.sleep(.35)
                if peer.poll() is not None: raise ValueError('UAS startup failed; inspect log')
                spec = template()
                spec.update(target_uri=f'sip:peer@127.0.0.1:{sip_port}', codec='PCMU', connect_timeout_s=3, max_call_s=5)
                spec['steps'] = [{'action': 'wait', 'seconds': .5}]
                if method: spec['steps'].append({'action': 'dtmf', 'digits': '1', 'method': method, 'duration_ms': 160, 'gap_ms': 100})
                spec['steps'].append({'action': 'hangup'})
                if functional:
                    write_json(root / 'queue.json', {'schema_version': '1.0', 'kind': 'sip_batch', 'concurrency': 2,
                               'rtp_port_base': 32000, 'jobs': [{'name': 'native loopback', 'repeat': 4, 'scenario': spec}]})
                    result = run_queue(root / 'queue.json', root / 'batch')
                else:
                    config = root / (name + '.json')
                    write_json(config, {'schema_version': '1.0', 'kind': 'sipp_load', 'scenario': spec, 'calls': 4,
                               'concurrency': 2, 'rate': 4, 'local_ip': '127.0.0.1', 'sip_port': port(), 'rtp_port': 32200, 'timeout_s': 10})
                    export_package(config, root / (name + '-package'))
                    result = run_load(root / (name + '-package'), root / (name + '-run'), binary)
                summary[name] = result
                write_json(root / 'summary.json', summary)
                print(name, result['status'], flush=True)
            finally:
                if peer.poll() is None:
                    peer.terminate()
                    try: peer.wait(timeout=5)
                    except subprocess.TimeoutExpired: peer.kill()
                peer.wait()
    return 0 if all(r['status'] == 'completed' for r in summary.values()) else 3


if __name__ == '__main__': raise SystemExit(main())
