"""Stream verified ZIP64 bundles; copy original evidence without changing it."""
import csv
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import tempfile
import zipfile

from voice_tools import __version__
from voice_tools.core.files import read_json, sha256, write_json
from .contract import no_secrets, relative, validate

MAX_FILES = 100000
MAX_METADATA = 16 * 1024 * 1024
MAX_BYTES = 100 * 1024**3
OMIT = {'.git', '.venv', '__pycache__', '.DS_Store', '.env', 'credentials.json', 'id_rsa', 'id_ed25519'}
MODEL_OR_KEY = {'.pt', '.pth', '.ckpt', '.safetensors', '.pem', '.key'}


def inside(root, path):
    path = Path(path)
    if path.is_symlink(): raise ValueError(f'不接受符号链接：{path.name}')
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents: raise ValueError(f'输入引用越出 --root：{path}')
    # Reject symlink components too, not just the final file.
    for parent in [path, *path.parents]:
        if parent.resolve() == root: break
        if parent.is_symlink(): raise ValueError('输入路径包含符号链接')
    return resolved


def json_references(data):
    """Only documented file contracts create file dependencies; remote paths do not."""
    if not isinstance(data, dict): return
    if isinstance(data.get('steps'), list):
        no_secrets(data)
        for step in data['steps']:
            if isinstance(step, dict) and step.get('action') in ('play', 'play_media'):
                yield step.get('file')
    if data.get('kind') == 'sip_batch':
        for job in data.get('jobs', []): yield from json_references(job.get('scenario'))
    if data.get('kind') == 'sipp_load': yield from json_references(data.get('scenario'))
    if 'audio_sha256' in data and isinstance(data.get('audio'), str): yield data['audio']
    if data.get('kind') == 'gap_evidence':
        recordings = data.get('recordings')
        if not isinstance(recordings, list): raise ValueError('间隙关联清单 recordings 无效')
        for recording in recordings:
            if not isinstance(recording, dict): raise ValueError('间隙录音绑定须为对象')
            for kind in ('rtp', 'nisqa'):
                bindings = recording.get(kind, [])
                if not isinstance(bindings, list): raise ValueError('间隙旁证绑定须为列表')
                for binding in bindings:
                    if not isinstance(binding, dict): raise ValueError('间隙旁证须为对象')
                    for key in ('report', 'timeline', 'results', 'provenance'):
                        if key in binding: yield binding[key]
        return
    if data.get('kind') == 'rtp_timeline':
        chunks = data.get('chunks')
        if not isinstance(chunks, list): raise ValueError('RTP 分片清单无效')
        for chunk in chunks:
            if not isinstance(chunk, dict) or 'path' not in chunk: raise ValueError('RTP 分片引用无效')
            yield chunk['path']
        return
    if data.get('kind') == 'nisqa_provenance':
        yield data.get('results')
        return
    # QA records and frozen event manifests carry the original local input identity.
    if 'sample_id' in data and isinstance(data.get('input'), str): yield data['input']
    for entry in data.get('recordings', []) if isinstance(data.get('recordings'), list) else []:
        if isinstance(entry, dict):
            for key in ('audio', 'input', 'events'):
                if isinstance(entry.get(key), str): yield entry[key]


