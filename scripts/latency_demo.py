#!/usr/bin/env python3
"""Generate explicit-boundary synthetic stereo fixtures plus a runnable portable task."""
import argparse
import json
from pathlib import Path
import wave
import numpy as np


def recording(path, rate=16000, human=((1, 2),), ai=((2.5, 3.5),), duration=7, duplicate=False):
    frames = int(rate * duration)
    samples = np.zeros((frames, 2), dtype='<i2')
    for channel, spans in enumerate((human, ai)):
        for start, end in spans:
            first, last = round(start * rate), round(end * rate)
            samples[first:last, channel] = (np.sin(np.arange(last-first) * 2 * np.pi * (440 + channel * 120) / rate) * 18000).astype('<i2')
    if duplicate:
        samples[:, 1] = samples[:, 0]
    with wave.open(str(path), 'wb') as stream:
        stream.setparams((2, 2, rate, frames, 'NONE', 'not compressed'))
        stream.writeframes(samples.tobytes())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    args=parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    audio=args.out/'audio';audio.mkdir()
    recording(audio/'normal.wav')
    recording(audio/'long-delay.wav',ai=((14,15),),duration=18)
    recording(audio/'short-ai.wav',ai=((3,3.2),))
    recording(audio/'no-response.wav',ai=())
    recording(audio/'silence.wav',human=(),ai=())
    recording(audio/'duplicate-channels.wav',duplicate=True)
    recording(audio/'overlap.wav',human=((1,4),),ai=((2,5),),duration=8)
    recording(audio/'many-turns.wav',human=tuple((1+i*8,2+i*8) for i in range(60)),ai=tuple((2.5+i*8,3.5+i*8) for i in range(60)),duration=484)
    task={'schema_version':'1.0','id':'latency-demo','title':'双轨录音延迟迁移演示','inputs':{'recordings':'audio'},
          'steps':[{'id':'latency','tool':'latency','action':'batch','timeout_s':1800,
                    'params':{'inputs':[{'input':'recordings'}],'system_channel':'right','include_audio':False}}]}
    (args.out/'task.json').write_text(json.dumps(task,ensure_ascii=False,indent=2)+'\n')
    (args.out/'executor.example.json').write_text(json.dumps({'schema_version':'1.0','network_allowed':False,'latency_dir':'/opt/latency','environments':{}},indent=2)+'\n')
    (args.out/'expected.json').write_text(json.dumps({'normal.wav':{'human_to_ai_s':[.5]},'long-delay.wav':{'human_to_ai_s':[12]},'synthetic_tolerance_s':.02,'real_speech_accuracy_claim':False},indent=2)+'\n')
    print(args.out)


if __name__=='__main__':
    main()
