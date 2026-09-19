"""SSH orchestration for private, bounded remote capture jobs."""
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
import math
from pathlib import Path
import re
import shlex
import threading
import time
from uuid import uuid4

from voice_tools import __version__
from voice_tools.core.files import new_output, read_json, sha256, write_json
from .batch import inventory
from .esl import validate_config
from .remote import capture_command
from .service import SSH, remote_directory


def epoch(value):
    if isinstance(value, (int, float)):
        result = float(value)
    else:
        when = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if when.tzinfo is None:
            raise ValueError('时间须含时区')
        result = when.timestamp()
    if not math.isfinite(result) or result < 0:
        raise ValueError('时间无效')
    return result


def encoded(value):
    return base64.b64encode(json.dumps(value, ensure_ascii=False).encode()).decode()


def remote_json(ssh, directory, name):
    remote_directory(directory)
    if not re.fullmatch(r'(?:status\.json|replies/[a-f0-9]{32}\.json)', name):
        raise ValueError('不支持的远端状态路径')
    raw = ssh.checked(['python3', '-c',
                       "from pathlib import Path; import sys,json,time; p=Path(sys.argv[1]); "
                       "assert p.stat().st_size <= 4194304; v=json.loads(p.read_text()); "
                       "v['_remote_read_epoch']=time.time(); print(json.dumps(v))", directory + '/' + name])
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('远端状态格式无效')
    return value


def settings(path, seconds, segment_seconds, max_mib, snaplen, snapshot_seconds, max_channels, mode, ring_files=32):
    if not 1 <= seconds <= 604800 or not 10 <= segment_seconds <= 3600 or not 1 <= max_mib <= 16384:
        raise ValueError('时限 1–604800 秒，分片 10–3600 秒，额度 1–16384 MiB')
    if not 64 <= snaplen <= 65535 or not 2 <= ring_files <= 4096:
        raise ValueError('snaplen 64–65535，环形分片数 2–4096')
    if not (snapshot_seconds == 0 or 5 <= snapshot_seconds <= 30) or not 1 <= max_channels <= 500:
        raise ValueError('快照间隔 0 或 5–30 秒，每轮最多 1–500 条腿')
    plans = []
    for host in inventory(path):
        if host.get('esl'):
            validate_config(host['esl'])
        config = {**host, 'seconds': seconds, 'segment_seconds': segment_seconds, 'max_mib': max_mib,
                  'frozen_mib': max_mib, 'snaplen': snaplen, 'snapshot_seconds': snapshot_seconds,
                  'max_channels': max_channels, 'mode': mode, 'ring_files': ring_files,
                  'sensor_id': host['name'] + ':' + host.get('interface', 'any')}
        capture_command(config, '/tmp/plan/spool')
        plans.append(config)
    return plans


def launch(config, ssh_factory=SSH, progress=None):
    ssh = ssh_factory(config['host'], config.get('ssh_port', 22), config.get('identity'))
    directory = '/tmp/voice-tools-' + uuid4().hex
    row = {'name': config['name'], 'host': config['host'], 'ssh_port': config.get('ssh_port', 22),
           'identity': config.get('identity'), 'sudo': config.get('sudo', False),
           'segment_seconds': config['segment_seconds'], 'seconds': config['seconds'],
           'status': 'preparing', 'remote_dir': directory}
    # Persist the intended path before the first remote mutation. A lost SSH
    # reply or interrupted startup must not leave an undiscoverable capture job.
    if progress:
        progress(row)
    try:
        prepared = ssh.checked(['sh', '-c', 'set -eu; umask 077; mkdir ' + shlex.quote(directory) + '; '
                               'printf "%s\\n%s\\n" "$(id -u)" "$(id -g)"'])
        uid, gid = prepared.splitlines()
        remote_directory(directory)
        config = {**config, 'uid': int(uid), 'gid': int(gid)}
        files = {name: Path(__file__).with_name(name).read_text() for name in ('remote.py', 'esl.py')}
        files['config.json'] = json.dumps(config)
        script = ("import base64,json,os,pathlib,sys; root=pathlib.Path(sys.argv[1]); "
                  "files=json.loads(base64.b64decode(sys.argv[2])); "
                  "[( (root/k).write_text(v), (root/k).chmod(0o600)) for k,v in files.items()]")
        ssh.checked(['python3', '-c', script, directory, encoded(files)])
        command = (['sudo', '-n'] if config.get('sudo') else []) + ['python3', directory + '/remote.py', 'run', directory]
        shell = 'umask 077; nohup ' + shlex.join(command) + ' < /dev/null > ' + shlex.quote(directory + '/agent.log') + ' 2>&1 &'
        ssh.checked(['sh', '-c', shell])
        deadline = time.monotonic() + 12
        while True:
            try:
                state = remote_json(ssh, directory, 'status.json')
                row.update(status=state['status'], state=state)
                break
            except (ValueError, OSError):
                if time.monotonic() >= deadline:
                    raise ValueError('远端任务未就绪；目录已保留，请检查 agent.log 和 dumpcap/sudo 权限')
                time.sleep(.25)
    except (ValueError, OSError) as error:
        row.update(status='failed', error=str(error))
    if progress:
        progress(row)
    return row


