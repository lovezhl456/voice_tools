"""活动检测不是语义理解；WebRTC VAD 也是有误差的语音候选。"""
import numpy as np


def merge_spans(spans, gap):
    merged = []
    for start, end in sorted(spans):
        if merged and start - merged[-1][1] <= gap + 1e-8:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def spans_from_mask(mask, frame_s, duration_s, minimum_s, gap_s):
    edges = np.diff(np.r_[False, mask, False].astype(np.int8))
    spans = [(int(a) * frame_s, min(int(b) * frame_s, duration_s))
             for a, b in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1))]
    # 先去掉瞬时尖峰，再合并短停顿，避免多个点击声拼成一段讲话。
    return merge_spans([(a, b) for a, b in spans if b - a + 1e-8 >= minimum_s], gap_s)


def detect_activity(audio, *, threshold_db=-45, minimum_s=0.16, gap_s=0.3, backend="energy"):
    data, rate = audio.samples, audio.sample_rate
    frame = round(rate * 0.02)
    levels, dc = [], 0.0
    clipped, channel_difference = 0, 0.0
    # 分块计算，避免长录音额外产生整文件大小的居中副本。
    for offset in range(0, len(data), frame * 512):
        block = data[offset:offset + frame * 512]
        clipped += int(np.count_nonzero(np.abs(block) >= .999))
        if data.shape[1] == 2:
            channel_difference = max(channel_difference, float(np.max(np.abs(block[:, 0] - block[:, 1]))))
        complete = len(block) // frame * frame
        if complete:
            parts = block[:complete].reshape(-1, frame, data.shape[1])
            mean = parts.mean(axis=1, keepdims=True)
            dc = max(dc, float(np.max(np.abs(mean))))
            rms = np.sqrt(np.mean((parts - mean) ** 2, axis=1))
            levels.extend(20 * np.log10(np.maximum(rms, 1e-6)))
        if complete < len(block):
            tail = block[complete:]
            mean = tail.mean(axis=0)
            dc = max(dc, float(np.max(np.abs(mean))))
            rms = np.sqrt(np.mean((tail - mean) ** 2, axis=0))
            levels.append(20 * np.log10(np.maximum(rms, 1e-6)))
    levels = np.asarray(levels)
    masks = levels >= threshold_db
    if backend == "webrtcvad":
        if rate not in (8000, 16000, 32000, 48000):
            raise ValueError("WebRTC VAD 仅支持 8/16/32/48 kHz，请先重采样")
        try:
            import webrtcvad
        except ImportError as error:
            raise ValueError("请先安装可选依赖：pip install -e '.[vad]'") from error
        for channel in range(data.shape[1]):
            vad = webrtcvad.Vad(2)
            pcm = (np.clip(data[:, channel], -1, 32767 / 32768) * 32768).astype("<i2")
            for index, offset in enumerate(range(0, len(data), frame)):
                part = pcm[offset:offset + frame]
                # 不补零伪造录音尾部的语音证据。
                masks[index, channel] = (len(part) == frame and masks[index, channel]
                                         and vad.is_speech(part.tobytes(), rate))
    elif backend != "energy":
        raise ValueError(f"未知检测器：{backend}")
    activity = [spans_from_mask(masks[:, channel], frame / rate, audio.duration_s, minimum_s, gap_s)
                for channel in range(data.shape[1])]
    health = {"median_dbfs": np.median(levels, axis=0).round(2).tolist(),
              "max_dc_offset": dc,
              "clipping_fraction": clipped / data.size,
              "duplicate_channels": bool(data.shape[1] == 2 and channel_difference < 1e-5)}
    return activity, health
