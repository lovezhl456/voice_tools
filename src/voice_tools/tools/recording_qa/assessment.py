"""Whole-call acoustic and response-timing decisions; never human gold labels."""
from dataclasses import asdict, dataclass
import hashlib
import math

from voice_tools.audio.activity import merge_spans
from voice_tools.tools.gaps.detector import Config as GapConfig, analyze as analyze_gaps
from .detector import Config, analyze, number

ALGORITHM = 'qa-assessment-1'
SCOPE = 'acoustic_and_response_timing'
DECISIONS = {'AUTO_PASS': '自动通过', 'AUTO_ANOMALY': '自动异常', 'NEEDS_REVIEW': '待人工复核'}
REASONS = {
    'no_response': '完整等待窗口内未检出 AI 语音或声学输出',
    'late_response': 'AI 语音开始时间超过应答时限',
    'non_speech_output': '有声学活动但语音模型未确认是语音',
    'short_response': 'AI 语音过短，无法确认完整输出',
    'short_window': '观察窗口不足，不能确定是否漏答',
    'no_turns': '未找到可评估的用户轮次或显式应答机会',
    'long_output_gap': 'AI 输出中的长停顿超过当前策略阈值',
    'micro_dropout': 'AI 语音内部出现短断音',
    'terminal_interruption': '已对齐的输出预期内发生中断',
    'long_silence': '录音中存在需要核对的长时间无语音区间',
    'gap_unconfirmed': '工程检测到输出间隙，语音或业务证据不足',
}


@dataclass(frozen=True)
class Policy:
    timeout_s: float = 5.0
    turn_gap_s: float = 0.8
    minimum_response_s: float = 0.25
    output_gap_s: float = 2.0
    long_silence_s: float = 10.0
    threshold_db: float = -45.0
    system_channel: int = 1
    channels_verified: bool = False
    ai_start_s: object = None
    audit_percent: float = 5.0

    def validate(self):
        number(self.timeout_s, 'timeout', .5, 120)
        number(self.turn_gap_s, 'turn-gap', 0, 3)
        number(self.minimum_response_s, 'minimum-response', .1, 2)
        number(self.output_gap_s, 'output-gap', .4, 120)
        number(self.long_silence_s, 'long-silence', 2, 300)
        number(self.audit_percent, 'audit-percent', 0, 100)
        if self.ai_start_s is not None:
            number(self.ai_start_s, 'ai-start', 0, 3600)
        Config(system_channel=self.system_channel, channel_verified=self.channels_verified,
               ai_start_s=self.ai_start_s, threshold_db=self.threshold_db).validate()


def overlap(spans, start, end):
    return sum(max(0, min(b, end) - max(a, start)) for a, b in spans)


def model_spans(model, audio):
    if not isinstance(model, dict):
        raise ValueError('语音模型返回了无效结果')
    if model.get('status') != 'completed':
        raise ValueError(model.get('error') or '语音模型未完成')
    coverage = model.get('coverage_s')
    if type(coverage) not in (int, float) or not math.isfinite(coverage) or abs(coverage - audio.duration_s) > 1e-5:
        raise ValueError('语音模型没有覆盖完整录音')
    channels = model.get('speech')
    if not isinstance(channels, list) or len(channels) != audio.samples.shape[1]:
        raise ValueError('模型返回的声道数不匹配')
    for spans in channels:
        previous = 0.0
        if not isinstance(spans, list):
            raise ValueError('模型语音区间格式无效')
        for span in spans:
            if not isinstance(span, (list, tuple)) or len(span) != 2:
                raise ValueError('模型语音区间格式无效')
            a, b = span
            if any(type(v) not in (int, float) or not math.isfinite(v) for v in span):
                raise ValueError('模型语音区间包含无效时间')
            if a < previous or b <= a or b > audio.duration_s + 1e-5:
                raise ValueError('模型语音区间越界、重叠或倒置')
            previous = b
    return channels


def response_turns(user, agent, start, end, metadata, policy):
    if 'opportunities' in metadata:
        return [dict(op, start_s=op['at_s'], source='business_event')
                for op in sorted(metadata['opportunities'], key=lambda op: op['at_s'])]
    turns = []
    for a, b in user:
        a, b = max(a, start), min(b, end)
        if b <= a:
            continue
        if (turns and a - turns[-1]['at_s'] <= policy.turn_gap_s and
                overlap(agent, turns[-1]['at_s'], a) == 0):
            turns[-1]['at_s'] = b
        else:
            turns.append({'id': f'turn-{len(turns)+1}', 'start_s': a,
                          'at_s': b, 'source': 'speech_model'})
    return turns