def start(config_path, output, seconds=86400, segment_seconds=60, max_mib=512, snaplen=65535,
          snapshot_seconds=10, max_channels=100, dry_run=False, mode='ring', ring_files=32, ssh_factory=SSH):
    plans = settings(config_path, seconds, segment_seconds, max_mib, snaplen, snapshot_seconds, max_channels, mode, ring_files)
    output = new_output(output); output.chmod(0o700)
    data = {'schema_version': '1.0', 'tool': 'capture-ring-job', 'tool_version': __version__,
            'status': 'planned', 'mode': mode, 'hosts': [], 'plans': plans,
            'warnings': ['原始包环形额度与冻结证据额度各自为 max-mib；事件日志另限 16 MiB/主机。',
                         '额度限制的是保留数据，不能保证高流量下的历史保留时长；冻结只复制已关闭的分片。',
                         '分片重组需要保留地址范围内后续 IPv4 分片及 IPv6 TCP/UDP，端口筛选在解码后进一步收敛。']}
    write_json(output / 'job.json', data)
    if dry_run:
        return data
    lock = threading.Lock()
    def progress(row):
        with lock:
            data['hosts'] = [h for h in data['hosts'] if h['name'] != row['name']] + [dict(row)]
            temporary = output / 'job.json.part'
            write_json(temporary, data)
            temporary.replace(output / 'job.json')
    try:
        with ThreadPoolExecutor(max_workers=len(plans)) as pool:
            jobs = [pool.submit(launch, p, ssh_factory, progress) for p in plans]
            for future in as_completed(jobs):
                future.result()
    except KeyboardInterrupt:
        data['status'] = 'interrupted'
        write_json(output / 'job.json', data)
        raise
    data['status'] = 'running' if all(h['status'] in ('running', 'complete') for h in data['hosts']) else 'partial'
    write_json(output / 'job.json', data)
    return data


