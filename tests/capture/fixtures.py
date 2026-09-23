"""Synthetic number-capture inputs shared by capture and review tests."""
import os

from voice_tools.core.capture_contract import selectors
from voice_tools.tools.capture.remote import Agent
from tests.report.fixtures import rtp
from tests.sessions.fixtures import CALL_A, CALL_B, T0, packet, pcap, sip


def invite(call_id, caller='1001', callee='1002', port=16000):
    return (sip(call_id, port=port).replace(b';tag=b', b'')
            .replace(b'1001', caller.encode()).replace(b'1002', callee.encode()))


def capture_config(**extra):
    return dict(name='fs-a', host='fs-a', sensor_id='fs-a:any', seconds=2, segment_seconds=10,
                max_mib=8, snaplen=65535, mode='window', snapshot_seconds=0, bpf='udp',
                selection=selectors(['1001'], []), uid=os.getuid(), gid=os.getgid(),
                bundle_mib=8, max_sessions=100, max_packets=10000, processing_seconds=30, **extra)


def seed_capture(directory, rows=None):
    root = directory / 'remote'
    root.mkdir()
    config = capture_config()
    agent = Agent(root, config)
    pcap(root / 'spool/a.pcap', rows or [
        (0, packet(invite(CALL_A))), (.01, packet(invite(CALL_B, '9001', '9002', 16002))),
        (.1, packet(rtp(1), 16000, 24000)), (.2, packet(rtp(2), 16002, 24002))])
    agent.started = T0
    agent.finished = T0 + 2

    class Ended:
        def poll(self):
            return 0

    agent.process = Ended()
    agent.status()
    return root, config
