"""Server-side session bundle worker; reads the capture file protocol."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

from voice_tools.core.files import read_json, sha256, write_json
from voice_tools.core.capture_contract import publish
from .store import safe_path


def select_calls(index, selection, limit):
    """Only FS role observations or initial INVITEs establish calling direction."""
    from voice_tools.tools.sessions.store import connect
    callers, callees = set(selection['callers']), set(selection['callees'])
    matches, eligible, total = [], 0, 0
    current, evidence = None, None
    with connect(index) as db:
        rows = db.execute('SELECT call_id,kind,data FROM observations o JOIN sources s ON s.id=o.source_id '
                          'ORDER BY call_id,epoch,o.id')
        for row in rows:
            if row['call_id'] != current:
                if evidence is not None:
                    total += 1
                    if len(matches) < limit:
                        matches.append(evidence)
                current, evidence = row['call_id'], None
            data = json.loads(row['data'])
            basis = 'fs_roles' if row['kind'] == 'fs' else 'sip_initial_invite'
            if row['kind'] != 'fs' and not (data.get('method') == 'INVITE' and not data.get('to_tag')):
                continue
            eligible += 1
            if (not callers or data.get('caller') in callers) and (not callees or data.get('callee') in callees):
                evidence = {'call_id': current, 'caller': data.get('caller'), 'callee': data.get('callee'),
                            'uuid': data.get('uuid'), 'basis': basis}
        if evidence is not None:
            total += 1
            if len(matches) < limit:
                matches.append(evidence)
    return matches, total, eligible


def process(root, config):
    from voice_tools.tools.sessions.store import build
    from voice_tools.tools.sessions.export import export
    state = read_json(root / 'status.json')
    if state['status'] == 'running':
        raise ValueError('采集尚未停止，拒绝拆包')
    work = root / 'number-work'
    work.mkdir(mode=0o700)
    files = state['closed_files']
    for row in files:
        path = safe_path(root / 'spool', row['file'])
        if list((path.stat().st_size, path.stat().st_mtime_ns)) != list(row['signature']):
            raise ValueError('停止后的原始分片发生变化，拒绝拆包')
        row['sha256'] = sha256(path)
    events = work / 'events.jsonl'
    invalid = 0
    with events.open('w', encoding='utf-8') as out:
        for path in (root / 'events.previous.jsonl', root / 'events.jsonl'):
            if not path.exists():
                continue
            for line in path.read_text().splitlines():
                try:
                    item = json.loads(line)
                    if not isinstance(item, dict):
                        raise ValueError('Invalid event')
                    out.write(json.dumps(item, ensure_ascii=False) + '\n')
                except (ValueError, TypeError):
                    invalid += 1
        if invalid or state.get('events_truncated'):
            out.write(json.dumps({'schema_version': '1.0', 'evidence': 'fs_event_gap',
                                 'reason': 'Event log rotated or contains invalid records'}) + '\n')
    # Reference stopped originals without making another full copy of the spool.
    host = {'schema_version': '1.0', 'tool': 'capture-batch-host', 'name': config['name'],
            'sensor_id': config['sensor_id'], 'status': state['status'],
            'warnings': state.get('warnings', []), 'warnings_informational': True,
            'sip_ports': config.get('sip_ports', [5060]),
            'files': [{**f, 'file': 'spool/' + f['file']} for f in files]}
    host['events'] = {'file': 'number-work/events.jsonl', 'sha256': sha256(events)}
    write_json(root / 'host.json', host)
    publish(root, config, status='indexing', capture_status=state['status'])
    summary = build(work / 'index', batches=[root], max_packets=config['max_packets'])
    matches, total, eligible = select_calls(work / 'index', config['selection'], config['max_sessions'])
    bundle = {'schema_version': '1.0', 'tool': 'capture-number-bundle', 'host': config['name'],
              'selection': config['selection'], 'capture_status': state['status'],
              'capture_health': state.get('capture_health', {}), 'index_partial': summary['partial'],
              'matched_sessions': total, 'eligible_observations': eligible, 'sessions': [], 'errors': [],
              'warnings': ['按服务器及精确 Call-ID 分开；号码不自动归一化，不跨 B2BUA 合并。',
                           '仅包含采集窗口内的候选媒体；窗口开始前/结束后的内容不在包中。',
                           '号码以 FS 主被叫字段或初始 INVITE 的 From/To user 匹配。']}
    partial = summary['partial'] or state['status'] != 'complete' or total > len(matches)
    if total > len(matches):
        bundle['warnings'].append('达到会话数量上限，部分匹配会话未导出。')
    if not eligible:
        bundle['warnings'].append('没有可用于确认主被叫方向的 FS 记录或初始 INVITE。')
        partial = True
    limit = config['bundle_mib'] * 1048576
    used = 0
    archive = root / 'sessions.zip.part'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, allowZip64=True) as package:
        for matched in matches:
            publish(root, config, status='exporting', matched_sessions=total, exported_sessions=len(bundle['sessions']))
            key = hashlib.sha256(matched['call_id'].encode()).hexdigest()
            target = work / key
            writing = False
            try:
                result = export(work / 'index', matched['call_id'], target, include_media=True, padding=2)
                paths = [target / f['file'] for f in result['files']] + [target / 'session.json']
                size = sum(p.stat().st_size for p in paths)
                # Bound both uncompressed evidence and the final ZIP, including metadata.
                if used + size + 1048576 > limit:
                    bundle['warnings'].append('达到打包额度，剩余会话未导出。')
                    partial = True
                    break
                if not result['files']:
                    raise ValueError('匹配到了会话，但没有可导出的 PCAP')
                members = [{'file': 'sessions/' + key + '/' + path.name,
                            'bytes': path.stat().st_size, 'sha256': sha256(path)} for path in paths]
                writing = True
                for path, member in zip(paths, members):
                    package.write(path, member['file'])
                used += size
                bundle['sessions'].append({**matched, 'directory': 'sessions/' + key,
                                           'partial': result['partial'], 'files': members})
                partial |= result['partial']
            except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                if writing:
                    # A ZIP write failure can leave an unregistered partial member.
                    # Do not publish that archive as a valid partial result.
                    raise
                partial = True
                bundle['errors'].append({'call_id': matched['call_id'], 'error': str(error)[:500]})
            finally:
                if target.exists():
                    shutil.rmtree(target)
        bundle.update(status='partial' if partial else 'complete', exported_sessions=len(bundle['sessions']),
                      omitted_sessions=total - len(bundle['sessions']))
        body = json.dumps(bundle, ensure_ascii=False, indent=2).encode()
        if used + len(body) > limit or len(body) > 4194304:
            raise ValueError('会话清单超过打包额度，原始抓包保留')
        package.writestr('manifest.json', body)
    if archive.stat().st_size > limit:
        archive.unlink()
        raise ValueError('压缩包超过额度，原始抓包保留')
    final = root / 'sessions.zip'
    archive.replace(final)
    final.chmod(0o600)
    if os.geteuid() == 0:
        os.chown(final, config['uid'], config['gid'])
    publish(root, config, status=bundle['status'], matched_sessions=total, exported_sessions=len(bundle['sessions']),
            omitted_sessions=bundle['omitted_sessions'], archive={'file': final.name, 'bytes': final.stat().st_size,
            'sha256': sha256(final)}, warnings=bundle['warnings'], errors=bundle['errors'][:20])


def main():
    root = Path(sys.argv[1])
    config = read_json(root / 'config.json')
    os.umask(0o077)
    try:
        process(root, config)
    except Exception as error:
        publish(root, config, status='failed', error=str(error)[:1000])
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
