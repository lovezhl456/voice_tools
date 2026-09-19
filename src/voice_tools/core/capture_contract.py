"""Number capture selection and versioned progress file contract."""
import os
import re
from .files import read_json, write_json

TERMINAL = ('complete', 'partial', 'failed')
MAX_BUNDLE_FILES = 50000
MAX_BUNDLE_MANIFEST_BYTES = 4 * 1048576
MAX_SESSION_MANIFEST_BYTES = 16 * 1048576


def selectors(callers=(), callees=()):
    result = {}
    for key, values in (('callers', callers), ('callees', callees)):
        if not isinstance(values, (list, tuple)) or len(values) > 32:
            raise ValueError('每个号码维度最多 32 个值')
        if any(not isinstance(v, str) or not v or len(v) > 128 or
               any(c.isspace() or ord(c) < 32 for c in v) for v in values):
            raise ValueError('号码须为 1–128 字符的精确值，不含空白或控制字符')
        result[key] = list(dict.fromkeys(values))
    if not any(result.values()):
        raise ValueError('至少指定一个 --caller 或 --callee')
    return result


def selection(value):
    if not isinstance(value, dict) or set(value) != {'callers', 'callees'}:
        raise ValueError('号码筛选条件格式无效')
    return selectors(value['callers'], value['callees'])


def matches(row, selected):
    return all(not selected[key] or row.get(field) in selected[key]
               for key, field in (('callers', 'caller'), ('callees', 'callee')))


def bundle_contract(value):
    if not isinstance(value, dict) or value.get('tool') != 'capture-number-bundle' or value.get('schema_version') != '1.0':
        raise ValueError('压缩包清单版本无效')
    selected = selection(value.get('selection'))
    sessions = value.get('sessions')
    if not isinstance(sessions, list) or len(sessions) > 1000 or value.get('status') not in ('complete', 'partial'):
        raise ValueError('压缩包会话列表或状态无效')
    counts = [value.get(k) for k in ('matched_sessions', 'exported_sessions', 'omitted_sessions')]
    if any(type(n) is not int or n < 0 for n in counts) or counts[1] != len(sessions) or counts[0] != counts[1] + counts[2]:
        raise ValueError('会话清单计数不一致')
    seen = set()
    for row in sessions:
        if not isinstance(row, dict):
            raise ValueError('会话条目格式无效')
        cid = row.get('call_id')
        if not isinstance(cid, str) or not 1 <= len(cid) <= 1024 or any(ord(c) < 32 for c in cid) or cid in seen:
            raise ValueError('会话 Call-ID 无效或重复')
        seen.add(cid)
        if row.get('basis') not in ('fs_roles', 'sip_initial_invite') or not matches(row, selected) or type(row.get('partial')) is not bool:
            raise ValueError('会话匹配依据或状态无效')
        files = row.get('files')
        if not isinstance(files, list) or len(files) < 2:
            raise ValueError('每个会话必须包含清单和 PCAP')
        for f in files:
            if (not isinstance(f, dict) or not isinstance(f.get('file'), str) or
                type(f.get('bytes')) is not int or f['bytes'] <= 0 or
                not isinstance(f.get('sha256'), str) or not re.fullmatch('[a-f0-9]{64}', f['sha256'])):
                raise ValueError('会话文件清单格式无效')
    if value['status'] == 'complete' and (counts[2] or value.get('errors') or value.get('index_partial') is not False
            or value.get('capture_status') != 'complete' or any(s['partial'] for s in sessions)):
        raise ValueError('完整状态与会话证据矛盾')
    return value


def publish(root, config, **values):
    path = root / 'number-status.json'
    state = read_json(path) if path.exists() else {'schema_version': '1.0', 'tool': 'capture-number-host',
        'name': config['name'], 'selection': config['selection']}
    state.update(values)
    temp = path.with_suffix('.part')
    write_json(temp, state)
    temp.chmod(0o600)
    if os.geteuid() == 0:
        os.chown(temp, config['uid'], config['gid'])
    temp.replace(path)
    return state
