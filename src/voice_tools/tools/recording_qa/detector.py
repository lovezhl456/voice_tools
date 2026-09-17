"""纯检测逻辑：不读文件、不写报告、不调用网络。"""
from dataclasses import asdict, dataclass
import math

from voice_tools.audio.activity import detect_activity

CANDIDATES = {"NO_OUTPUT_CANDIDATE", "LATE_OUTPUT_CANDIDATE"}
SCHEMA_VERSION = "1.0"


def number(value, name, low=0, high=float("inf")):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} 必须是 {low}–{high} 范围内的有限数字")
    return float(value)


@dataclass(frozen=True)
class Config:
    timeout_s: float = 5.0
    threshold_db: float = -45.0
    minimum_s: float = 0.16
    join_gap_s: float = 0.3
    system_channel: int = 1
    channel_verified: bool = False
    ai_start_s: object = None
    backend: str = "energy"

    def validate(self):
        number(self.timeout_s, "timeout_s", 0.1, 120)
        number(self.threshold_db, "threshold_db", -100, -1)
        number(self.minimum_s, "minimum_s", 0.02, 2)
        number(self.join_gap_s, "join_gap_s", 0, 2)
        if type(self.system_channel) is not int or self.system_channel not in (0, 1):
            raise ValueError("system_channel 必须为 0（左）或 1（右）")
        if type(self.channel_verified) is not bool:
            raise ValueError("channel_verified 必须为布尔值")
        if self.backend not in ("energy", "webrtcvad"):
            raise ValueError("未知活动检测器")


def metadata_for(metadata, duration, config):
    if not isinstance(metadata, dict):
        raise ValueError("事件文件必须是 JSON 对象")
    if metadata.get("schema_version", "1.0") != "1.0":
        raise ValueError("不支持的事件 schema_version")
    if "system_channel" in metadata:
        if type(metadata["system_channel"]) is not int or metadata["system_channel"] != config.system_channel:
            raise ValueError("事件文件与 --system-channel 不一致")
    verified = metadata.get("channel_verified", config.channel_verified)
    if type(verified) is not bool:
        raise ValueError("channel_verified 必须为布尔值")
    start = metadata.get("ai_start_s", config.ai_start_s)
    if start is not None:
        start = number(start, "ai_start_s", 0, duration)
    end = number(metadata.get("ai_end_s", duration), "ai_end_s", start or 0, duration)
    for key in ("user_speech", "exclusions"):
        spans = metadata.get(key, [])
        if not isinstance(spans, list):
            raise ValueError(f"{key} 必须为列表")
        for span in spans:
            if not isinstance(span, dict) or not {"start_s", "end_s"} <= span.keys():
                raise ValueError(f"{key} 缺少 start_s / end_s")
            a = number(span["start_s"], key + ".start_s", 0, duration)
            b = number(span["end_s"], key + ".end_s", a, duration)
            if b <= a:
                raise ValueError(f"{key} 区间长度必须大于 0")
    if "opportunities" in metadata:
        if not isinstance(metadata["opportunities"], list):
            raise ValueError("opportunities 必须为列表")
        ids = set()
        for op in metadata["opportunities"]:
            if not isinstance(op, dict) or not {"id", "at_s"} <= op.keys():
                raise ValueError("应答机会缺少 id / at_s")
            if not isinstance(op["id"], str) or not op["id"].strip() or op["id"] in ids:
                raise ValueError("应答机会 id 必须是非空且唯一的字符串")
            ids.add(op["id"])
            at = number(op["at_s"], "at_s", 0, duration)
            number(op.get("window_end_s", duration), "window_end_s", at, duration)
            if type(op.get("expects_response", True)) is not bool:
                raise ValueError("expects_response 必须为布尔值")
    return verified, start, end


