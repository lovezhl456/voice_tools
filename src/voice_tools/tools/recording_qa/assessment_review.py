"""Validate separate whole-recording human labels and report observed coverage."""
import csv
import json
import math
import re
from pathlib import Path

from .review import timestamp

FIELDS = ['schema_version', 'assessment_id', 'audio_sha256', 'automatic_decision',
          'decision', 'reviewer', 'reviewed_at', 'notes']


def blank_row(record):
    return {'schema_version': '1.0', 'assessment_id': record['assessment_id'],
            'audio_sha256': record['audio_sha256'], 'automatic_decision': record['result']['decision']}


def validate_waveform(waveform, duration):
    """Bound optional display data before rendering imported result packages."""
    if not isinstance(waveform, dict) or duration is None:
        raise ValueError('波形摘要缺少有效时长')
    length, bin_s = waveform.get('duration_s'), waveform.get('bin_s')
    if (any(type(value) not in (int, float) or not math.isfinite(value) for value in (length, bin_s))
            or abs(length - duration) > 1e-5 or not 0 < bin_s <= duration + 1e-5):
        raise ValueError('波形摘要时间范围无效')
    channels = waveform.get('channels')
    if not isinstance(channels, list) or not 1 <= len(channels) <= 2:
        raise ValueError('波形摘要须为单轨或双轨')
    counts = []
    for channel in channels:
        if not isinstance(channel, list) or not 1 <= len(channel) <= 1200:
            raise ValueError('波形摘要每轨须为 1–1200 格')
        counts.append(len(channel))
        for pair in channel:
            if (not isinstance(pair, (list, tuple)) or len(pair) != 2 or
                    any(type(value) not in (int, float) or not math.isfinite(value) for value in pair) or
                    not -1 <= pair[0] <= pair[1] <= 1):
                raise ValueError('波形幅度摘要无效')
    covered_s = counts[0] * bin_s
    if len(set(counts)) != 1 or not duration - 1e-5 <= covered_s < duration + bin_s + 1e-5:
        raise ValueError('波形摘要未覆盖完整录音')


def validate_record(record):
    if (not isinstance(record, dict) or record.get('kind') != 'qa_assessment' or
            record.get('schema_version') != '1.0' or not isinstance(record.get('input'), str) or
            not isinstance(record.get('assessment_id'), str) or
            not re.fullmatch('[0-9a-f]{64}', record['assessment_id'])):
        raise ValueError('不是受支持的整通质检结果')
    digest = record.get('audio_sha256')
    if not isinstance(digest, str) or (not re.fullmatch('[0-9a-f]{64}', digest) and not (digest == '' and record.get('error'))):
        raise ValueError('录音摘要无效')
    result = record.get('result')
    if not isinstance(result, dict) or result.get('decision') not in ('AUTO_PASS', 'AUTO_ANOMALY', 'NEEDS_REVIEW'):
        raise ValueError('整通质检结论无效')
    for field in ('review_required', 'audit_selected'):
        if type(result.get(field)) is not bool:
            raise ValueError('复核或抽检标志无效')
    if 'channel_verified' in result and type(result['channel_verified']) is not bool:
        raise ValueError('声道核实标志须为布尔值')
    if 'system_channel' in result and (type(result['system_channel']) is not int or
                                       result['system_channel'] not in (0, 1)):
        raise ValueError('系统声道须为 0 或 1')
    if result.get('channel_verified') is True and 'system_channel' not in result:
        raise ValueError('已核实角色缺少系统声道')
    for field in ('blockers', 'findings', 'turns', 'review_windows'):
        if not isinstance(result.get(field), list):
            raise ValueError('整通质检证据结构无效')
    if not isinstance(result.get('model'), dict) or not isinstance(result.get('engineering'), dict):
        raise ValueError('整通质检执行阶段缺失')
    duration = result.get('duration_s')
    if duration is None:
        if not record.get('error') or result['decision'] != 'NEEDS_REVIEW':
            raise ValueError('缺少有效录音时长')
    elif type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
        raise ValueError('录音时长无效')
    if 'waveform' in record:
        validate_waveform(record['waveform'], duration)
    windows = result['review_windows']
    if result.get('checked_range') is not None:
        windows = [*windows, result['checked_range']]
    for window in windows:
        if (not isinstance(window, (list, tuple)) or len(window) != 2 or duration is None or
                any(type(v) not in (int, float) or not math.isfinite(v) for v in window) or
                not 0 <= window[0] <= window[1] <= duration + 1e-5):
            raise ValueError('复核时间窗口无效')
    if any(not isinstance(value, str) for value in result['blockers']):
        raise ValueError('待复核原因格式无效')
    for item in result['findings']:
        if (not isinstance(item, dict) or item.get('decision') not in ('ANOMALY', 'REVIEW') or
                not isinstance(item.get('reason'), str) or duration is None):
            raise ValueError('异常证据格式无效')
        a, b = item.get('start_s'), item.get('end_s')
        if (any(type(v) not in (int, float) or not math.isfinite(v) for v in (a, b)) or
                not 0 <= a < b <= duration + 1e-5):
            raise ValueError('异常证据时间无效')
    for turn in result['turns']:
        if not isinstance(turn, dict) or turn.get('decision') not in ('PASS', 'ANOMALY', 'REVIEW', 'EXCLUDED'):
            raise ValueError('自动分析轮次格式无效')
    if result['decision'] != 'AUTO_PASS' and not result['review_required']:
        raise ValueError('异常或不确定录音不能移出人工例外队列')
    if result['audit_selected'] and (result['decision'] != 'AUTO_PASS' or not result['review_required']):
        raise ValueError('抽检标志与自动结论不一致')
    if result['decision'] != 'NEEDS_REVIEW' and (result['blockers'] or result['model'].get('status') != 'completed' or
                                               result['engineering'].get('status') != 'completed'):
        raise ValueError('检查未完成的录音不能自动判定')


