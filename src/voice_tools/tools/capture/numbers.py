"""Deploy bounded number capture, then verify and download session archives."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import shlex
import stat
import threading
import time
from uuid import uuid4
import zipfile

from voice_tools.core.files import new_output, read_json, sha256, write_json
from voice_tools.core.capture_contract import (TERMINAL, selectors, selection, bundle_contract,
    MAX_BUNDLE_FILES, MAX_BUNDLE_MANIFEST_BYTES, MAX_SESSION_MANIFEST_BYTES)
from .remote import atomic
from .ring import encoded, load_job, settings
from .service import SSH, remote_directory

MODULE = 'voice_tools.tools.capture.number_remote'


def runtime(target):
    package = Path(__file__).resolve().parents[2]
    modules = ['__init__.py', 'tools/__init__.py', 'core/__init__.py', 'core/files.py', 'core/packets.py', 'core/capture_contract.py',
        'tools/capture/__init__.py', 'tools/capture/remote.py', 'tools/capture/esl.py', 'tools/capture/number_remote.py',
        'tools/sessions/__init__.py', 'tools/sessions/store.py', 'tools/sessions/pcap.py',
        'tools/sessions/export.py', 'tools/sessions/media.py', 'tools/sessions/number_bundle.py']
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in modules:
            archive.write(package / name, 'voice_tools/' + name)
    target.chmod(0o600)


def command(row, action):
    root = remote_directory(row['remote_dir'])
    return (['sudo', '-n'] if row.get('sudo') else []) + ['env', 'PYTHONPATH=' + root + '/runtime.zip',
                                                        'python3', '-m', MODULE, action, root]


def launch(config, package, progress, ssh_factory=SSH):
    directory = '/tmp/voice-tools-' + uuid4().hex
    row = {k: config[k] for k in ('name', 'host', 'seconds', 'segment_seconds', 'processing_seconds', 'bundle_mib', 'selection')}
    row.update({k: config.get(k) for k in ('identity', 'sudo')})
    row.update(ssh_port=config.get('ssh_port', 22), remote_dir=directory, status='preparing')
    progress(row)
    try:
        ssh = ssh_factory(row['host'], row['ssh_port'], row['identity'])
        owner = ssh.checked(['sh', '-c', 'set -eu; umask 077; mkdir ' + shlex.quote(directory) +
                             '; printf "%s\\n%s\\n" "$(id -u)" "$(id -g)"'])
        uid, gid = map(int, owner.splitlines())
        ssh.upload(package, directory + '/runtime.zip')
        remote_config = {**config, 'uid': uid, 'gid': gid}
        ssh.checked(['python3', '-c', 'import base64,pathlib,sys; p=pathlib.Path(sys.argv[1]); '
                     'p.write_bytes(base64.b64decode(sys.argv[2])); p.chmod(0o600)',
                     directory + '/config.json', encoded(remote_config)])
        ssh.checked(command(row, 'check'))
        shell = 'umask 077; nohup ' + shlex.join(command(row, 'run')) + ' < /dev/null > ' + directory + '/runner.log 2>&1 &'
        ssh.checked(['sh', '-c', shell])
        row['status'] = 'running'
    except (ValueError, OSError) as error:
        row.update(status='failed', error=str(error))
    progress(row)
    return row


def remote_state(ssh, directory):
    root = remote_directory(directory)
    raw = ssh.checked(['python3', '-c', 'import pathlib,sys; p=pathlib.Path(sys.argv[1]); '
                       'assert p.stat().st_size <= 4194304; print(p.read_text())', root + '/number-status.json'])
    state = json.loads(raw)
    if (not isinstance(state, dict) or state.get('tool') != 'capture-number-host' or state.get('schema_version') != '1.0'
            or state.get('status') not in (*TERMINAL, 'capturing', 'indexing', 'exporting')):
        raise ValueError('号码抓包状态格式无效')
    return state


def verify_archive(path, info, budget):
    if (not isinstance(info, dict) or info.get('file') != 'sessions.zip' or type(info.get('bytes')) is not int or
        not 0 < info['bytes'] <= budget or path.stat().st_size != info['bytes'] or sha256(path) != info.get('sha256')):
        raise ValueError('压缩包大小或 SHA-256 校验失败')
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        names = [i.filename for i in entries]
        name_set = set(names)
        if len(entries) > MAX_BUNDLE_FILES or len(names) != len(name_set) or sum(i.file_size for i in entries) > budget:
            raise ValueError('压缩包展开额度或条目数无效')
        for name in names:
            p = PurePosixPath(name)
            if p.is_absolute() or '..' in p.parts or '\\' in name or (name != 'manifest.json' and
                    not re.fullmatch(r'sessions/[a-f0-9]{64}/(?:session.json|[0-9]{4,}\.pcapng)', name)):
                raise ValueError('压缩包含非会话路径')
            kind = stat.S_IFMT(archive.getinfo(name).external_attr >> 16)
            if kind not in (0, stat.S_IFREG):
                raise ValueError('压缩包只允许普通文件')
        if 'manifest.json' not in names or archive.getinfo('manifest.json').file_size > MAX_BUNDLE_MANIFEST_BYTES:
            raise ValueError('压缩包清单缺失或过大')
        manifest = bundle_contract(json.loads(archive.read('manifest.json')))
        expected = {'manifest.json'}
        for session in manifest['sessions']:
            directory = 'sessions/' + hashlib.sha256(session['call_id'].encode()).hexdigest()
            if session['directory'] != directory:
                raise ValueError('会话目录与 Call-ID 不符')
            for item in session['files']:
                name = item['file']
                if name in expected or PurePosixPath(name).parent.as_posix() != directory or name not in name_set:
                    raise ValueError('会话文件清单与压缩包不符')
                expected.add(name)
                digest = hashlib.sha256()
                with archive.open(name) as stream:
                    for block in iter(lambda: stream.read(1048576), b''):
                        digest.update(block)
                if item['bytes'] != archive.getinfo(name).file_size or item['sha256'] != digest.hexdigest():
                    raise ValueError('会话文件摘要不符')
            inner_path = directory + '/session.json'
            pcaps = {f['file'].split('/')[-1]: f for f in session['files'] if f['file'].endswith('.pcapng')}
            if not pcaps or inner_path not in expected or archive.getinfo(inner_path).file_size > MAX_SESSION_MANIFEST_BYTES:
                raise ValueError('会话 PCAP/清单缺失或超过额度')
            inner = json.loads(archive.read(inner_path))
            if (not isinstance(inner, dict) or inner.get('schema_version') != '1.0' or inner.get('tool') != 'sessions-export'
                    or inner.get('call_id') != session['call_id'] or inner.get('partial') is not session['partial']
                    or not isinstance(inner.get('files'), list) or len(inner['files']) != len(pcaps)):
                raise ValueError('会话内外清单不一致')
            seen_files = set()
            for entry in inner['files']:
                if not isinstance(entry, dict) or not isinstance(entry.get('file'), str):
                    raise ValueError('会话内文件条目格式无效')
                name = entry['file']
                if (name not in pcaps or name in seen_files or entry.get('sha256') != pcaps[name]['sha256']
                        or type(entry.get('packets')) is not int or entry['packets'] < 1):
                    raise ValueError('会话内 PCAP 文件清单不一致')
                seen_files.add(name)
        if expected != name_set:
            raise ValueError('压缩包含未登记文件')
    return manifest


def collect_host(row, output, stop, wait, ssh_factory=SSH):
    folder = new_output(output / row['name']); folder.chmod(0o700)
    result = {**row, 'status': 'pending'}
    result.pop('error', None)
    ssh = ssh_factory(row['host'], row.get('ssh_port', 22), row.get('identity'))
    try:
        deadline = time.monotonic() + (row['seconds'] + row['processing_seconds'] + 90 if wait else 0)
        last_error = None
        while not stop.is_set():
            try:
                state = remote_state(ssh, row['remote_dir'])
                result.update(remote_status=state)
                if state['status'] in TERMINAL:
                    break
                last_error = None
            except (ValueError, OSError) as error:
                last_error = row.get('error') or str(error)
                if row.get('status') == 'failed':
                    raise ValueError(last_error) from error
            if time.monotonic() >= deadline:
                raise ValueError(last_error or '远端尚未完成；使用 fetch-number 重试取回')
            stop.wait(1)
        else:
            raise ValueError('本地等待中断；远端限时任务继续，可再次取回')
        if not state.get('archive'):
            raise ValueError(state.get('error', '远端未生成压缩包，原始抓包保留'))
        info = state['archive']
        if not isinstance(info, dict) or info.get('file') != 'sessions.zip' or type(info.get('bytes')) is not int or not 0 < info['bytes'] <= row['bundle_mib'] * 1048576:
            raise ValueError('远端压缩包路径或额度无效')
        target = folder / 'sessions.zip.part'
        ssh.copy(remote_directory(row['remote_dir']) + '/sessions.zip', target, stop=stop)
        manifest = verify_archive(target, info, row['bundle_mib'] * 1048576)
        if manifest.get('status') != state['status'] or manifest.get('host') != row['name']:
            raise ValueError('压缩包与任务状态不符')
        if (selection(manifest['selection']) != selection(state.get('selection')) or
                ('selection' in row and selection(manifest['selection']) != selection(row['selection']))):
            raise ValueError('压缩包号码条件与原始任务不符')
        target.replace(folder / 'sessions.zip'); (folder / 'sessions.zip').chmod(0o600)
        write_json(folder / 'manifest.json', manifest)
        result.update(status=state['status'], archive=row['name'] + '/sessions.zip', sha256=info['sha256'],
                      matched_sessions=manifest['matched_sessions'], exported_sessions=manifest['exported_sessions'])
    except (ValueError, OSError, KeyError, TypeError, zipfile.BadZipFile) as error:
        result.update(status='failed', error=str(error))
        (folder / 'sessions.zip.part').unlink(missing_ok=True)
    atomic(folder / 'host.json', result)
    return result


def collect(data, output, wait, ssh_factory):
    result = {'schema_version': '1.0', 'tool': 'capture-number', 'status': 'collecting',
              'selection': data['selection'], 'hosts': [], 'job': str(output / 'job.json')}
    stop = threading.Event()
    try:
        with ThreadPoolExecutor(max_workers=len(data['hosts'])) as pool:
            futures = {pool.submit(collect_host, {**row, 'selection': data['selection']}, output, stop, wait, ssh_factory): row
                       for row in data['hosts']}
            try:
                for future in as_completed(futures):
                    try:
                        result['hosts'].append(future.result())
                    except (ValueError, OSError, KeyError, TypeError) as error:
                        result['hosts'].append({**futures[future], 'status': 'failed', 'error': str(error)})
                    atomic(output / 'number.json', result)
            except KeyboardInterrupt:
                stop.set()
                raise
    except KeyboardInterrupt:
        result['status'] = 'interrupted'; atomic(output / 'number.json', result)
        raise ValueError('本地中断，远端限时任务继续；使用 capture fetch-number --job <本次输出目录> --out <新目录> 取回') from None
    result['status'] = 'complete' if all(h['status'] == 'complete' for h in result['hosts']) else 'partial'
    atomic(output / 'number.json', result)
    return result


def start(inventory, output, callers=(), callees=(), seconds=300, segment_seconds=60, max_mib=512,
          snaplen=65535, snapshot_seconds=10, max_channels=100, max_sessions=1000, bundle_mib=512,
          max_packets=1000000, processing_seconds=1800, dry_run=False, ssh_factory=SSH):
    selection = selectors(callers, callees)
    if not 1 <= max_sessions <= 1000 or not 2 <= bundle_mib <= 2048 or not 1 <= max_packets <= 5000000:
        raise ValueError('会话上限 1–1000，打包额度 2–2048 MiB，索引包数 1–5000000')
    if not 30 <= processing_seconds <= 86400 or max_mib > 2048:
        raise ValueError('远端处理时限 30–86400 秒，原始采集额度最多 2048 MiB')
    plans = settings(inventory, seconds, segment_seconds, max_mib, snaplen, snapshot_seconds, max_channels, 'window')
    if math.ceil(seconds / segment_seconds) + 1 > 2048:
        raise ValueError('号码抓包最多 2048 分片，请增加分片间隔')
    for plan in plans:
        plan.update(selection=selection, max_sessions=max_sessions, bundle_mib=bundle_mib,
                    max_packets=max_packets, processing_seconds=processing_seconds)
    output = new_output(output); output.chmod(0o700)
    data = {'schema_version': '1.0', 'tool': 'capture-ring-job', 'purpose': 'capture-by-number',
            'status': 'planned', 'selection': selection, 'plans': plans, 'hosts': []}
    atomic(output / 'job.json', data)
    if dry_run:
        result = {**data, 'tool': 'capture-number'}
        atomic(output / 'number.json', result)
        return result
    package = output / 'runtime.zip'; runtime(package)
    lock = threading.Lock()
    def progress(row):
        with lock:
            data['hosts'] = [h for h in data['hosts'] if h['name'] != row['name']] + [dict(row)]
            atomic(output / 'job.json', data)
    try:
        with ThreadPoolExecutor(max_workers=len(plans)) as pool:
            futures = [pool.submit(launch, p, package, progress, ssh_factory) for p in plans]
            for future in as_completed(futures):
                future.result()
    except KeyboardInterrupt:
        data['status'] = 'interrupted'; atomic(output / 'job.json', data)
        raise ValueError('启动中断；job.json 已保留远端路径，可用 fetch-number 或 ring-stop 恢复') from None
    data['status'] = 'started'; atomic(output / 'job.json', data)
    return collect(data, output, True, ssh_factory)


def fetch(job, output, wait=False, ssh_factory=SSH):
    data = load_job(job)
    if data.get('purpose') != 'capture-by-number':
        raise ValueError('fetch-number 需要 by-number 生成的 job.json')
    selection(data.get('selection'))
    for row in data['hosts']:
        remote_directory(row['remote_dir'])
        if any(type(row.get(key)) is not int or not low <= row[key] <= high for key, low, high in
               (('bundle_mib', 2, 2048), ('processing_seconds', 30, 86400), ('seconds', 1, 604800))):
            raise ValueError('号码抓包任务额度无效')
    output = new_output(output); output.chmod(0o700)
    atomic(output / 'job.json', data)
    return collect(data, output, wait, ssh_factory)
