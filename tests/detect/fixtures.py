import copy
from pathlib import Path

import numpy as np

from voice_tools.audio.io import Audio, write_wav
from voice_tools.tools.detect.definition import load, validate


def config(label='低电平', threshold=-30, version='1'):
    return validate({'schema_version': '1.0', 'id': 'level', 'version': version, 'name': '测试电平',
        'metrics': {'level': {'kind': 'rms_dbfs', 'channel': 1}},
        'rules': [{'id': 'quiet', 'label': label, 'when': {'metric': 'level', 'op': 'lt', 'value': threshold}}]})


def audio(level=0.005, seconds=8, rate=8000, channels=2):
    samples = np.zeros((round(seconds * rate), channels), dtype=np.float32)
    t = np.arange(len(samples)) / rate
    samples[:, 0] = .1 * np.sin(2 * np.pi * 440 * t)
    if channels == 2:
        samples[:, 1] = level * np.sin(2 * np.pi * 330 * t)
    return Audio(samples, rate)


def wav(path, level=.005, seconds=8, channels=2):
    result = audio(level, seconds, channels=channels)
    write_wav(path, result.samples, result.sample_rate)
    return Path(path)


def example(kind='ai-silence'):
    from voice_tools.tools.detect import definition
    return load(Path(definition.__file__).parent / 'resources' / (kind + '.json'))