def response_check(turn, stop, agent, energy_agent, policy):
    at = turn['at_s']
    responses = [(max(a, at), min(b, stop)) for a, b in agent if b > at and a < stop]
    sustained = [(a, b) for a, b in responses if b-a >= policy.minimum_response_s]
    if sustained:
        latency = min(a for a, b in sustained) - at
        return ('ANOMALY', 'late_response', latency) if latency >= policy.timeout_s else ('PASS', 'speech_in_time', latency)
    if responses:
        return 'REVIEW', 'short_response', None
    if stop-at < policy.timeout_s:
        return 'REVIEW', 'short_window', None
    if overlap(energy_agent, at, at+policy.timeout_s) > 0:
        return 'REVIEW', 'non_speech_output', None
    return 'ANOMALY', 'no_response', None


def conversational_pause(start, end, user, agent):
    # A pause between two AI turns is not a dropout inside one AI utterance.
    if any(a <= start and b >= end for a, b in agent):
        return False
    before = [b for a, b in agent if b <= start + .25]
    after = [a for a, b in agent if a >= end - .25]
    return bool(before and after and overlap(user, max(before), min(after)) > 0)


def assess(audio, audio_hash, metadata, policy, model):
    policy.validate()
    config = Config(timeout_s=policy.timeout_s, threshold_db=policy.threshold_db,
                    system_channel=policy.system_channel, channel_verified=policy.channels_verified,
                    ai_start_s=policy.ai_start_s)
    engineering = analyze(audio, metadata, config)
    gaps = analyze_gaps(audio, audio_hash, metadata, GapConfig(
        system_channel=policy.system_channel, channels_verified=policy.channels_verified,
        threshold_db=policy.threshold_db, dead_air_ms=policy.output_gap_s*1000))
    start, end = engineering['ai_start_s'] or 0, engineering['ai_end_s']
    blocks, findings, turns, excluded_gaps = [], [], [], []
    health = engineering['health']
    if audio.samples.shape[1] != 2:
        blocks.append('单声道无法独立核对用户和 AI')
    if not engineering['channel_verified']:
        blocks.append('用户与 AI 声道尚未核实')
    if engineering['ai_start_s'] is None:
        blocks.append('缺少 AI 接管时间，请核实范围后设置 --ai-start 或事件文件')
    if start >= end:
        blocks.append('没有有效的 AI 质检时间范围')
    if health['duplicate_channels']:
        blocks.append('声道重复或均静音')
    if abs(gaps['health'].get('correlation') or 0) > .98:
        blocks.append('两轨高度相关，需要核对串音或声道映射')
    if health['clipping_fraction'] > .01:
        blocks.append('削波比例超过 1%，需要核对录音质量')
    if metadata.get('output_events') and not metadata['output_events'].get('alignment_verified'):
        blocks.append('输出事件尚未验证时间对齐')

    def finding(kind, a, b, automatic=False, source=None):
        if b <= a:
            return
        findings.append({'id': f'finding-{len(findings)+1}', 'kind': kind,
                         'start_s': round(a, 6), 'end_s': round(b, 6),
                         'decision': 'ANOMALY' if automatic else 'REVIEW',
                         'reason': REASONS[kind], 'source': source or []})

    try:
        channels = model_spans(model, audio)
    except ValueError as error:
        channels = None
        blocks.append(str(error))
        model = {'status': 'error' if not isinstance(model, dict) or model.get('status') != 'not_run' else 'not_run',
                 'error': str(error)}
    exclusions = [(item['start_s'], item['end_s']) for item in metadata.get('exclusions', [])]
    if channels is not None and len(channels) == 2:
        agent, user = channels[policy.system_channel], channels[1-policy.system_channel]
        if 'user_speech' in metadata:
            declared = [(item['start_s'], item['end_s']) for item in metadata['user_speech']]
            if (any(overlap(user, a, b) < min(.1, (b-a)/2) for a, b in declared) or
                    any(b-a >= .3 and overlap(declared, a, b) < (b-a)/2 for a, b in user)):
                blocks.append('已提供的用户讲话区间与语音模型不一致')
            user = declared
        turns = response_turns(user, agent, start, end, metadata, policy)
        if len(turns) > 2000:
            blocks.append('轮次数超过预览上限，未完整判定')
            turns = turns[:2000]
        for index, turn in enumerate(turns):
            at = turn['at_s']
            stop = min(end, turn.get('window_end_s', end))
            if index+1 < len(turns):
                stop = min(stop, turns[index+1]['at_s'])
            for a, b in user + exclusions:
                if a > at:
                    stop = min(stop, a)
            stop = max(at, stop)
            excluded = (not turn.get('expects_response', True) or at < start or at > end or
                        any(a <= at < b for a, b in exclusions))
            if excluded:
                status, reason, latency = 'EXCLUDED', 'outside_response_window', None
            else:
                status, reason, latency = response_check(turn, stop, agent, engineering.get('system_activity', []), policy)
                if status != 'PASS':
                    finding(reason, at, stop, status == 'ANOMALY', ['engineering', 'speech_model', turn['source']])
            turn.update(decision=status, reason=reason, observed_until_s=stop,
                        latency_s=None if latency is None else round(latency, 6))
        if not turns and 'opportunities' not in metadata:
            finding('no_turns', start, end)
        for gap in gaps['gaps']:
            if gap['status'] != 'CANDIDATE' or 'members' in gap:
                continue
            a, b = gap['start_s'], gap['end_s']
            if a < start or b > end:
                continue
            if gap['evidence_level'] != 'event_aligned' and conversational_pause(a, b, user, agent):
                excluded_gaps.append({'start_s': a, 'end_s': b, 'kind': gap['type'],
                                      'reason': '用户轮次之间的停顿；应答时序另行检查'})
                continue
            before = overlap(agent, max(start, a-.5), a) >= .1
            after = overlap(agent, b, min(end, b+.5)) >= .1
            supported = (before and after) or gap['evidence_level'] == 'event_aligned'
            kind = {'dead_air': 'long_output_gap', 'micro_dropout': 'micro_dropout',
                    'terminal_interruption': 'terminal_interruption'}.get(gap['type'], 'gap_unconfirmed')
            finding(kind if supported else 'gap_unconfirmed', a, b, supported,
                    ['engineering', 'speech_model', gap['evidence_level']])
        activity = merge_spans([(max(a, start), min(b, end)) for a, b in user+agent+exclusions
                                if b > start and a < end], 0)
        for (_, a), (b, _) in zip(activity, activity[1:]):
            if b-a >= policy.long_silence_s and not any(f['start_s'] <= a and f['end_s'] >= b for f in findings):
                finding('long_silence', a, b, False, ['speech_model'])
    if gaps.get('processing_error'):
        blocks.append('工程间隙检测未完整完成：' + gaps['processing_error'])
    if blocks or any(f['decision'] == 'REVIEW' for f in findings) or any(t['decision'] == 'REVIEW' for t in turns):
        decision = 'NEEDS_REVIEW'
    elif any(f['decision'] == 'ANOMALY' for f in findings) or any(t['decision'] == 'ANOMALY' for t in turns):
        decision = 'AUTO_ANOMALY'
    else:
        decision = 'AUTO_PASS'
    sample = int(hashlib.sha256((audio_hash+ALGORITHM).encode()).hexdigest()[:8], 16) / 2**32 * 100
    audit = decision == 'AUTO_PASS' and sample < policy.audit_percent
    review_required = decision != 'AUTO_PASS' or audit
    windows = merge_spans([(max(start, f['start_s']-1), min(end, f['end_s']+1)) for f in findings], .5)
    if review_required and (blocks or not windows):
        windows = [(start, end)]
    return {'schema_version': '1.0', 'algorithm': ALGORITHM, 'scope': SCOPE,
            'decision': decision, 'review_required': review_required, 'audit_selected': audit,
            'duration_s': audio.duration_s, 'sample_rate': audio.sample_rate, 'system_channel': policy.system_channel,
            'checked_range': [start, end], 'channel_verified': engineering['channel_verified'],
            'policy': {**asdict(policy), 'channels_verified': engineering['channel_verified'],
                       'ai_start_s': engineering['ai_start_s']}, 'blockers': list(dict.fromkeys(blocks)),
            'findings': findings, 'turns': turns, 'review_windows': windows if review_required else [],
            'model': model, 'engineering': {'status': 'partial_error' if gaps.get('processing_error') else 'completed', 'health': health,
                'opportunities_before_grouping': len(engineering['opportunities']),
                'gap_candidates': gaps['candidate_count'], 'excluded_conversation_gaps': excluded_gaps},
            'notice': '自动判断只覆盖声学与应答时序；语音活动不证明回答内容正确，自动结果不是人工黄金标签。'}
