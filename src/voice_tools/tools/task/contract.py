"""Task schema 1.0: typed parameters and explicit input/previous-step references."""
import math
from pathlib import PurePosixPath
import re

from .catalog import catalog

ID = re.compile(r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
SECRETS = {'password', 'token', 'auth_token', 'private_key', 'secret'}


def object_fields(value, allowed, label):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValueError(f'{label} 格式或字段无效')


def relative(value, glob=False):
    if not isinstance(value, str) or not value or '\\' in value or '\x00' in value:
        raise ValueError('路径必须为非空包内相对路径')
    path = PurePosixPath(value)
    if path.is_absolute() or value != path.as_posix() or value == '.' or '..' in path.parts or ':' in value or any(ord(c) < 32 for c in value) or (not glob and any(c in value for c in '*?[')):
        raise ValueError(f'路径不允许越界、绝对地址或未声明的通配符：{value}')
    return value


def no_secrets(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in SECRETS and item not in (None, ''):
                raise ValueError(f'配置不允许携带秘密值：{key}；请使用执行端环境变量或配置文件')
            no_secrets(item)
    elif isinstance(value, list):
        for item in value: no_secrets(item)


def references(value):
    if isinstance(value, dict):
        if 'input' in value or 'step' in value:
            yield value
        else:
            for item in value.values(): yield from references(item)
    elif isinstance(value, list):
        for item in value: yield from references(item)


def validate(task):
    object_fields(task, {'schema_version', 'id', 'title', 'inputs', 'steps'}, 'task')
    if task.get('schema_version') != '1.0' or not isinstance(task.get('id'), str) or not ID.fullmatch(task['id']):
        raise ValueError('任务需要 schema_version=1.0 及字母开头的 id')
    if not isinstance(task.get('title'), str) or not 1 <= len(task['title']) <= 200:
        raise ValueError('任务标题须为 1–200 个字符')
    inputs = task.get('inputs', {})
    if not isinstance(inputs, dict) or len(inputs) > 1000:
        raise ValueError('inputs 须为不超过 1000 项的名称与路径映射')
    for name, path in inputs.items():
        if not ID.fullmatch(name): raise ValueError('输入名称无效')
        relative(path)
    steps = task.get('steps')
    if not isinstance(steps, list) or not 1 <= len(steps) <= 100:
        raise ValueError('steps 须包含 1–100 个步骤')
    known = set()
    for step in steps:
        object_fields(step, {'id', 'tool', 'action', 'params', 'depends_on', 'environment', 'timeout_s', 'title'}, 'step')
        sid = step.get('id', '')
        if not isinstance(sid, str) or not ID.fullmatch(sid) or sid in known: raise ValueError('步骤 id 无效或重复')
        key = f"{step.get('tool')}.{step.get('action')}"
        if key not in catalog(): raise ValueError(f'不支持的业务操作：{key}')
        params = step.get('params', {})
        if not isinstance(params, dict): raise ValueError('params 必须为对象')
        specs = {a['name']: a for a in catalog()[key]['arguments']}
        if set(params) - set(specs): raise ValueError(f'{sid} 含未知参数：{set(params)-set(specs)}')
        for name, value in params.items():
            a = specs[name]
            if a['role'] in ('runtime', 'output'):
                raise ValueError(f'{sid}.{name} 由执行端／输出目录管理，不能写入任务参数')
            values = value if isinstance(value, list) else [value]
            if a['repeatable'] and not isinstance(value, list): raise ValueError(f'{name} 必须是数组')
            if a['nargs'] is None and not a['repeatable'] and isinstance(value, list): raise ValueError(f'{name} 只接受单个值或引用')
            if isinstance(a['nargs'], int) and a['type'] != 'boolean' and not a['repeatable'] and len(values) != a['nargs']: raise ValueError(f'{name} 参数数量无效')
            if a['role'] == 'input_group':
                if not values or any(not isinstance(row, list) or len(row) < 2 or not isinstance(row[0], str) or any(not isinstance(ref, dict) for ref in row[1:]) for row in values):
                    raise ValueError(f'{name} 须为 [[采集点名称, 文件引用], ...]')
            elif a['role'] == 'input':
                if not values or any(not isinstance(v, dict) for v in values):
                    raise ValueError(f'{sid}.{name} 必须使用 input/step 文件引用')
            else:
                def scalar(v):
                    if isinstance(v, list):
                        for x in v: scalar(x)
                    elif not isinstance(v, (str, int, float, bool)) or (isinstance(v, float) and not math.isfinite(v)):
                        raise ValueError(f'{sid}.{name} 参数类型无效')
                    elif a['type'] == 'boolean' and not isinstance(v, bool): raise ValueError(f'{name} 必须为布尔值')
                    elif a['type'] in ('int', 'float') and (isinstance(v, bool) or not isinstance(v, (int, float))): raise ValueError(f'{name} 必须为数值')
                    elif a['type'] == 'int' and int(v) != v: raise ValueError(f'{name} 必须为整数')
                    elif 'choices' in a and v not in a['choices']: raise ValueError(f'{name} 不在允许值中')
                scalar(value)
        for ref in references(params):
            object_fields(ref, {'input', 'step', 'path'}, '文件引用')
            if ('input' in ref) == ('step' in ref): raise ValueError('文件引用必须且只能选择 input 或 step')
            if 'input' in ref and (not isinstance(ref['input'], str) or ref['input'] not in inputs): raise ValueError('引用了未声明的输入')
            if 'step' in ref and (not isinstance(ref['step'], str) or ref['step'] not in known): raise ValueError('只能引用前序步骤')
            if 'path' in ref: relative(ref['path'], glob=True)
        deps = step.get('depends_on', [])
        if not isinstance(deps, list) or any(not isinstance(d, str) or d not in known for d in deps): raise ValueError('depends_on 只能包含前序步骤')
        if 'environment' in step and (not isinstance(step['environment'], str) or not ID.fullmatch(step['environment'])): raise ValueError('环境名称无效')
        timeout = step.get('timeout_s', 3600)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 604800:
            raise ValueError('步骤时限须为 1–604800 秒')
        known.add(sid)
    no_secrets(task)
    return task
