"""Self-contained remote capture agent. Only stdlib plus colocated esl.py is required."""
import argparse
from datetime import datetime
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import stat
import struct
import subprocess
import threading
import time

try:
    from . import esl
except ImportError:
    import esl


def atomic(path, value, owner=None):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.part')
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n')
    temporary.chmod(0o600)
    if owner and os.geteuid() == 0:
        os.chown(temporary, *owner)
    temporary.replace(path)


def capture_command(config, directory):
    seconds, interval = config['seconds'], config['segment_seconds']
    slots = config.get('ring_files', 32) if config['mode'] == 'ring' else math.ceil(seconds / interval) + 1
    if not 2 <= slots <= 4096:
        raise ValueError('采集分片数须为 2–4096；增加分片间隔或缩短采集时长')
    budget = config['max_mib'] * 1048576
    # dumpcap switches *after* crossing the byte threshold. Reserve a full packet
    # and header in every slot; do not substitute snaplen for actual packet sizes.
    kilobytes = (budget // slots - config['snaplen'] - 512) // 1000
    if kilobytes < 1:
        raise ValueError('文件额度不足以容纳分片数和 snaplen；增加额度或分片间隔')
    args = ['dumpcap', '-q', '-P', '-i', config.get('interface', 'any'), '-f', config['bpf'],
            '-s', str(config['snaplen']), '-b', f'duration:{interval}', '-b', f'filesize:{kilobytes}',
            '-b', 'printname:stdout',
            '-a', f'duration:{seconds}', '-w', str(Path(directory) / 'capture.pcap')]
    args += ['-b' if config['mode'] == 'ring' else '-a', f'files:{slots}']
    return args


def bounds(path):
    """Read classic PCAP times without decoding payloads or loading the file."""
    size = Path(path).stat().st_size
    with open(path, 'rb') as stream:
        head = stream.read(24)
        formats = {b'\xd4\xc3\xb2\xa1': ('<', 1e6), b'\xa1\xb2\xc3\xd4': ('>', 1e6),
                   b'\x4d\x3c\xb2\xa1': ('<', 1e9), b'\xa1\xb2\x3c\x4d': ('>', 1e9)}
        if len(head) != 24 or head[:4] not in formats:
            raise ValueError('Invalid PCAP header')
        endian, resolution = formats[head[:4]]
        first = last = None
        count = truncated = 0
        while True:
            header = stream.read(16)
            if not header:
                break
            if len(header) != 16:
                raise ValueError('Truncated PCAP record')
            sec, sub, captured, wire = struct.unpack(endian + 'IIII', header)
            if captured > 1048576 or captured > wire or stream.tell() + captured > size:
                raise ValueError('Invalid PCAP record length')
            when = sec + sub / resolution
            first = when if first is None else min(first, when)
            last = when if last is None else max(last, when)
            count += 1
            truncated += captured < wire
            stream.seek(captured, 1)
    return {'first_epoch': first, 'last_epoch': last, 'packets': count, 'bytes': size,
            'truncated_packets': truncated}


def copy_stable(source, target, owner=None, signature=None):
    digest = hashlib.sha256()
    temporary = Path(str(target) + '.part')
    with open(source, 'rb') as reader, temporary.open('xb') as writer:
        before = os.fstat(reader.fileno())
        if signature and (before.st_size, before.st_mtime_ns) != tuple(signature):
            raise ValueError('Capture rotated before freeze')
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('Only regular closed capture files can be frozen')
        while True:
            block = reader.read(1024 * 1024)
            if not block:
                break
            writer.write(block)
            digest.update(block)
        after = os.fstat(reader.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        temporary.unlink()
        raise ValueError('Capture changed during freeze')
    temporary.chmod(0o600)
    if owner and os.geteuid() == 0:
        os.chown(temporary, *owner)
    temporary.replace(target)
    return digest.hexdigest()


def capture_statistics(text):
    rows = re.findall(r"Packets received/dropped on interface '[^\n]*?':\s*(\d+)/(\d+)"
                      r"(?:\s+\(pcap:(\d+)/dumpcap:(\d+)/flushed:(\d+)/ps_ifdrop:(\d+)\))?", text)
    detailed = bool(rows) and all(r[2] for r in rows)
    return {'statistics_available': bool(rows),
            'capture_dropped_packets': sum(int(r[1]) for r in rows) if rows else None,
            'kernel_dropped_packets': sum(int(r[2]) for r in rows) if detailed else None,
            'dumpcap_dropped_packets': sum(int(r[3]) for r in rows) if detailed else None,
            'flushed_packets': sum(int(r[4]) for r in rows) if detailed else None,
            'interface_dropped_packets': sum(int(r[5]) for r in rows) if detailed else None}


class Agent:
    def __init__(self, directory, config):
        self.root, self.config = Path(directory), config
        self.owner = (config.get('uid', os.getuid()), config.get('gid', os.getgid()))
        self.stop = threading.Event()
        self.events_lock = threading.Lock()
        self.closed_lock = threading.Lock()
        self.closed_names = {}
        self.closed_notifications = 0
        self.refresh = queue.Queue(maxsize=500)
        self.states = {}
        self.cache = {}
        self.seen_files = set()
        self.evicted = False
        self.event_truncated = False
        self.file_errors = set()
        self.finished = None
        self.capture_health = {'kernel_dropped_packets': None, 'statistics_available': False}
        self.started = time.time()
        self.process = None
        self.running = False
        self.errors = []
        self.forced_reason = None
        for name in ('spool', 'requests', 'replies', 'frozen'):
            p = self.root / name
            p.mkdir(exist_ok=True)
            p.chmod(0o700)
            self.own(p)

    def own(self, path):
        if os.geteuid() == 0:
            os.chown(path, *self.owner)

    def emit(self, value):
        value = dict(value, host=self.config['name'])
        uid = value.get('uuid')
        with self.events_lock:
            if uid:
                previous = self.states.get(uid, {})
                for key in ('call_id', 'caller', 'callee', 'peer_uuid', 'correlation_id'):
                    if not value.get(key) and previous.get(key):
                        value[key] = previous[key]
                if value.get('flow') and previous.get('flow') != value['flow']:
                    value['media_changed'] = True
                if value.get('call_id'):
                    self.states[uid] = dict(value)
                    if not value.get('flow') and previous.get('flow'):
                        self.states[uid]['flow'] = previous['flow']
                if value.get('event') in ('CHANNEL_HANGUP_COMPLETE', 'CHANNEL_DESTROY'):
                    self.states.pop(uid, None)
                elif value.get('evidence') == 'fs_event' and value.get('event') != 'SNAPSHOT':
                    try:
                        self.refresh.put_nowait(uid)
                    except queue.Full:
                        self.event_truncated = True
            if len(self.states) > 20000:
                self.states.pop(next(iter(self.states)))
                self.event_truncated = True
            path = self.root / 'events.jsonl'
            if path.exists() and path.stat().st_size > 8 * 1048576:
                old = self.root / 'events.previous.jsonl'
                if old.exists():
                    old.unlink()
                    self.event_truncated = True
                path.replace(old)
            with path.open('a') as stream:
                stream.write(json.dumps(value, ensure_ascii=False) + '\n')
            path.chmod(0o600)
            self.own(path)

    def snapshot(self, selected=None):
        try:
            fs = self.config.get('fs_cli', 'fs_cli')
            if selected is None:
                cp = subprocess.run([fs, '-x', 'show channels as json'], capture_output=True, text=True, timeout=3)
                rows = json.loads(cp.stdout)['rows']
                if cp.returncode:
                    raise ValueError('FS query failed')
                if not isinstance(rows, list) or any(not isinstance(r, dict) or not isinstance(r.get('uuid'), str) for r in rows):
                    raise ValueError('Invalid FS channels result')
                ids = sorted({str(esl.UUID(r['uuid'])) for r in rows})
                limit = self.config.get('max_channels', 100)
                offset = self.config.setdefault('_snapshot_offset', 0) % max(1, len(ids))
                selected = (ids[offset:] + ids[:offset])[:limit]
                self.config['_snapshot_offset'] += limit
                if len(ids) > limit:
                    self.emit({'schema_version': '1.0', 'evidence': 'fs_event_gap', 'observed_at': esl.iso(),
                               'reason': 'Snapshot channel limit; rotating sample'})
            deadline = time.monotonic() + 8
            for uid in selected:
                if self.stop.is_set():
                    break
                if time.monotonic() >= deadline:
                    self.emit({'schema_version': '1.0', 'evidence': 'fs_event_gap', 'observed_at': esl.iso(),
                               'reason': 'FS snapshot time budget exceeded'})
                    break
                cp = subprocess.run([fs, '-x', 'uuid_dump ' + str(esl.UUID(uid))], capture_output=True, text=True, timeout=2)
                if cp.returncode or cp.stdout.startswith('-ERR'):
                    self.emit({'schema_version': '1.0', 'evidence': 'fs_event_gap', 'observed_at': esl.iso(),
                               'reason': 'FS UUID query unavailable; leg may have ended before refresh'})
                    continue
                values = esl.headers(cp.stdout)
                values.update({'unique-id': uid, 'event-name': 'SNAPSHOT'})
                item = esl.record(values)
                if item:
                    item.update(evidence='fs_snapshot', window_seconds=self.config.get('snapshot_seconds', 10))
                    self.emit(item)
        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
            self.emit({'schema_version': '1.0', 'evidence': 'fs_event_gap', 'observed_at': esl.iso(),
                       'reason': 'FS snapshot unavailable or budget exceeded'})

    def observe(self):
        interval = self.config.get('snapshot_seconds', 10)
        next_snapshot = 0
        while not self.stop.is_set():
            if interval and time.monotonic() >= next_snapshot:
                self.snapshot()
                next_snapshot = time.monotonic() + interval
            pending = set()
            while len(pending) < 4:
                try:
                    pending.add(self.refresh.get_nowait())
                except queue.Empty:
                    break
            if pending:
                self.snapshot(sorted(pending))
            self.stop.wait(.5)

    def files(self):
        entries = []
        for p in (self.root / 'spool').glob('*.pcap'):
            try:
                info = p.lstat()
                if stat.S_ISREG(info.st_mode):
                    entries.append((info.st_mtime_ns, p))
            except FileNotFoundError:
                self.evicted = True
        all_files = [p for _, p in sorted(entries)]
        current = {p.name for p in all_files}
        if self.seen_files - current:
            self.evicted = True
        if self.config['mode'] == 'ring':
            count = self.config.get('ring_files', 32)
            if self.closed_notifications > count or (self.closed_notifications >= count and self.process and self.process.poll() is None):
                self.evicted = True
        self.seen_files |= current
        self.seen_files = set(sorted(self.seen_files)[-10000:])
        if self.process and self.process.poll() is None:
            # A file can be open yet stable, and two files can have equal mtimes.
            # Only dumpcap's native post-close notification establishes closure.
            with self.closed_lock:
                all_files = [p for p in all_files if p.name in self.closed_names]
        result = []
        for p in all_files:
            try:
                signature = (p.stat().st_size, p.stat().st_mtime_ns)
                if self.cache.get(p.name, {}).get('signature') != signature:
                    self.cache[p.name] = {**bounds(p), 'signature': signature, 'file': p.name}
                    self.own(p)
                result.append(dict(self.cache[p.name]))
            except FileNotFoundError:
                self.evicted = True
            except (ValueError, OSError):
                self.file_errors.add(p.name)
                self.file_errors = set(sorted(self.file_errors)[-100:])
        self.cache = {k: v for k, v in self.cache.items() if k in current}
        return result

    def mark_closed(self, name):
        path = Path(name)
        if path.parent != self.root / 'spool' or path.suffix != '.pcap':
            return
        with self.closed_lock:
            self.closed_names[path.name] = True
            self.closed_notifications += 1
            if len(self.closed_names) > 4096:
                self.closed_names.pop(next(iter(self.closed_names)))

    def observe_closed_files(self):
        for line in iter(lambda: self.process.stdout.readline(4096), b''):
            self.mark_closed(line.decode('utf-8', 'replace').strip())

    def observe_capture_log(self):
        # Even with -q, dumpcap prints each rotated filename to stderr. Cap that
        # stream too; high traffic must not turn a bounded ring into a full disk.
        path = self.root / 'capture.log'
        try:
            with path.open('ab') as initial:
                stream = initial
                try:
                    for block in iter(lambda: self.process.stderr.readline(65536), b''):
                        if stream.tell() + len(block) > 1048576:
                            stream.close()
                            path.replace(self.root / 'capture.previous.log')
                            stream = path.open('wb')
                            path.chmod(0o600)
                            self.own(path)
                        stream.write(block)
                        stream.flush()
                finally:
                    stream.close()
        except OSError:
            self.errors.append('Capture log unavailable; stopping to preserve bounded capture')
            self.forced_reason = 'capture_log_failed'
            self.stop.set()

    def status(self):
        files = self.files()
        alive = self.running or (self.process is not None and self.process.poll() is None)
        elapsed = (self.finished or time.time()) - self.started
        rc = self.process.poll() if self.process else None
        state = 'running' if alive else ('complete' if rc == 0 and elapsed >= self.config['seconds'] - 1 else 'partial')
        if self.forced_reason and not alive:
            state = 'stopped' if self.forced_reason == 'requested_stop' else 'failed'
        if state == 'complete' and (self.file_errors or self.capture_health['kernel_dropped_packets']
                                    or self.capture_health.get('capture_dropped_packets')
                                    or self.capture_health.get('interface_dropped_packets')
                                    or any(f['truncated_packets'] for f in files)):
            state = 'partial'
        result = {'schema_version': '1.0', 'tool': 'capture-agent', 'status': state, 'started_epoch': self.started,
                  'updated_epoch': time.time(), 'elapsed_seconds': elapsed, 'capture_exit_code': rc,
                  'mode': self.config['mode'], 'name': self.config['name'], 'remote_dir': str(self.root),
                  'closed_files': files, 'evicted': self.evicted, 'events_truncated': self.event_truncated,
                  'esl_enabled': bool(self.config.get('esl')), 'errors': self.errors[-10:],
                  'reason': self.forced_reason, 'size_policy': 'native_file_bytes',
                  'captured_until_epoch': self.finished, 'invalid_files': sorted(self.file_errors),
                  'capture_health': self.capture_health,
                  'closed_file_notifications': self.closed_notifications,
                  'warnings': ['冻结仅包含已关闭分片；首末包时间不是连续无丢包的证明。']}
        atomic(self.root / 'status.json', result, self.owner)
        return result

    def freeze(self, request):
        start, end = float(request['from_epoch']), float(request['to_epoch'])
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end):
            raise ValueError('Invalid freeze time window')
        token = request['id']
        if not re.fullmatch('[a-f0-9]{32}', token):
            raise ValueError('Invalid request ID')
        files = [row for row in self.files() if row['first_epoch'] is not None
                 and row['last_epoch'] >= start and row['first_epoch'] <= end]
        # Reserve space for both bounded event logs and the manifest, as well as PCAPs.
        with self.events_lock:
            event_bytes = sum(p.stat().st_size for p in (self.root / 'events.previous.jsonl', self.root / 'events.jsonl') if p.exists())
        used = sum(p.stat().st_size for p in (self.root / 'frozen').rglob('*') if p.is_file())
        if used + sum(r['bytes'] for r in files) + event_bytes + 65536 + 2048 * len(files) > self.config.get('frozen_mib', self.config['max_mib']) * 1048576:
            raise ValueError('Frozen evidence quota exhausted; release a previously fetched freeze first')
        dest = self.root / 'frozen' / token
        dest.mkdir(mode=0o700)
        self.own(dest)
        meta = {'schema_version': '1.0', 'tool': 'capture-batch-host', 'name': self.config['name'],
                'host': self.config.get('host', self.config['name']), 'sensor_id': self.config['sensor_id'],
                'bpf': self.config['bpf'], 'sip_ports': self.config.get('sip_ports', [5060]),
                'files': [], 'errors': [], 'warnings': [], 'requested_window': [start, end],
                'snapshot_seconds': self.config.get('snapshot_seconds', 10), 'status': 'complete', 'warnings_informational': True,
                'remote_dir': str(self.root), 'freeze_id': token}
        for i, row in enumerate(files, 1):
            try:
                target = dest / f'part-{i:06d}.pcap'
                digest = copy_stable(self.root / 'spool' / row['file'], target, self.owner, row['signature'])
                meta['files'].append({k: v for k, v in row.items() if k not in ('signature', 'file')})
                meta['files'][-1].update(file=target.name, sha256=digest, has_packet_data=row['packets'] > 0)
            except (ValueError, OSError):
                Path(str(target) + '.part').unlink(missing_ok=True)
                meta['errors'].append({'file': row['file'], 'error': 'Closed fragment expired or changed during freeze'})
        # Keep the most recent mapping preceding the window, plus events inside it.
        preceding, preceding_media, selected, preceding_health = {}, {}, [], None
        invalid_events = 0
        with self.events_lock:
            for p in (self.root / 'events.previous.jsonl', self.root / 'events.jsonl'):
                if not p.exists():
                    continue
                for line in p.read_text().splitlines():
                    try:
                        item = json.loads(line)
                        when = datetime.fromisoformat(item['observed_at']).timestamp()
                    except (ValueError, KeyError, TypeError, OverflowError, OSError):
                        invalid_events += 1
                        continue
                    if when < start and item.get('uuid'):
                        preceding[item['uuid']] = item
                        if item.get('flow'):
                            preceding_media[item['uuid']] = item
                    elif when < start and item.get('evidence') in ('fs_event_gap', 'fs_event_status'):
                        preceding_health = item
                    elif start <= when <= end:
                        selected.append(item)
            if preceding_health and preceding_health.get('evidence') == 'fs_event_gap':
                selected.insert(0, preceding_health)
            if invalid_events:
                selected.append({'schema_version': '1.0', 'evidence': 'fs_event_gap', 'observed_at': esl.iso(start),
                                 'reason': 'Invalid or truncated event records', 'invalid_records': invalid_events})
            baseline = list(preceding.values())
            baseline.extend(item for uid, item in preceding_media.items() if preceding[uid] != item)
            baseline.sort(key=lambda item: datetime.fromisoformat(item['observed_at']).timestamp())
            target = dest / 'events.jsonl'
            body = ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in baseline + selected).encode('utf-8')
            # The live event log may have grown while large PCAPs were copied.
            # Recheck exact bytes; keep the PCAP evidence and a gap receipt if the
            # requested mapping no longer fits the reserved frozen quota.
            reserved = used + sum(f['bytes'] for f in meta['files']) + 65536 + 2048 * len(files)
            if reserved + len(body) > self.config.get('frozen_mib', self.config['max_mib']) * 1048576:
                baseline = []
                selected = [{'schema_version': '1.0', 'evidence': 'fs_event_gap', 'observed_at': esl.iso(start),
                             'reason': 'Mapping grew beyond frozen quota while copying captures'}]
                body = (json.dumps(selected[0]) + '\n').encode('utf-8')
            target.write_bytes(body)
            target.chmod(0o600)
            self.own(target)
        meta['events'] = {'file': 'events.jsonl', 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'bytes': target.stat().st_size}
        state = self.status()
        first = min((r['first_epoch'] for r in files), default=None)
        last = max((r['last_epoch'] for r in files), default=None)
        partial = bool(meta['errors']) or not files or self.event_truncated or bool(self.file_errors)
        partial |= any(r.get('truncated_packets') for r in files)
        partial |= bool(state.get('capture_health', {}).get('kernel_dropped_packets'))
        partial |= bool(state.get('capture_health', {}).get('capture_dropped_packets'))
        partial |= bool(state.get('capture_health', {}).get('interface_dropped_packets'))
        partial |= start < self.started or end > (state.get('captured_until_epoch') or state['updated_epoch'])
        partial |= self.evicted and (first is None or start < first)
        partial |= state['status'] == 'running' and (last is None or end > last)
        partial |= state['status'] in ('failed', 'partial')
        if any(r.get('evidence') == 'fs_event_gap' for r in selected):
            partial = True
            meta['warnings'].append('所选窗口存在 FS 事件/快照缺口。')
        meta.update(status='partial' if partial else 'complete', available_packet_window=[first, last],
                    event_records=len(selected) + len(baseline), esl_enabled=bool(self.config.get('esl')),
                    capture_health=state.get('capture_health', {}), invalid_files=sorted(self.file_errors),
                    capture_status=state['status'], events_truncated=self.event_truncated)
        meta['warnings'].append('按整片冻结，可能包括窗口两侧邻近流量；所有原始来源独立保留。')
        if partial:
            meta['warnings'].append('窗口或事件覆盖不完整；检查留存范围、状态和每文件结果。')
        atomic(dest / 'host.json', meta, self.owner)
        return {'status': meta['status'], 'remote_export': str(dest), 'manifest': meta}

    def requests(self):
        for path in sorted((self.root / 'requests').glob('*.json'))[:32]:
            if path.is_symlink() or path.stat().st_size > 4096 or not re.fullmatch('[a-f0-9]{32}.json', path.name):
                continue
            try:
                req = json.loads(path.read_text())
                token = path.stem
                if req.get('id') != token:
                    raise ValueError('Request identity mismatch')
                if req.get('action') == 'stop':
                    self.forced_reason = 'requested_stop'
                    self.stop.set()
                    result = {'status': 'stopping'}
                elif req.get('action') == 'freeze':
                    # Give the active fragment a chance to close, never copy it live.
                    if self.process and self.process.poll() is None and time.time() < req['to_epoch'] + self.config['segment_seconds'] + 2:
                        # Byte-based rotation may already cover the requested end.
                        # Waiting the full timer interval could overwrite that evidence.
                        if not any(f['last_epoch'] is not None and f['last_epoch'] >= req['to_epoch'] for f in self.files()):
                            continue
                    result = self.freeze(req)
                elif req.get('action') == 'release':
                    freeze_id = req.get('freeze_id', '')
                    if not re.fullmatch('[a-f0-9]{32}', freeze_id):
                        raise ValueError('Invalid freeze ID')
                    shutil.rmtree(self.root / 'frozen' / freeze_id)
                    result = {'status': 'released', 'freeze_id': freeze_id}
                else:
                    raise ValueError('Unknown action')
            except (ValueError, OSError, KeyError, TypeError):
                result = {'status': 'error', 'error': 'Request invalid, window unavailable or frozen quota exhausted'}
            atomic(self.root / 'replies' / path.name, result, self.owner)
            path.unlink()

    def terminate_capture(self):
        if self.process and self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGINT)
            except ProcessLookupError:
                return
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait()

    def run(self):
        threads = []
        self.running = True
        if self.config.get('esl'):
            esl.validate_config(self.config['esl'])
        log = self.root / 'capture.log'
        log.touch(mode=0o600)
        self.own(log)
        try:
            self.process = subprocess.Popen(capture_command(self.config, self.root / 'spool'),
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            threads = [threading.Thread(target=target, daemon=True) for target in
                       (self.observe_closed_files, self.observe_capture_log, self.observe)]
            if self.config.get('esl'):
                threads.append(threading.Thread(target=esl.listen, args=(self.config['esl'], self.stop, self.emit), daemon=True))
            for thread in threads:
                thread.start()
            deadline = time.monotonic() + self.config['seconds'] + 10
            while not self.stop.is_set() and self.process.poll() is None:
                self.status()
                self.requests()
                self.stop.wait(.25)
                if time.monotonic() > deadline:
                    self.forced_reason = 'remote_deadline'
                    break
        except (OSError, ValueError):
            self.forced_reason = 'agent_or_capture_failed'
            self.errors.append('Unable to start or maintain capture; inspect capture.log on the host')
        finally:
            self.terminate_capture()
            self.finished = time.time()
            self.stop.set()
            for thread in threads:
                thread.join(timeout=12)
                if thread.is_alive():
                    self.event_truncated = True
            if self.process:
                self.process.stdout.close()
                self.process.stderr.close()
            with log.open('rb') as reader:
                reader.seek(max(0, reader.seek(0, 2) - 65536))
                self.capture_health = capture_statistics(reader.read().decode('utf-8', 'replace'))
            self.running = False
            self.status()
        # Once stopped, the immutable spool remains available through one-shot CLI requests.


def main():
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=('run', 'freeze', 'release'))
    p.add_argument('directory')
    p.add_argument('--request')
    args = p.parse_args()
    root = Path(args.directory)
    config = json.loads((root / 'config.json').read_text())
    agent = Agent(root, config)
    if args.action == 'run':
        agent.run()
    else:
        state = json.loads((root / 'status.json').read_text())
        if state['status'] == 'running':
            raise ValueError('Running jobs require queued requests')
        agent.started = state['started_epoch']
        agent.evicted = state['evicted']
        agent.event_truncated = state.get('events_truncated', False)
        agent.file_errors = set(state.get('invalid_files', []))
        agent.capture_health = state.get('capture_health', agent.capture_health)
        req = json.loads(Path(args.request).read_text())
        if args.action == 'freeze':
            # Preserve final status rather than replacing it with a new empty agent.
            agent.status = lambda: state
            with (root / 'operation.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                result = agent.freeze(req)
        else:
            if not re.fullmatch('[a-f0-9]{32}', req['freeze_id']):
                raise ValueError('Invalid freeze ID')
            with (root / 'operation.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                shutil.rmtree(root / 'frozen' / req['freeze_id'])
                result = {'status': 'released'}
        atomic(root / 'replies' / (req['id'] + '.json'), result, agent.owner)
        Path(args.request).unlink(missing_ok=True)


if __name__ == '__main__':
    main()
