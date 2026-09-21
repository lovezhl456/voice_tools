"""Pinned, local-only neural speech evidence. Download is a separate action."""
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from urllib.error import URLError
from urllib.request import Request, urlopen

import numpy as np

from voice_tools.audio.activity import spans_from_mask
from voice_tools.core.files import read_json, sha256, write_json
from . import DEFAULT_MODEL_DIRECTORY

RESOURCES = Path(__file__).with_name('resources')
DEFAULT_DIRECTORY = DEFAULT_MODEL_DIRECTORY


def manifest():
    return read_json(RESOURCES / 'silero.json')


def model_file(directory=None):
    return (Path(directory) if directory else DEFAULT_DIRECTORY.expanduser()) / manifest()['filename']


def verify(directory=None):
    spec = manifest()
    path = model_file(directory)
    if not path.is_file():
        raise ValueError('缺少语音模型；请先运行 qa setup --component model 或 qa model-download，或显式使用 --rules-only')
    if path.stat().st_size != spec['bytes'] or sha256(path) != spec['sha256']:
        raise ValueError('语音模型摘要不匹配；拒绝加载未知或损坏的模型')
    return path


def download(directory):
    spec = manifest()
    directory = Path(directory).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    target = model_file(directory)
    if target.exists():
        verify(directory)
        return {'model': str(target), 'sha256': spec['sha256'], 'cached': True}
    raw = (f"https://raw.githubusercontent.com/snakers4/silero-vad/{spec['commit']}/"
           'src/silero_vad/data/silero_vad.onnx')
    blob = f"https://api.github.com/repos/snakers4/silero-vad/git/blobs/{spec['git_blob']}"
    failures = []
    for url in (raw, blob):
        try:
            with urlopen(Request(url, headers={'User-Agent': 'voice-tools-autoqa'}), timeout=30) as response:
                payload = response.read(8 * 1024 * 1024 + 1)
            if len(payload) > 8 * 1024 * 1024:
                raise ValueError('模型下载超过大小限制')
            if url == blob:
                value = json.loads(payload)
                if value.get('encoding') != 'base64':
                    raise ValueError('模型仓库返回了未知编码')
                payload = base64.b64decode(value['content'])
            if len(payload) != spec['bytes'] or hashlib.sha256(payload).hexdigest() != spec['sha256']:
                raise ValueError('下载的模型摘要不匹配')
            break
        except (OSError, URLError, ValueError, KeyError) as error:
            failures.append(str(error))
    else:
        raise ValueError('语音模型下载失败：' + '; '.join(failures))
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(payload)
    try:
        # link is atomic and refuses to overwrite a file created by another process.
        os.link(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    (directory / 'LICENSE.txt').write_bytes((RESOURCES / 'silero-LICENSE.txt').read_bytes())
    write_json(directory / 'manifest.json', spec)
    return {'model': str(target), 'sha256': spec['sha256'], 'cached': False}


class SpeechModel:
    def __init__(self, directory=None, threshold=0.5):
        path = verify(directory)
        if not math.isfinite(threshold) or not 0.1 <= threshold <= 0.9:
            raise ValueError('语音阈值须为 0.1–0.9')
        try:
            import onnxruntime as ort
            from scipy.signal import resample_poly
        except ImportError as error:
            raise ValueError('请安装 CPU 模型依赖：voice-tools qa setup --component runtime') from error
        ort.disable_telemetry_events()
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        try:
            self.session = ort.InferenceSession(str(path), sess_options=options, providers=['CPUExecutionProvider'])
        except Exception as error:
            raise ValueError('ONNX 语音模型加载失败：' + str(error)) from error
        if {item.name for item in self.session.get_inputs()} != {'input', 'state', 'sr'}:
            raise ValueError('ONNX 模型输入合同不匹配')
        self.resample = resample_poly
        self.threshold = threshold
        self.identity = {key: manifest()[key] for key in ('name', 'version', 'sha256')}
        self.identity.update(runtime=ort.__version__, provider='CPUExecutionProvider', threshold=threshold)

    def predict(self, audio):
        if not np.isfinite(audio.samples).all():
            raise ValueError('语音模型输入包含非有限样本')
        rate = audio.sample_rate if audio.sample_rate in (8000, 16000) else 16000
        samples = audio.samples
        if rate != audio.sample_rate:
            divisor = math.gcd(rate, audio.sample_rate)
            samples = self.resample(samples, rate // divisor, audio.sample_rate // divisor, axis=0)
        frame, context_size = (256, 32) if rate == 8000 else (512, 64)
        channels = []
        for channel in range(samples.shape[1]):
            # Recurrent state belongs to a single channel of a single recording.
            state = np.zeros((2, 1, 128), dtype=np.float32)
            context = np.zeros((1, context_size), dtype=np.float32)
            probabilities = []
            for start in range(0, len(samples), frame):
                chunk = np.zeros((1, frame), dtype=np.float32)
                part = samples[start:start + frame, channel]
                chunk[0, :len(part)] = part
                inputs = np.concatenate((context, chunk), axis=1)
                try:
                    probability, state = self.session.run(None, {
                        'input': inputs, 'state': state, 'sr': np.array(rate, dtype=np.int64)})
                except Exception as error:
                    raise ValueError('语音模型推理失败：' + str(error)) from error
                probability = float(probability.item())
                if not math.isfinite(probability) or not 0 <= probability <= 1 or not np.isfinite(state).all():
                    raise ValueError('语音模型返回无效概率或状态')
                probabilities.append(probability)
                context = chunk[:, -context_size:]
            mask = np.asarray(probabilities) >= self.threshold
            spans = spans_from_mask(mask, frame / rate, audio.duration_s, 0.128, 0.192)
            channels.append([[round(a, 6), round(b, 6)] for a, b in spans])
        return {**self.identity, 'status': 'completed', 'coverage_s': audio.duration_s,
                'sample_rate': rate, 'speech': channels}


def doctor(directory=None):
    try:
        model = SpeechModel(directory)
        from voice_tools.audio.io import Audio
        model.predict(Audio(np.zeros((8000, 1), dtype=np.float32), 8000))
        return {'ready': True, 'model': model.identity, 'network_accessed': False, 'issues': []}
    except (ValueError, OSError) as error:
        return {'ready': False, 'issues': [str(error)], 'network_accessed': False}
