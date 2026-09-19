"""Read-only inbound ESL framing and field whitelist; also deployed with the remote agent."""
from datetime import datetime, timezone
import ipaddress
import os
from pathlib import Path
import socket
import time
from urllib.parse import unquote
from uuid import UUID

EVENTS = ('CHANNEL_CREATE CHANNEL_ANSWER CHANNEL_BRIDGE CHANNEL_UNBRIDGE '
          'CHANNEL_HANGUP_COMPLETE CHANNEL_DESTROY CHANNEL_CALLSTATE CODEC RECV_RTCP_MESSAGE')


def iso(epoch=None):
    return datetime.fromtimestamp(time.time() if epoch is None else epoch, timezone.utc).isoformat()


def headers(text):
    result = {}
    for line in text.splitlines():
        key, sep, value = line.partition(':')
        if sep:
            result[key.strip().lower()] = unquote(value.strip())
    return result


def record(values, received=None):
    h = {k.lower(): v for k, v in values.items()}
    def get(*keys):
        return next((h[k.lower()] for k in keys if h.get(k.lower())), '')
    uid = get('unique-id', 'channel-call-uuid', 'caller-unique-id')
    try:
        uid = str(UUID(uid))
    except ValueError:
        return None
    call_id = get('variable_sip_call_id', 'sip_call_id')
    if len(call_id) > 1024 or any(ord(c) < 32 for c in call_id):
        call_id = ''
    received = time.time() if received is None else received
    try:
        timestamp = float(get('event-date-timestamp')) / 1000000
        when = iso(timestamp)
    except (ValueError, OverflowError, OSError):
        when = iso(received)
    data = {'schema_version': '1.0', 'evidence': 'fs_event', 'observed_at': when,
            'received_at': iso(received), 'uuid': uid, 'call_id': call_id,
            'event': get('event-name') or 'SNAPSHOT',
            'caller': get('caller-caller-id-number', 'variable_caller_id_number')[:256],
            'callee': get('caller-destination-number', 'variable_destination_number')[:256]}
    peer = get('other-leg-unique-id', 'variable_bridge_uuid', 'variable_signal_bond', 'bridge_uuid', 'signal_bond')
    try:
        data['peer_uuid'] = str(UUID(peer)) if peer else None
    except ValueError:
        data['peer_uuid'] = None
    correlation = get('variable_sip_h_x-cid', 'variable_sip_h_x-trace-id', 'variable_conversation_id')
    if correlation and len(correlation) <= 1024 and not any(ord(c) < 32 for c in correlation):
        data['correlation_id'] = correlation
    data['state'] = get('channel-call-state', 'channel-state')[:64]
    data['flow'] = None
    try:
        addresses = [ipaddress.ip_address(get('variable_'+k, k)) for k in ('local_media_ip', 'remote_media_ip')]
        ports = [int(get('variable_'+k, k)) for k in ('local_media_port', 'remote_media_port')]
        if any(a.is_unspecified for a in addresses) or any(not 1 <= p <= 65535 for p in ports):
            raise ValueError()
        data['flow'] = dict(local_ip=str(addresses[0]), local_port=ports[0], remote_ip=str(addresses[1]), remote_port=ports[1])
    except ValueError:
        pass
    data['codecs'] = {k: get('variable_'+k, k)[:80] for k in
                      ('read_codec', 'write_codec', 'read_rate', 'write_rate', 'rtp_use_codec_name', 'rtp_use_codec_rate')
                      if get('variable_'+k, k)}
    return data


def validate_config(config):
    if not isinstance(config, dict) or set(config) - {'host', 'port', 'password_file', 'password_env'}:
        raise ValueError('ESL 配置只接受 host/port/password_file/password_env，禁止明文密码字段')
    host = config.get('host', '127.0.0.1')
    if not ipaddress.ip_address(host).is_loopback:
        raise ValueError('ESL 经 SSH 在远端连接回环地址；不向网络发送明文 ESL 密码')
    if not isinstance(config.get('port', 8021), int) or not 1 <= config.get('port', 8021) <= 65535:
        raise ValueError('ESL 端口无效')
    if bool(config.get('password_file')) == bool(config.get('password_env')):
        raise ValueError('ESL 必须且只能指定远端 password_file 或 password_env')
    for key in ('password_file', 'password_env'):
        if config.get(key) and (not isinstance(config[key], str) or '\n' in config[key]):
            raise ValueError('ESL 凭据来源必须是有效字符串')
    return config


class Connection:
    def __init__(self, sock):
        self.sock, self.buffer = sock, bytearray()

    def frame(self):
        while True:
            marker = self.buffer.find(b'\n\n')
            length_sep = 2
            other = self.buffer.find(b'\r\n\r\n')
            if other >= 0 and (marker < 0 or other < marker):
                marker, length_sep = other, 4
            if marker >= 0:
                head = headers(self.buffer[:marker].decode('utf-8', 'replace'))
                length = int(head.get('content-length', '0'))
                if not 0 <= length <= 1048576:
                    raise ValueError('ESL frame size exceeds budget')
                end = marker + length_sep + length
                if len(self.buffer) >= end:
                    body = bytes(self.buffer[marker + length_sep:end])
                    del self.buffer[:end]
                    return head, body
            elif len(self.buffer) > 65536:
                raise ValueError('ESL header exceeds budget')
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError('ESL disconnected')
            self.buffer.extend(chunk)

    def command(self, command):
        self.sock.sendall(command.encode() + b'\n\n')


def listen(config, stop, emit, connector=socket.create_connection):
    validate_config(config)
    while not stop.is_set():
        sock = None
        try:
            if config.get('password_file'):
                path = Path(config['password_file'])
                if path.stat().st_size > 4096:
                    raise ValueError('ESL credential file too large')
                secret = path.read_text().strip()
            else:
                secret = os.environ.get(config['password_env'], '')
            if not secret or any(c in secret for c in '\r\n\x00'):
                raise ValueError('ESL credential missing or malformed')
            sock = connector((config.get('host', '127.0.0.1'), config.get('port', 8021)), timeout=3)
            sock.settimeout(3)
            conn = Connection(sock)
            if conn.frame()[0].get('content-type') != 'auth/request':
                raise ValueError('Unexpected ESL handshake')
            conn.command('auth ' + secret)
            secret = ''
            if not conn.frame()[0].get('reply-text', '').startswith('+OK'):
                raise ValueError('ESL authentication failed')
            conn.command('event plain ' + EVENTS)
            if not conn.frame()[0].get('reply-text', '').startswith('+OK'):
                raise ValueError('ESL subscription failed')
            emit({'schema_version': '1.0', 'evidence': 'fs_event_status', 'observed_at': iso(), 'status': 'connected'})
            sock.settimeout(.5)
            while not stop.is_set():
                try:
                    head, body = conn.frame()
                except socket.timeout:
                    continue
                if head.get('content-type') == 'text/event-plain':
                    item = record(headers(body.decode('utf-8', 'replace')))
                    if item:
                        emit(item)
        except (OSError, ValueError, EOFError):
            # Never serialize authentication responses, secret paths, or exception payloads.
            emit({'schema_version': '1.0', 'evidence': 'fs_event_gap', 'observed_at': iso(),
                  'reason': 'ESL disconnected, unavailable or authentication/subscription failed; no replay guaranteed'})
        finally:
            if sock is not None:
                sock.close()
        stop.wait(2)
