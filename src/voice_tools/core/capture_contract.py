"""Number capture selection and versioned progress file contract."""
import os
from .files import read_json, write_json

TERMINAL = ('complete', 'partial', 'failed')


def selectors(callers=(), callees=()):
    result = {}
    for key, values in (('callers', callers), ('callees', callees)):
        if isinstance(values, str) or len(values) > 32:
            raise ValueError('每个号码维度最多 32 个值')
        if any(not isinstance(v, str) or not v or len(v) > 128 or
               any(c.isspace() or ord(c) < 32 for c in v) for v in values):
            raise ValueError('号码须为 1–128 字符的精确值，不含空白或控制字符')
        result[key] = list(dict.fromkeys(values))
    if not any(result.values()):
        raise ValueError('至少指定一个 --caller 或 --callee')
    return result


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
