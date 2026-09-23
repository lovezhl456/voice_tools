"""Synthetic recordings and model evidence shared by recording QA tests."""
import numpy as np

from voice_tools.audio.io import Audio, write_wav
from voice_tools.core.files import write_json
from voice_tools.tools.recording_qa.scenarios import fixture


def write_case(directory, name):
    directory.mkdir(parents=True, exist_ok=True)
    samples, events = fixture(name)
    path = directory / (name + ".wav")
    write_wav(path, samples, 8000)
    if events is not None:
        write_json(path.with_suffix(".events.json"), events)
    return path


HASH = 'a' * 64


def recording(user=((.5, 1.5),), agent=((2, 3),), duration=8, rate=8000):
    data = np.zeros((round(duration * rate), 2), dtype=np.float32)
    for channel, spans in enumerate((user, agent)):
        for start_s, end_s in spans:
            start, stop = round(start_s * rate), round(end_s * rate)
            frequency = 337 if channel == 0 else 220
            data[start:stop, channel] = .2 * np.sin(2 * np.pi * frequency
                                                    * np.arange(stop - start) / rate)
    return Audio(data, rate)


def evidence(audio, user=((.5, 1.5),), agent=((2, 3),)):
    return {'name': 'test-model', 'version': 'fixture', 'sha256': 'b' * 64,
            'status': 'completed', 'coverage_s': audio.duration_s,
            'speech': [list(user), list(agent)]}


class FixtureModel:
    identity = {'name': 'test', 'version': '1', 'sha256': 'a' * 64}

    def predict(self, audio):
        return evidence(audio)
