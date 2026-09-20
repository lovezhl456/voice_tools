#!/usr/bin/env python3
"""Create synthetic WAV/event/evidence examples, never production golden labels."""
import argparse
from pathlib import Path
import json
import struct
import socket

import numpy as np

from voice_tools.audio.io import write_wav
from voice_tools.core.files import new_output, sha256, write_json


def audio(path, duration, agent, caller=((0,.5),)):
    rate=16000
    samples=np.zeros((int(duration*rate),2),dtype=np.float32)
    for channel,spans in ((0,caller),(1,agent)):
        for start,end in spans:
            a,b=round(start*rate),round(end*rate)
            samples[a:b,channel]=.25*np.sin(2*np.pi*(320+channel*120)*np.arange(b-a)/rate)
    write_wav(path,samples,rate)


def packet_file(path):
    # Ethernet/IPv4/UDP/RTP; synthetic PCMA payload and an intentional capture gap.
    frames=[]
    for i in range(350):
        if 100<=i<200:continue
        rtp=struct.pack('!BBHII',0x80,8,i,i*160,0x1234)+bytes([0xd5])*160
        udp=struct.pack('!HHHH',16000,24000,len(rtp)+8,0)+rtp
        ip=struct.pack('!BBHHHBBH4s4s',0x45,0,len(udp)+20,i,0,64,17,0,socket.inet_aton('192.0.2.1'),socket.inet_aton('192.0.2.2'))+udp
        eth=bytes.fromhex('00112233445566778899aabb0800')+ip
        sec,usec=100+i//50,(i%50)*20000
        frames.append(struct.pack('<IIII',sec,usec,len(eth),len(eth))+eth)
    path.write_bytes(struct.pack('<IHHIIII',0xa1b2c3d4,2,4,0,0,65535,1)+b''.join(frames))


def create(output):
    root=new_output(output);calls=root/'calls';calls.mkdir()
    cases={'dead-air':(7,[(1,2),(4,5)]),'micro':(4,[(1,1.5),(1.6,2.1)]),
           'cluster':(5,[(1,1.4),(1.5,1.8),(1.9,2.2),(2.3,2.7)]),
           'terminal':(7,[(1,2)]),'allowed-wait':(7,[(1,2),(4,5)]),
           'high-occupancy':(17,[(0,8),(9,17)])}
    for name,(duration,spans) in cases.items():
        path=calls/(name+'.wav');audio(path,duration,spans)
        if name in ('terminal','allowed-wait'):
            event={'id':'expected-1','type':'tts_expected','utterance_id':'u1','start_s':1,'end_s':6}
            if name=='allowed-wait':event={'id':'wait-1','type':'tool_wait','start_s':2,'end_s':4,'allows_silence':True}
            write_json(path.with_suffix('.events.json'),{'schema_version':'1.0','system_channel':1,'channel_verified':True,
                'output_events':{'schema_version':'1.0','audio_sha256':sha256(path),'alignment_verified':True,'source':'synthetic-example','events':[event]}})
    packet_file(root/'synthetic.pcap')
    score=root/'nisqa-fixture';score.mkdir()
    # No fabricated model score. These rows only demonstrate temporal association.
    source='synthetic-dead-air.wav';records=[]
    for i,(a,b) in enumerate(((0,3),(3,6),(6,7))):
        records.append({'schema_version':'1.0','file':source,'channel':'right','segment_index':i,'start_seconds':a,'end_seconds':b,
            'status':'insufficient_evidence','reason':'synthetic_fixture_no_model_run','scores':None})
    results=score/'results.jsonl';results.write_text(''.join(json.dumps(r)+'\n' for r in records))
    digest=sha256(calls/'dead-air.wav')
    write_json(score/'provenance.json',{'schema_version':'1.0','kind':'nisqa_provenance','results':'results.jsonl',
        'results_sha256':sha256(results),'sources':[{'file':source,'sha256':digest,'unchanged':True}]})
    recording={'audio_sha256':digest,'nisqa':[{'results':'nisqa-fixture/results.jsonl','provenance':'nisqa-fixture/provenance.json','source_file':source,'channel':'right'}]}
    write_json(root/'evidence-nisqa.json',{'schema_version':'1.0','kind':'gap_evidence','recordings':[recording]})
    recording['rtp']=[{'timeline':'media/rtp-timeline-001/timeline.json','sensor':'demo-edge',
        'stream':{'src':'192.0.2.1','src_port':16000,'dst':'192.0.2.2','dst_port':24000,'ssrc':'0x1234'},
        'recording_start_epoch':100,'alignment_verified':True}]
    write_json(root/'evidence.json',{'schema_version':'1.0','kind':'gap_evidence','recordings':[recording]})
    broken=json.loads(json.dumps(recording));broken['rtp'][0]['sensor']='wrong-edge'
    write_json(root/'evidence-wrong.json',{'schema_version':'1.0','kind':'gap_evidence','recordings':[broken]})
    legacy = json.loads(json.dumps(recording))
    legacy['nisqa'][0].pop('provenance')
    legacy['rtp'][0].pop('timeline')
    legacy['rtp'][0]['report'] = 'legacy-report.json'
    write_json(root/'legacy-report.json', {'pcaps': [{'sensor': 'demo-edge', 'analysis': {
        'streams': [{**recording['rtp'][0]['stream'], 'packets': 250}]}}]})
    write_json(root/'evidence-legacy.json', {'schema_version':'1.0', 'kind':'gap_evidence', 'recordings':[legacy]})
    write_json(root/'task.json',{'schema_version':'1.0','id':'output-gaps-demo','title':'输出间隙离线示例',
        'inputs':{'calls':'calls','evidence':'evidence.json'},'steps':[{'id':'gaps','tool':'gaps','action':'analyze',
        'params':{'inputs':[{'input':'calls'}],'evidence':{'input':'evidence'},'include_audio':True,'fail_on_findings':True}}]})
    write_json(root/'expected.json',{'dataset_kind':'synthetic','notice':'独立编写的工程规则预期，不是真实人工黄金集；NISQA 示例没有运行模型。',
        'candidates':{'dead-air':1,'micro':1,'cluster':3,'terminal':1,'allowed-wait':0,'high-occupancy':1}})
    return root


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True)
    root=create(parser.parse_args().out);print(f'合成示例已生成：{root}')
