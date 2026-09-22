"""Pure rule evaluation. Excluded regions split analysis; missing audio is an error."""
import operator

from .metrics import METRICS, activity_spans, measure

COMPARE = {'lt': operator.lt, 'le': operator.le, 'gt': operator.gt,
           'ge': operator.ge, 'eq': operator.eq, 'ne': operator.ne}
MAX_WINDOWS = 10000


def evaluate(condition, values):
    if 'all' in condition:
        return all(evaluate(child, values) for child in condition['all'])
    if 'any' in condition:
        return any(evaluate(child, values) for child in condition['any'])
    if 'not' in condition:
        return not evaluate(condition['not'], values)
    return COMPARE[condition['op']](values[condition['metric']], condition['value'])


def analysis_spans(scope, duration):
    start = max(scope['start_s'], scope['skip_first_s'])
    end = min(duration - scope['skip_last_s'], scope['end_s'] if scope['end_s'] is not None else duration)
    spans = [(start, end)] if end > start else []
    for exclusion in scope['exclude']:
        a, b = exclusion['start_s'], exclusion['end_s']
        remaining = []
        for left, right in spans:
            if b <= left or a >= right:
                remaining.append((left, right))
            else:
                if a > left:
                    remaining.append((left, a))
                if b < right:
                    remaining.append((b, right))
        spans = remaining
    return spans


def windows(audio, config):
    spec = config['window']
    count = 0
    for start, end in analysis_spans(config['scope'], audio.duration_s):
        if spec['kind'] == 'whole':
            starts = [start]
        elif spec['kind'] == 'sliding':
            # Integer step indexing avoids cumulative floating point drift.
            n = int((end - start) / spec['step_s']) + 1
            if n > MAX_WINDOWS:
                raise ValueError('分析窗口超过 10000，请增大 step_s 或缩小分析范围')
            starts = [start + index * spec['step_s'] for index in range(n)]
        else:
            rate = audio.sample_rate
            left, right = round(start * rate), round(end * rate)
            activity = activity_spans(audio.samples[left:right, spec['channel']], rate, spec['params'])
            starts = [left / rate + b + spec['delay_s'] for _, b in activity]
        for left in starts:
            right = end if spec['kind'] == 'whole' else min(end, left + spec['length_s'])
            if right <= left:
                continue
            if spec['kind'] != 'whole' and not spec['include_partial'] and right - left + 1e-8 < spec['length_s']:
                continue
            # Boundaries identify actual samples, not a rounded display approximation.
            a, b = round(left * audio.sample_rate), round(right * audio.sample_rate)
            if b <= a:
                continue
            count += 1
            if count > MAX_WINDOWS:
                raise ValueError('分析窗口超过 10000，请缩小分析范围')
            yield a, b


def analyze(audio, config):
    channels = [metric['channel'] for metric in config['metrics'].values()]
    if config['window']['kind'] == 'after_activity':
        channels.append(config['window']['channel'])
    if max(channels) >= audio.samples.shape[1]:
        raise ValueError('配置引用了录音中不存在的声道，不能视为静音或未应答')
    findings, count = [], 0
    for start, end in windows(audio, config):
        count += 1
        values = {}
        cache = {}
        for name, metric in config['metrics'].items():
            key = (metric['kind'], metric['channel'], tuple(sorted(metric['params'].items())))
            if key not in cache:
                cache[key] = measure(metric['kind'], audio.samples[start:end, metric['channel']],
                                     audio.sample_rate, metric['params'])
            values[name] = cache[key]
        for rule in config['rules']:
            if not evaluate(rule['when'], values):
                continue
            if 'unless' in rule and evaluate(rule['unless'], values):
                continue
            if len(findings) >= 100000:
                raise ValueError('单录音单配置命中超过 100000 条，请增大步长或缩小范围')
            findings.append({'rule_id': rule['id'], 'label': rule['label'],
                             'start_s': start / audio.sample_rate, 'end_s': end / audio.sample_rate,
                             'reason': rule['description'], 'values': dict(values),
                             'units': {name: METRICS[m['kind']][0] for name, m in config['metrics'].items()},
                             'when': rule['when'], 'unless': rule.get('unless')})
    if not count:
        return {'status': 'no_windows', 'windows': 0, 'findings': [],
                'note': '范围被排除、录音不足一个完整窗口或没有触发活动；不能判为无问题'}
    return {'status': 'ok', 'windows': count, 'findings': findings, 'note': ''}