def read_records(path):
    records = {}
    with Path(path).open(encoding='utf-8') as stream:
        for line in stream:
            if not line.strip():
                continue
            record = json.loads(line)
            validate_record(record)
            key = record['assessment_id']
            if key in records:
                raise ValueError('整通质检结果包含重复身份')
            records[key] = record
    if not records:
        raise ValueError('整通质检结果为空')
    return records


def check(path, results):
    records = read_records(results)
    labels, seen = {}, set()
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != FIELDS:
            raise ValueError('整通复核 CSV 列不匹配；不能混用逐片段标签')
        for row in reader:
            if set(row) != set(FIELDS) or any(value is None for value in row.values()):
                raise ValueError('整通复核 CSV 行列数不匹配')
            key = row.get('assessment_id')
            if key not in records or key in seen:
                raise ValueError('复核身份未知或重复')
            seen.add(key)
            record = records[key]
            if any(row.get(field) != value for field, value in blank_row(record).items()):
                raise ValueError('复核身份、摘要或自动结论与结果不匹配')
            for field in ('reviewer', 'notes'):
                if re.match(r"^'\s*['=+@-]", row[field]):
                    row[field] = row[field][1:]
            if not row.get('decision'):
                if any(row.get(field) for field in ('reviewer', 'reviewed_at', 'notes')):
                    raise ValueError('存在未完整填写的人工标签')
                continue
            if row['decision'] not in ('normal', 'abnormal', 'uncertain') or not row.get('reviewer', '').strip():
                raise ValueError('人工判断或复核人无效')
            row['reviewed_at'] = timestamp(row.get('reviewed_at'))
            labels[key] = row
    return records, labels


def evaluate(path, results, dataset_kind):
    if dataset_kind not in ('synthetic', 'real'):
        raise ValueError('须声明 synthetic 或 real 数据集')
    records, labels = check(path, results)
    counts = {'tp': 0, 'fp': 0, 'fn': 0, 'tn': 0}
    reviewed_pass, missed_pass, abstained = 0, 0, 0
    for key, label in labels.items():
        decision = records[key]['result']['decision']
        if label['decision'] == 'uncertain':
            continue
        abnormal = label['decision'] == 'abnormal'
        if decision == 'NEEDS_REVIEW':
            abstained += 1
        elif decision == 'AUTO_PASS':
            reviewed_pass += 1
            missed_pass += int(abnormal)
            counts['fn' if abnormal else 'tn'] += 1
        else:
            counts['tp' if abnormal else 'fp'] += 1
    def ratio(a, b):
        return a/b if b else None
    return {'kind': 'qa_assessment_evaluation', 'schema_version': '1.0', 'dataset_kind': dataset_kind,
            'recordings': len(records), 'human_labels': len(labels), 'human_coverage': len(labels)/len(records),
            'counts': counts, 'machine_abstained_on_reviewed': abstained,
            'precision_on_reviewed': ratio(counts['tp'], counts['tp']+counts['fp']),
            'recall_on_reviewed_decided': ratio(counts['tp'], counts['tp']+counts['fn']),
            'reviewed_automatic_pass': reviewed_pass, 'abnormal_in_automatic_pass': missed_pass,
            'automatic_pass_error_on_reviewed': ratio(missed_pass, reviewed_pass),
            'notice': '仅统计已人工复核的样本；未复核和人工不确定不计正确。只复核异常队列不能估计自动通过集合的漏检率。'}