def load_job(path):
    path = Path(path); path = path / 'job.json' if path.is_dir() else path
    data = read_json(path)
    if data.get('tool') != 'capture-ring-job' or data.get('schema_version') != '1.0':
        raise ValueError('不支持的远端任务清单')
    rows = data.get('hosts', [])
    if not 1 <= len(rows) <= 16:
        raise ValueError('任务没有实际启动的主机，或主机数超过 16；离线计划不能用于远端操作')
    names = [r.get('name') for r in rows]
    if any(not isinstance(n, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', n) for n in names) or len(set(names)) != len(names):
        raise ValueError('任务的主机名称无效或重复')
    return data


def status(job, ssh_factory=SSH):
    data = load_job(job)
    results = []
    for row in data['hosts']:
        try:
            ssh = ssh_factory(row['host'], row.get('ssh_port', 22), row.get('identity'))
            state = remote_json(ssh, row['remote_dir'], 'status.json')
            if state['status'] == 'running' and state.get('_remote_read_epoch', time.time()) - state['updated_epoch'] > 30:
                state['status'] = 'unresponsive'
            results.append({'name': row['name'], **state})
        except (ValueError, OSError, KeyError) as error:
            results.append({'name': row['name'], 'status': 'failed', 'error': str(error)})
    return {'hosts': results, 'partial': any(r['status'] in ('partial', 'failed', 'unresponsive') for r in results)}


def request(row, action, values=None, ssh_factory=SSH):
    ssh = ssh_factory(row['host'], row.get('ssh_port', 22), row.get('identity'))
    directory = remote_directory(row['remote_dir'])
    token = uuid4().hex
    payload = {'id': token, 'action': action, **(values or {})}
    target = directory + '/requests/' + token + '.json'
    script = ("import base64,pathlib,sys; p=pathlib.Path(sys.argv[1]); t=p.with_suffix('.part'); "
              "t.write_bytes(base64.b64decode(sys.argv[2])); t.chmod(0o600); t.replace(p)")
    ssh.checked(['python3', '-c', script, target, encoded(payload)])
    state = remote_json(ssh, directory, 'status.json')
    if state['status'] != 'running':
        if action == 'stop':
            ssh.checked(['python3', '-c', 'import pathlib,sys; pathlib.Path(sys.argv[1]).unlink(missing_ok=True)', target])
            return {'status': state['status']}
        argv = (['sudo', '-n'] if row.get('sudo') else []) + ['python3', directory + '/remote.py', action, directory, '--request', target]
        ssh.checked(argv, timeout=180)
    deadline = time.monotonic() + row['segment_seconds'] + 30
    while True:
        try:
            response = remote_json(ssh, directory, 'replies/' + token + '.json')
            if response.get('status') == 'error':
                raise ValueError(response['error'])
            response['request_id'] = token
            return response
        except (ValueError, OSError) as error:
            if time.monotonic() >= deadline or 'quota' in str(error) or 'Request invalid' in str(error):
                raise ValueError('远端请求未完成；任务与已冻结证据保留：' + str(error)) from error
            # If the agent exited between enqueue and processing, finalize the request once.
            state = remote_json(ssh, directory, 'status.json')
            if state['status'] != 'running' and action == 'stop':
                ssh.checked(['python3', '-c', 'import pathlib,sys; pathlib.Path(sys.argv[1]).unlink(missing_ok=True)', target])
                return {'status': state['status']}
            if state['status'] != 'running':
                argv = (['sudo', '-n'] if row.get('sudo') else []) + ['python3', directory + '/remote.py', action, directory, '--request', target]
                ssh.checked(argv, timeout=180)
            time.sleep(.5)


def stop(job, ssh_factory=SSH):
    results = []
    for row in load_job(job)['hosts']:
        try:
            results.append({'name': row['name'], **request(row, 'stop', ssh_factory=ssh_factory)})
        except (ValueError, OSError, KeyError) as error:
            results.append({'name': row['name'], 'status': 'failed', 'error': str(error)})
    return {'hosts': results, 'partial': any(r['status'] == 'failed' for r in results)}


def download(ssh, directory, filename, expected, output):
    if not re.fullmatch(r'(?:part-[0-9]{6}\.pcap|events\.jsonl)', filename):
        raise ValueError('冻结清单文件名无效')
    if not re.fullmatch('[0-9a-f]{64}', expected):
        raise ValueError('冻结文件摘要无效')
    remote = directory + '/' + filename
    before = ssh.checked(['sha256sum', remote]).split()[0]
    target = output / filename; temporary = output / (filename + '.part')
    ssh.copy(remote, temporary)
    after = ssh.checked(['sha256sum', remote]).split()[0]
    if not before == expected == after == sha256(temporary):
        raise ValueError('冻结文件在传输前后摘要不一致')
    temporary.chmod(0o600); temporary.replace(target)


def fetch(job, output, start_time, end_time, ssh_factory=SSH, per_host_windows=None):
    a, b = epoch(start_time), epoch(end_time)
    if not a < b or b > time.time() + 5:
        raise ValueError('冻结窗口须为已发生的有效时间段')
    output = new_output(output); output.chmod(0o700)
    batch = {'schema_version': '1.0', 'tool': 'capture-batch', 'status': 'complete', 'hosts': [], 'requested_window': [a, b]}
    def one(row):
        dest = new_output(output / row['name']); dest.chmod(0o700)
        meta = {'schema_version': '1.0', 'tool': 'capture-batch-host', 'name': row['name'], 'files': [], 'status': 'failed'}
        try:
            window = (per_host_windows or {}).get(row['name'], (a, b))
            reply = request(row, 'freeze', {'from_epoch': window[0], 'to_epoch': window[1]}, ssh_factory)
            meta = reply['manifest']
            directory = reply['remote_export']
            if directory != row['remote_dir'] + '/frozen/' + reply['request_id']:
                raise ValueError('远端冻结目录与请求身份不匹配')
            ssh = ssh_factory(row['host'], row.get('ssh_port', 22), row.get('identity'))
            for file in meta['files']:
                download(ssh, directory, file['file'], file['sha256'], dest)
            if meta.get('events'):
                download(ssh, directory, 'events.jsonl', meta['events']['sha256'], dest)
        except (ValueError, OSError, KeyError) as error:
            meta.update(status='partial', error=str(error))
        write_json(dest / 'host.json', meta)
        return {'name': row['name'], 'manifest': row['name'] + '/host.json', 'status': meta['status']}
    rows = load_job(job)['hosts']
    with ThreadPoolExecutor(max_workers=max(1, len(rows))) as pool:
        for result in pool.map(one, rows):
            batch['hosts'].append(result)
            if result['status'] != 'complete':
                batch['status'] = 'partial'
            write_json(output / 'batch.json', batch)
    return batch


def release(job, host, freeze_id, ssh_factory=SSH):
    row = next((h for h in load_job(job)['hosts'] if h['name'] == host), None)
    if row is None or not re.fullmatch('[a-f0-9]{32}', freeze_id):
        raise ValueError('主机或 freeze-id 无效')
    return request(row, 'release', {'freeze_id': freeze_id}, ssh_factory)


def run_window(config_path, output, seconds=300, segment_seconds=60, max_mib=512, snaplen=65535,
               snapshot_seconds=10, max_channels=100, dry_run=False):
    # Keep job, frozen export and recoverable remote locations even on interruption.
    output = new_output(output); output.chmod(0o700)
    started = time.time()
    try:
        job = start(config_path, output / 'job', seconds, segment_seconds, max_mib, snaplen,
                    snapshot_seconds, max_channels, dry_run, mode='window')
    except KeyboardInterrupt:
        stop(output / 'job')
        raise ValueError('启动中断已尝试停止；job.json 保留预分配路径，请检查 status/stop/fetch') from None
    if dry_run:
        write_json(output / 'batch.json', {'schema_version': '1.0', 'tool': 'capture-batch',
                                         'status': 'planned', 'hosts': [], 'plans': job['plans']})
        return read_json(output / 'batch.json')
    deadline = time.monotonic() + seconds + 35
    try:
        while time.monotonic() < deadline:
            current = status(output / 'job')
            if all(h['status'] != 'running' for h in current['hosts']):
                break
            time.sleep(.5)
    except KeyboardInterrupt:
        stop(output / 'job')
        raise ValueError('本地中断已尝试停止远端任务；job.json 保留，请检查 status/stop/fetch') from None
    # Fetch into a child directory, then point the top-level manifest at it.
    current = status(output / 'job')
    windows = {h['name']: (h['started_epoch'], h.get('captured_until_epoch') or h['updated_epoch'])
               for h in current['hosts'] if 'started_epoch' in h}
    result = fetch(output / 'job', output / 'capture', started, time.time(), per_host_windows=windows)
    result['requested_duration_seconds'] = seconds
    for row in result['hosts']:
        row['manifest'] = 'capture/' + row['manifest']
    write_json(output / 'batch.json', result)
    return result