def discover(root, inputs):
    found, pending, warnings, aliases = {}, [root / value for value in inputs.values()], [], {}
    total = 0
    while pending:
        requested = pending.pop()
        path = inside(root, requested)
        if path.is_dir():
            if not any(path.iterdir()): raise ValueError(f'输入目录为空：{path.name}')
            for child in sorted(path.iterdir()):
                if child.name in OMIT: raise ValueError(f'输入目录包含不应打包的文件：{child.name}；请缩小素材目录')
                pending.append(child)
            continue
        if not path.is_file(): raise ValueError(f'缺少引用的输入文件：{path}')
        aliases[str(Path(requested).absolute())] = 'inputs/' + path.relative_to(root).as_posix()
        if path in found: continue
        if path.name in OMIT or path.name.startswith('.env.') or path.name == 'nisqa.tar' or path.suffix.lower() in MODEL_OR_KEY:
            raise ValueError(f'不能打包模型或凭据：{path.name}')
        with path.open('rb') as probe:
            if re.search(rb'-----BEGIN [A-Z ]*PRIVATE KEY-----', probe.read(2048)):
                raise ValueError(f'不能打包私钥：{path.name}')
        total += path.stat().st_size
        if len(found) >= MAX_FILES or total > MAX_BYTES: raise ValueError('任务包超过 100000 文件或 100 GiB 展开额度')
        found[path] = 'inputs/' + path.relative_to(root).as_posix()
        if path.suffix.lower() == '.wav':
            for suffix in ('.events.json', '.provenance.json'):
                sidecar = path.with_suffix(suffix)
                if sidecar.exists(): pending.append(sidecar)
        if path.suffix.lower() in ('.json', '.jsonl'):
            if path.stat().st_size > MAX_METADATA:
                raise ValueError(f'结构化输入超过 16 MiB，无法可靠收集依赖，请拆分：{path.name}')
            try:
                records = [read_json(path)] if path.suffix == '.json' else [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
            except (ValueError, UnicodeError):
                continue  # Invalid business data remains evidence and fails in its own tool.
            for record in records:
                no_secrets(record)
                for ref in json_references(record):
                    if not isinstance(ref, str) or not ref: raise ValueError(f'无效素材引用：{path.name}')
                    pending.append(path.parent / ref)
        if path.suffix.lower() == '.csv':
            with path.open(encoding='utf-8-sig', newline='') as stream:
                reader = csv.DictReader(stream)
                if {'reference', 'degraded'} <= set(reader.fieldnames or []):
                    for row in reader:
                        for key in ('reference', 'degraded'):
                            if row.get(key): pending.append(path.parent / row[key])
        if path.suffix.lower() in ('.sqlite', '.db'):
            try:
                with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
                    for (ref,) in db.execute('SELECT path FROM sources'):
                        if not isinstance(ref, str) or not ref: raise ValueError('会话索引来源路径无效')
                        pending.append(path.parent / ref)
            except sqlite3.Error as error:
                raise ValueError(f'会话索引依赖无法读取：{error}') from error
    return found, warnings, aliases


def create_archive(output, entries, kind, metadata=None):
    output = Path(output)
    if output.exists(): raise ValueError('输出包已存在，拒绝覆盖')
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix='.bundle-', delete=False) as stream:
            temp = Path(stream.name)
        manifest = {'schema_version': '1.0', 'kind': kind, 'tool_version': __version__, 'files': [], **(metadata or {})}
        with zipfile.ZipFile(temp, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=3, allowZip64=True) as archive:
            for name, source in sorted(entries.items()):
                relative(name)
                if name == 'manifest.json': raise ValueError('manifest.json 为保留路径')
                source = Path(source)
                if not source.is_file() or source.is_symlink(): raise ValueError('包中包含非普通文件')
                digest, size = hashlib.sha256(), 0
                with source.open('rb') as reader, archive.open(name, 'w', force_zip64=True) as writer:
                    for block in iter(lambda: reader.read(1024 * 1024), b''):
                        writer.write(block); digest.update(block); size += len(block)
                manifest['files'].append({'path': name, 'bytes': size, 'sha256': digest.hexdigest()})
            archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, allow_nan=False))
        os.link(temp, output)  # Atomic create: a concurrent pack never overwrites it.
        return {'path': str(output.resolve()), 'files': len(entries), 'sha256': sha256(output), 'kind': kind}
    finally:
        if temp: temp.unlink(missing_ok=True)


def pack(task_path, root, output):
    task = validate(read_json(task_path))
    root = Path(root).resolve()
    found, warnings, aliases = discover(root, task.get('inputs', {}))
    source_map = {**aliases, **{str(path): name for path, name in found.items()}}
    input_map = {key: 'inputs/' + inside(root, root / value).relative_to(root).as_posix()
                 for key, value in task.get('inputs', {}).items()}
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'task.json'; write_json(path, task)
        mapping = Path(folder) / 'input-map.json'; write_json(mapping, {'inputs': input_map, 'sources': source_map})
        return create_archive(output, {**{name: path for path, name in found.items()}, 'task.json': path, 'input-map.json': mapping},
                              'voice_task', {'task_id': task['id'], 'warnings': warnings})


def inspect_archive(path, expected=None, extract_to=None):
    """Validate all members before opening any business file; never ZipFile.extractall."""
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        names = [x.filename for x in infos]
        if len(infos) > MAX_FILES + 4 or len(names) != len(set(names)) or len({n.casefold() for n in names}) != len(names):
            raise ValueError('包中有过多、重复或大小写冲突的文件')
        if 'manifest.json' not in names or archive.getinfo('manifest.json').file_size > MAX_METADATA:
            raise ValueError('包缺少有效清单')
        manifest = json.loads(archive.read('manifest.json'))
        if not isinstance(manifest, dict) or manifest.get('schema_version') != '1.0' or manifest.get('kind') not in ('voice_task', 'voice_result') or not isinstance(manifest.get('tool_version'), str):
            raise ValueError('不支持的包类型／版本')
        if expected and manifest['kind'] != expected: raise ValueError('任务包／结果包类型不匹配')
        rows = manifest.get('files')
        if not isinstance(rows, list): raise ValueError('文件清单格式无效')
        listed = {}
        for row in rows:
            if not isinstance(row, dict) or set(row) != {'path', 'bytes', 'sha256'}: raise ValueError('文件清单行无效')
            relative(row['path'])
            if isinstance(row['bytes'], bool) or not isinstance(row['bytes'], int) or row['bytes'] < 0 or not isinstance(row['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', row['sha256']):
                raise ValueError('文件清单大小／摘要格式无效')
            if row['path'] in listed: raise ValueError('清单重复路径')
            listed[row['path']] = row
        if set(listed) != set(names) - {'manifest.json'}: raise ValueError('清单与包文件不一致')
        total = 0
        for info in infos:
            relative(info.filename)
            mode = (info.external_attr >> 16) & 0o170000
            if info.is_dir() or mode not in (0, stat.S_IFREG) or info.flag_bits & 1:
                raise ValueError('不支持目录条目、链接、设备或加密文件')
            total += info.file_size
            if total > MAX_BYTES: raise ValueError('包展开后超过 100 GiB')
            if info.filename != 'manifest.json' and listed[info.filename]['bytes'] != info.file_size: raise ValueError('文件大小与清单不符')
        if extract_to and shutil.disk_usage(Path(extract_to).parent).free < total + 64 * 1024**2:
            raise ValueError('磁盘空间不足，无法完整展开结果')
        for info in infos:
            digest = hashlib.sha256()
            writer = None
            try:
                if extract_to:
                    target = Path(extract_to) / info.filename
                    target.parent.mkdir(parents=True, exist_ok=True)
                    writer = target.open('xb')
                with archive.open(info) as reader:
                    for block in iter(lambda: reader.read(1024 * 1024), b''):
                        digest.update(block)
                        if writer: writer.write(block)
                if info.filename != 'manifest.json' and digest.hexdigest() != listed[info.filename]['sha256']:
                    raise ValueError(f'文件摘要不符：{info.filename}')
            finally:
                if writer: writer.close()
        return manifest


def unpack(path, output, expected=None):
    output = Path(output)
    if output.exists(): raise ValueError('解包目标必须不存在')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix='.unpack-') as folder:
        try: manifest = inspect_archive(path, expected, folder)
        except (zipfile.BadZipFile, RuntimeError) as error: raise ValueError(f'压缩包损坏或格式不受支持：{error}') from error
        os.rename(folder, output)
    return manifest