def analyze(audio, metadata=None, config=None):
    config = config or Config()
    config.validate()
    data = audio.samples
    if data.ndim != 2 or data.shape[1] not in (1, 2) or len(data) == 0:
        raise ValueError("没有有效的单/双声道样本")
    import numpy as np
    if not np.isfinite(data).all() or not 8000 <= audio.sample_rate <= 48000:
        raise ValueError("样本或采样率无效")
    metadata = {} if metadata is None else metadata
    verified, start, end = metadata_for(metadata, audio.duration_s, config)
    activity, health = detect_activity(audio, threshold_db=config.threshold_db, minimum_s=config.minimum_s,
                                       gap_s=config.join_gap_s, backend=config.backend)
    warnings = []
    if config.backend == "energy":
        warnings.append("能量活动包含噪声、音乐和提示音；不等于人声或有效回答")
    else:
        warnings.append("VAD 输出仅为语音候选，不能判断说话人身份或回答语义")
    if not verified:
        warnings.append("声道角色尚未验证；按配置映射筛查，候选需核对声道")
    if start is None:
        warnings.append("缺少 AI 接管时间；可能包含 IVR / 转接阶段")
    if health["duplicate_channels"]:
        warnings.append("两轨相同或均静音，不能确认独立用户和 AI 轨")
    if health["max_dc_offset"] > 0.05:
        warnings.append("存在直流偏移；能量检测已去除帧均值")
    if health["clipping_fraction"] > 0.01:
        warnings.append("削波超过 1%，需复核音质")
    result = {"schema_version": SCHEMA_VERSION, "duration_s": audio.duration_s,
              "sample_rate": audio.sample_rate, "channels": data.shape[1],
              "config": asdict(config), "channel_verified": verified,
              "ai_start_s": start, "ai_end_s": end, "health": health,
              "warnings": warnings, "opportunities": [], "candidate_count": 0,
              "interpretation": "仅输出复核候选；录音不能单独确定 LLM/TTS/RTP 根因。"}
    if data.shape[1] != 2:
        result.update(status="INSUFFICIENT_EVIDENCE", user_activity=[], system_activity=[])
        warnings.append("单声道混音无法分离用户/AI，转人工复核；不复制成双轨")
        return result
    system_activity, user_activity = activity[config.system_channel], activity[1 - config.system_channel]
    result.update(user_activity=user_activity, system_activity=system_activity)
    user_spans = metadata.get("user_speech", [{"start_s": a, "end_s": b} for a, b in user_activity])
    explicit = "opportunities" in metadata
    if explicit:
        opportunities = metadata["opportunities"]
    else:
        opportunities = [{"id": f"auto-{index:04d}", "at_s": span["end_s"]}
                         for index, span in enumerate(user_spans, 1)
                         if span["end_s"] >= (start or 0) and span["end_s"] <= end]
        warnings.append("机会从用户轨活动结束推导，不代表用户语义说完")
    opportunities = sorted(opportunities, key=lambda op: op["at_s"])
    exclusions = metadata.get("exclusions", [])
    evidence = "event_aligned" if explicit and verified and start is not None else "acoustic_only"
    for index, op in enumerate(opportunities):
        at = float(op["at_s"])
        stop = min(end, op.get("window_end_s", end))
        if index + 1 < len(opportunities):
            stop = min(stop, opportunities[index + 1]["at_s"])
        for span in exclusions + user_spans:
            if span["start_s"] > at:
                stop = min(stop, span["start_s"])
        stop = max(at, stop)
        item = {"id": op["id"], "at_s": at, "observed_until_s": stop,
                "source": "event" if explicit else "activity_end", "evidence_level": evidence,
                "first_activity_s": None, "latency_s": None, "fault_confirmed": False}
        excluded = (not op.get("expects_response", True) or at < (start or 0) or at >= end
                    or any(s["start_s"] <= at < s["end_s"] for s in exclusions + user_spans))
        if excluded:
            status, reason = "EXCLUDED", "outside_response_window"
        elif health["duplicate_channels"]:
            status, reason = "INSUFFICIENT_EVIDENCE", "duplicate_channels"
        else:
            onsets = [max(a, at) for a, b in system_activity if b > at and a < stop]
            onset = min(onsets) if onsets else None
            if onset is not None:
                latency = onset - at
                item.update(first_activity_s=round(onset, 6), latency_s=round(latency, 6))
                if latency + 1e-8 >= config.timeout_s:
                    status, reason = "LATE_OUTPUT_CANDIDATE", "activity_after_deadline"
                else:
                    status, reason = "OUTPUT_NEEDS_REVIEW", "activity_is_not_semantic_response"
            elif stop - at + 1e-8 >= config.timeout_s:
                status, reason = "NO_OUTPUT_CANDIDATE", "no_detected_output_before_deadline"
            else:
                status, reason = "CENSORED", "observation_shorter_than_timeout"
        item.update(status=status, reason_code=reason)
        result["opportunities"].append(item)
    result["candidate_count"] = sum(op["status"] in CANDIDATES for op in result["opportunities"])
    result["status"] = "REVIEW_REQUIRED" if opportunities else "NO_OPPORTUNITIES"
    return result
