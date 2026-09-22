"""Strict v1 definition validation and canonical identity."""
import copy
import hashlib
import json
import re

from voice_tools.core.files import read_json
from .metrics import finite, parameters

SCHEMA_VERSION = '1.0'
OPERATORS = {'lt', 'le', 'gt', 'ge', 'eq', 'ne'}


def text(value, name, limit=200):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'{name} 必须为 1–{limit} 字符的非空文本')
    return value


def identifier(value, name):
    text(value, name, 80)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', value):
        raise ValueError(f'{name} 仅允许英文字母、数字、点、短横线和下划线')
    return value


def fields(obj, required, optional=(), name='配置'):
    if not isinstance(obj, dict):
        raise ValueError(f'{name} 必须为对象')
    if not set(required) <= set(obj) or set(obj) - set(required) - set(optional):
        raise ValueError(f'{name} 字段错误：必填 {sorted(required)}，可选 {sorted(optional)}')


def channel(value):
    if type(value) is not int or value not in (0, 1):
        raise ValueError('channel 必须为 0（左／单轨）或 1（右）；不复制缺失声道')
    return value


def interval(value, name):
    fields(value, ('start_s', 'end_s'), name=name)
    start = finite(value['start_s'], name + '.start_s', 0, 3600)
    end = finite(value['end_s'], name + '.end_s', 0, 3600)
    if end <= start:
        raise ValueError(f'{name} 的 end_s 必须大于 start_s')
    return value


def condition(value, metrics, depth=0, budget=None):
    budget = [0] if budget is None else budget
    budget[0] += 1
    if depth > 8 or budget[0] > 128 or not isinstance(value, dict):
        raise ValueError('条件必须为对象，最多 8 层、128 个节点')
    groups = set(value) & {'all', 'any', 'not'}
    if groups:
        if len(value) != 1:
            raise ValueError('all / any / not 节点只能包含一个组合字段')
        key = next(iter(groups))
        children = [value[key]] if key == 'not' else value[key]
        if not isinstance(children, list) or not children:
            raise ValueError('all / any 必须为非空条件列表')
        for child in children:
            condition(child, metrics, depth + 1, budget)
    else:
        fields(value, ('metric', 'op', 'value'), name='比较条件')
        if not isinstance(value['metric'], str) or value['metric'] not in metrics:
            raise ValueError('条件引用了不存在的指标')
        if not isinstance(value['op'], str) or value['op'] not in OPERATORS:
            raise ValueError('op 必须为 lt / le / gt / ge / eq / ne')
        finite(value['value'], '条件 value')


def validate(raw):
    config = copy.deepcopy(raw)
    fields(config, ('schema_version', 'id', 'version', 'name', 'metrics', 'rules'),
           ('description', 'scope', 'window'))
    if config['schema_version'] != SCHEMA_VERSION:
        raise ValueError('只支持 detection definition schema_version 1.0')
    for key in ('id', 'version'):
        identifier(config[key], key)
    text(config['name'], 'name')
    text(config.setdefault('description', '未提供业务说明'), 'description', 4000)
    scope = config.setdefault('scope', {})
    fields(scope, (), ('start_s', 'end_s', 'skip_first_s', 'skip_last_s', 'exclude'), 'scope')
    for key in ('start_s', 'skip_first_s', 'skip_last_s'):
        scope[key] = finite(scope.get(key, 0), key, 0, 3600)
    scope.setdefault('end_s', None)
    if scope['end_s'] is not None:
        finite(scope['end_s'], 'end_s', 0, 3600)
        if scope['end_s'] <= scope['start_s']:
            raise ValueError('scope.end_s 必须大于 start_s')
    exclusions = scope.setdefault('exclude', [])
    if not isinstance(exclusions, list) or len(exclusions) > 100:
        raise ValueError('scope.exclude 必须为最多 100 个区间的列表')
    for excluded in exclusions:
        interval(excluded, 'scope.exclude')
    window = config.setdefault('window', {'kind': 'whole'})
    if not isinstance(window, dict) or window.get('kind') not in ('whole', 'sliding', 'after_activity'):
        raise ValueError('window.kind 必须为 whole / sliding / after_activity')
    optional = {'whole': (), 'sliding': ('length_s', 'step_s', 'include_partial'),
                'after_activity': ('length_s', 'channel', 'params', 'delay_s', 'include_partial')}[window['kind']]
    fields(window, ('kind',), optional, 'window')
    if window['kind'] != 'whole':
        window['length_s'] = finite(window.get('length_s', 5), 'window.length_s', 0.02, 3600)
        window.setdefault('include_partial', False)
        if type(window['include_partial']) is not bool:
            raise ValueError('include_partial 必须为布尔值')
    if window['kind'] == 'sliding':
        window['step_s'] = finite(window.get('step_s', window['length_s']), 'step_s', 0.02, 3600)
    if window['kind'] == 'after_activity':
        window['channel'] = channel(window.get('channel', 0))
        window['params'] = parameters('activity_total_s', window.get('params', {}))
        window['delay_s'] = finite(window.get('delay_s', 0), 'delay_s', 0, 120)
    metrics = config['metrics']
    if not isinstance(metrics, dict) or not 1 <= len(metrics) <= 32:
        raise ValueError('metrics 必须包含 1–32 项指标')
    for name, metric in metrics.items():
        identifier(name, '指标标识')
        fields(metric, ('kind', 'channel'), ('params',), '指标 ' + name)
        if not isinstance(metric['kind'], str):
            raise ValueError('指标 kind 必须为文本')
        channel(metric['channel'])
        metric['params'] = parameters(metric['kind'], metric.get('params', {}))
    rules = config['rules']
    if not isinstance(rules, list) or not 1 <= len(rules) <= 32:
        raise ValueError('rules 必须为 1–32 项规则列表')
    ids = set()
    for rule in rules:
        fields(rule, ('id', 'label', 'when'), ('unless', 'description'), 'rule')
        identifier(rule['id'], 'rule.id')
        if rule['id'] in ids:
            raise ValueError('rule.id 必须唯一')
        ids.add(rule['id'])
        text(rule['label'], 'label', 100)
        text(rule.setdefault('description', rule['label']), 'rule.description', 1000)
        condition(rule['when'], metrics)
        if 'unless' in rule:
            condition(rule['unless'], metrics)
    return config


def load(path):
    if path.stat().st_size > 1024 * 1024:
        raise ValueError('配置文件不能超过 1 MiB')
    return validate(read_json(path))


def canonical(config):
    return json.dumps(config, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def fingerprint(config):
    return hashlib.sha256(canonical(config).encode('utf-8')).hexdigest()