def relocate_inputs(root, mapping):
    """Working copies only; the archive and original input files stay unchanged."""
    replacements = {old: str(root / new) for old, new in mapping['sources'].items()}
    def change(value):
        if isinstance(value, str):
            return replacements.get(value, replacements.get(str(Path(value)), replacements.get(str(Path(value).resolve()), value)) if Path(value).is_absolute() else value)
        if isinstance(value, list): return [change(v) for v in value]
        if isinstance(value, dict): return {k: change(v) for k, v in value.items()}
        return value
    # New evidence references are relative; their content hashes must survive relocation.
    protected = set()
    for candidate in (root / 'inputs').rglob('*.json'):
        if candidate.stat().st_size > MAX_METADATA:
            continue
        try:
            value = read_json(candidate)
        except (ValueError, UnicodeError):
            continue
        preserve_evidence = isinstance(value, dict) and (
            value.get('kind') in ('gap_evidence', 'rtp_timeline', 'nisqa_provenance')
            or 'output_events' in value
            or ('output_sha256' in value and 'time_mapping' in value))
        if preserve_evidence:
            protected.add(candidate.resolve())
            for ref in json_references(value):
                protected.add((candidate.parent / ref).resolve())
    modified = []
    for path in (root / 'inputs').rglob('*') if (root / 'inputs').exists() else []:
        if path.resolve() in protected:
            continue
        if path.suffix in ('.json', '.jsonl') and path.stat().st_size <= MAX_METADATA:
            try:
                if path.suffix == '.json':
                    before = read_json(path); after = change(before)
                    if before != after: write_json(path, after); modified.append(str(path.relative_to(root)))
                else:
                    before = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
                    after = change(before)
                    if before != after:
                        path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in after)); modified.append(str(path.relative_to(root)))
            except (ValueError, UnicodeError): pass
        elif path.suffix.lower() in ('.sqlite', '.db'):
            with sqlite3.connect(path) as db:
                for old, new in replacements.items(): db.execute('UPDATE sources SET path=? WHERE path=?', (new, old))
            modified.append(str(path.relative_to(root)))
        elif path.suffix == '.csv':
            try:
                with path.open(encoding='utf-8-sig', newline='') as stream:
                    reader = csv.DictReader(stream); fields = reader.fieldnames
                    if not {'reference', 'degraded'} <= set(fields or []): continue
                    rows = list(reader)
                changed = change(rows)
                if rows != changed:
                    with path.open('w', encoding='utf-8', newline='') as stream:
                        writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader(); writer.writerows(changed)
                    modified.append(str(path.relative_to(root)))
            except (ValueError, UnicodeError): pass
    return {'paths': replacements, 'rewritten_working_copies': modified}


def task_mapping(root, task):
    """A self-consistent ZIP checksum is not authorization to read host paths."""
    mapping = read_json(root / 'input-map.json')
    if not isinstance(mapping, dict) or set(mapping) != {'inputs', 'sources'} or not all(isinstance(mapping[k], dict) for k in mapping):
        raise ValueError('输入映射格式无效')
    expected = {key: 'inputs/' + value for key, value in task.get('inputs', {}).items()}
    if mapping['inputs'] != expected: raise ValueError('输入映射与任务不一致')
    for name in [*mapping['inputs'].values(), *mapping['sources'].values()]:
        relative(name)
        if not name.startswith('inputs/') or not (root / name).exists(): raise ValueError('输入映射越界或缺失')
    for name in mapping['sources']:
        if not isinstance(name, str) or not Path(name).is_absolute(): raise ValueError('来源映射须为原始绝对路径')
    return mapping
