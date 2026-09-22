"""Small, explicit metric registry. Definitions contain data, never executable code."""
import math

# Each parameter is (default, minimum, maximum). Unknown parameters are rejected.
ACTIVITY_PARAMS = {'frame_ms': (20, 5, 100), 'threshold_dbfs': (-45, -120, 0),
                   'min_duration_s': (0.1, 0, 3600), 'join_gap_s': (0, 0, 120)}
METRICS = {
    'rms_dbfs': ('dBFS', '区间均方根电平，静音下限 -120 dBFS', {}),
    'peak_dbfs': ('dBFS', '区间绝对峰值电平，静音下限 -120 dBFS', {}),
    'clipping_ratio': ('ratio', '绝对幅值达到 clip_level 的样本比例', {'clip_level': (0.999, 0.01, 1)}),
    'duration_s': ('s', '当前分析窗口长度', {}),
}
for category, description in (('silence', '低于阈值'), ('activity', '达到阈值')):
    for suffix, unit, detail in (('ratio', 'ratio', '片段累计长度占窗口比例'),
                               ('total_s', 's', '片段累计时长'),
                               ('longest_s', 's', '最长连续片段'),
                               ('count', 'count', '片段次数')):
        METRICS[category + '_' + suffix] = (unit, description + detail, ACTIVITY_PARAMS)


def finite(value, name, low=-1e12, high=1e12):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{name} 必须是 {low}–{high} 之间的有限数字')
    return value


def parameters(kind, supplied):
    if kind not in METRICS:
        raise ValueError(f'未知指标 {kind}；通过 detect catalog 查看支持的指标')
    if not isinstance(supplied, dict):
        raise ValueError('指标 params 必须为对象')
    allowed = METRICS[kind][2]
    unknown = set(supplied) - set(allowed) - {'remove_dc'}
    if unknown:
        raise ValueError(f'{kind} 不支持参数 {sorted(unknown)}')
    result = {key: finite(supplied.get(key, default), key, low, high)
              for key, (default, low, high) in allowed.items()}
    remove_dc = supplied.get('remove_dc', True)
    if type(remove_dc) is not bool:
        raise ValueError('remove_dc 必须为布尔值')
    result['remove_dc'] = remove_dc
    return result


def activity_spans(samples, rate, params, silence=False):
    """Frame means are removed before energy calculation; tail is not zero-padded."""
    import numpy as np
    from voice_tools.audio.activity import spans_from_mask
    frame = max(1, round(rate * params['frame_ms'] / 1000))
    levels = []
    for offset in range(0, len(samples), frame * 512):
        block = samples[offset:offset + frame * 512]
        complete = len(block) // frame * frame
        if complete:
            parts = block[:complete].reshape(-1, frame).astype(np.float64)
            if params['remove_dc']:
                parts = parts - parts.mean(axis=1, keepdims=True)
            levels.extend(20 * np.log10(np.maximum(np.sqrt(np.mean(parts ** 2, axis=1)), 1e-6)))
        if complete < len(block):
            tail = block[complete:].astype(np.float64)
            if params['remove_dc']:
                tail = tail - tail.mean()
            levels.append(20 * np.log10(max(float(np.sqrt(np.mean(tail ** 2))), 1e-6)))
    mask = np.asarray(levels) >= params['threshold_dbfs']
    if silence:
        mask = ~mask
    return spans_from_mask(mask, frame / rate, len(samples) / rate,
                           params['min_duration_s'], params['join_gap_s'])


def measure(kind, samples, rate, params):
    import numpy as np
    duration = len(samples) / rate
    if not len(samples):
        raise ValueError('窗口没有样本，不能当作静音')
    if kind == 'duration_s':
        return duration
    if kind.startswith(('silence_', 'activity_')):
        spans = activity_spans(samples, rate, params, kind.startswith('silence_'))
        lengths = [b - a for a, b in spans]
        suffix = kind.split('_', 1)[1]
        return {'ratio': sum(lengths) / duration, 'total_s': sum(lengths),
                'longest_s': max(lengths, default=0), 'count': len(lengths)}[suffix]
    data = samples.astype(np.float64)
    if params['remove_dc']:
        data = data - data.mean()
    if kind == 'clipping_ratio':
        return float(np.mean(np.abs(data) >= params['clip_level']))
    amplitude = float(np.max(np.abs(data))) if kind == 'peak_dbfs' else float(np.sqrt(np.mean(data ** 2)))
    return 20 * math.log10(max(amplitude, 1e-6))


def catalog():
    return {name: {'unit': unit, 'description': desc,
                   'params': {**{key: {'default': default, 'minimum': low, 'maximum': high}
                                  for key, (default, low, high) in params.items()},
                              'remove_dc': {'default': True, 'type': 'boolean'}}}
            for name, (unit, desc, params) in METRICS.items()}
