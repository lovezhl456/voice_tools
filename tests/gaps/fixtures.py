"""Synthetic calls and events shared by gap tests."""
import numpy as np

from voice_tools.audio.io import Audio

HASH = 'a' * 64
STREAM = {'src': '192.0.2.1', 'src_port': 16000, 'dst': '192.0.2.2',
          'dst_port': 24000, 'ssrc': '0x1234'}


def signal(duration=7, agent=((1, 2), (4, 5)), caller=((0, .5),), rate=16000):
    data = np.zeros((round(duration * rate), 2), dtype=np.float32)
    for channel, intervals in ((0, caller), (1, agent)):
        for start, end in intervals:
            a, b = round(start * rate), round(end * rate)
            data[a:b, channel] = .3 * np.sin(2 * np.pi * (337 if channel == 0 else 220)
                                              * np.arange(b - a) / rate)
    return Audio(data, rate)


def metadata(events, verified=True):
    return {'schema_version': '1.0', 'system_channel': 1, 'channel_verified': True,
            'output_events': {'schema_version': '1.0', 'audio_sha256': HASH,
                              'alignment_verified': verified,
                              'source': 'test-expected-playback', 'events': events}}


def expected(end=6, ident='u1', start=1):
    return {'id': 'expected-' + ident, 'type': 'tts_expected', 'utterance_id': ident,
            'start_s': start, 'end_s': end}
