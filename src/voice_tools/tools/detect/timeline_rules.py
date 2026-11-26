"""Versioned acoustic interval rules for the recording workbench."""
from collections import Counter
from operator import gt, ge
from subprocess import SubprocessError

import numpy as np

from .definition import fields, identifier, text, validate as validate_windows
from .metrics import activity_spans, finite, parameters

KINDS = {
    'quiet': '单轨或双轨同时安静',
    'output_pause': 'AI 声音中间的安静段',
    'response_wait': '用户收声到下一段 AI 声音起点',
    'overlap': '两轨同时出现声音',
}
OPS = {'gt': gt, 'ge': ge}


def validate(config):
    if isinstance(config, dict) and config.get('schema_version') == '1.0':
        return validate_windows(config)
    fields(config, ('schema_version', 'id', 'version', 'name', 'kind', 'min_s'),
           ('channel', 'threshold_dbfs', 'op', 'description'), '时序规则')
    if config['schema_version'] != '2.0' or config['kind'] not in KINDS:
        raise ValueError('未知规则格式或时序能力')
    kind = config['kind']
    channel = config.get('channel', 'both')
    if channel not in (('left', 'right', 'both') if kind == 'quiet' else ('both',)):
        raise ValueError('此规则的适用声道无效')
    op = config.get('op', 'gt')
    if op not in OPS:
        raise ValueError('持续时间比较只支持 gt 或 ge')
    minimum_dbfs = -90 if kind == 'response_wait' else -120
    return dict(schema_version='2.0', id=identifier(config['id'], 'id'),
                version=identifier(config['version'], 'version'), name=text(config['name'], 'name', 100),
                description=text(config.get('description', KINDS[kind]), 'description', 800),
                kind=kind, channel=channel, op=op, min_s=finite(config['min_s'], 'min_s', 0.02, 3600),
                threshold_dbfs=finite(config.get('threshold_dbfs', -45), 'threshold_dbfs', minimum_dbfs, 0))


def intersections(left, right):
    i = j = 0
    while i < len(left) and j < len(right):
        a, b = left[i]
        c, d = right[j]
        if max(a, c) < min(b, d):
            yield max(a, c), min(b, d)
        if b <= d:
            i += 1
        else:
            j += 1


def response_intervals(path, ai_channel, dbfs):
    from voice_tools.tools.latency.contract import validate_parameters
    from voice_tools.tools.latency.runtime import prefix
    from voice_tools.tools.latency.service import measure_path, measurement

    params, _ = validate_parameters({'energy_threshold': 10 ** (dbfs / 10) * 1e6})
    try:
        _, _, raw = measure_path(path, prefix(), params, 'left' if ai_channel == 0 else 'right', 120)
    except (OSError, SubprocessError) as error:
        raise ValueError('逐轮延迟引擎不可用或超过时限，请先运行 latency doctor') from error
    result = measurement(raw)
    pairs = [(item['source_end_s'], item['response_start_s']) for item in result['pairs']
             if item['direction'] == 'human_to_ai']
    dispositions = result['coverage']['dispositions']
    labels = {'unpaired': '无配对的 AI 起声', 'no_response': '无后续 AI 起声', 'superseded': '用户再次起声', 'overlap': '用户收声时 AI 仍在发声'}
    unmatched = Counter({labels.get(key, key): count for key, count in dispositions.items() if key != 'paired'})
    if not pairs and not unmatched:
        unmatched['缺少可配对的用户收声和 AI 起声'] += 1
    return pairs, unmatched


def analyze(audio, rule, ai_channel, playable):
    kind = rule['kind']
    channel = rule['channel']
    count = audio.samples.shape[1]
    role_rule = kind in ('output_pause', 'response_wait')
    note = ''
    if (kind != 'quiet' or channel != 'left') and count != 2:
        note = '缺少右声道'
    elif role_rule and ai_channel not in (0, 1):
        note = '请选择 AI 所在声道'
    elif count == 2 and kind != 'quiet' and np.array_equal(audio.samples[:, 0], audio.samples[:, 1]):
        note = '两轨采样相同，缺少独立声道证据'
    if note:
        return dict(status='no_windows', windows=0, findings=[], note=note)
    params = parameters('activity_count', {'threshold_dbfs': rule['threshold_dbfs'],
                                           'min_duration_s': 0, 'join_gap_s': 0})

    def spans(index, quiet=False):
        return activity_spans(audio.samples[:, index], audio.sample_rate, params, silence=quiet)

    unmatched = Counter()
    if kind == 'quiet':
        if channel == 'both':
            candidates = intersections(spans(0, True), spans(1, True))
        else:
            candidates = spans(0 if channel == 'left' else 1, True)
    elif kind == 'overlap':
        candidates = intersections(spans(0), spans(1))
    elif kind == 'output_pause':
        segments = spans(ai_channel)
        candidates = [(left[1], right[0]) for left, right in zip(segments, segments[1:])]
        if len(segments) < 2:
            unmatched['缺少停顿两侧的 AI 声音'] += 1
    else:
        candidates, unmatched = response_intervals(playable, ai_channel, rule['threshold_dbfs'])

    findings = []
    checked = 0
    for start, end in candidates:
        checked += 1
        seconds = round(end - start, 8)
        if OPS[rule['op']](seconds, rule['min_s']):
            findings.append({'rule_id': rule['kind'], 'label': rule['name'], 'start_s': start, 'end_s': end,
                             'reason': KINDS[kind], 'values': {'duration': seconds}, 'units': {'duration': 's'},
                             'when': None, 'unless': None, 'evidence': 'latency_checker' if kind == 'response_wait' else 'acoustic', 'span_kind': 'interval'})
    note = '；'.join(f'{reason}（{number}）' for reason, number in unmatched.items())
    status = 'partial_evidence' if unmatched and checked else 'no_windows' if unmatched else 'ok'
    return dict(status=status, windows=checked, findings=findings, note=note)
