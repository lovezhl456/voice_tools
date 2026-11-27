"""Read a pixel-sized min/max envelope for a local window of prepared PCM16."""
import math
import wave

import numpy as np


def read_window(path, start, end, bins):
    if not all(type(v) in (int, float) and math.isfinite(v) for v in (start, end)) or not 0 <= start < end:
        raise ValueError('波形时间区间无效')
    if type(bins) is not int or not 64 <= bins <= 4096:
        raise ValueError('波形分辨率无效')
    with wave.open(str(path), 'rb') as stream:
        rate, channels, frames = stream.getframerate(), stream.getnchannels(), stream.getnframes()
        if start >= frames / rate or end > frames / rate + 1e-6:
            raise ValueError('波形区间超出当前录音')
        first = math.floor(start * rate)
        count = min(frames, math.ceil(end * rate)) - first
        stride = math.ceil(count / bins)
        if channels not in (1, 2) or stream.getsampwidth() != 2 or stream.getcomptype() != 'NONE':
            raise ValueError('波形源需为 PCM16 单声道或双声道')
        stream.setpos(first)
        peaks = [[] for _ in range(channels)]
        # Align reads with bin boundaries, including the last partial bin.
        block = stride * max(1, 65536 // stride)
        for offset in range(0, count, block):
            size = min(block, count - offset)
            raw = stream.readframes(size)
            if len(raw) != size * channels * 2:
                raise ValueError('录音数据不完整')
            samples = np.frombuffer(raw, dtype='<i2').reshape(size, channels)
            starts = np.arange(0, size, stride)
            bounds = np.stack((np.minimum.reduceat(samples, starts, axis=0),
                               np.maximum.reduceat(samples, starts, axis=0)), axis=-1) / 32768
            for track, values in zip(peaks, bounds.transpose(1, 0, 2)):
                track.extend(values.tolist())
    return dict(start_s=first / rate, bin_s=stride / rate, samples=stride == 1, channels=peaks)
