"""保留原始声道，不把缺失样本或格式错误当作静音。"""
from dataclasses import dataclass
from pathlib import Path
import wave

import numpy as np


@dataclass
class Audio:
    samples: np.ndarray
    sample_rate: int

    @property
    def duration_s(self):
        return len(self.samples) / self.sample_rate


def read_wav(path, max_seconds=3600, max_decoded_bytes=512 * 1024 * 1024):
    try:
        with wave.open(str(path), "rb") as stream:
            channels, width = stream.getnchannels(), stream.getsampwidth()
            rate, count = stream.getframerate(), stream.getnframes()
            if channels not in (1, 2) or width != 2 or stream.getcomptype() != "NONE":
                raise ValueError("仅支持单/双声道 PCM16 WAV；请先转换，保持原始声道")
            if not 8000 <= rate <= 48000 or count <= 0 or count / rate > max_seconds:
                raise ValueError("采样率须为 8–48 kHz，录音时长须大于 0 且不超过 3600 秒")
            if count * channels * 4 > max_decoded_bytes:
                raise ValueError("解码后样本超过 512 MiB 内存上限，请先按事件分段或重采样")
            payload = stream.readframes(count)
            if len(payload) != count * channels * width:
                raise ValueError("WAV 数据截断，不能把缺失录音解释为静音")
    except (wave.Error, EOFError) as error:
        raise ValueError(f"无效或不支持的 WAV：{Path(path).name}") from error
    data = np.frombuffer(payload, dtype="<i2").reshape(-1, channels).astype(np.float32)
    data /= 32768
    return Audio(data, rate)


def write_wav(path, samples, rate):
    data = np.asarray(samples)
    if data.ndim == 1:
        data = data[:, None]
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(data.shape[1])
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes((np.clip(data, -1, 32767 / 32768) * 32768).astype("<i2").tobytes())
