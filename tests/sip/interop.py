"""Opt-in, bounded SIP acceptance against real peers. Never run on test discovery.

Usage: python -m tests.sip.interop --phase pjsua --out NEW_DIRECTORY
Public calls require the explicit --phase public switch. No accounts are created.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import random
import re
import signal
import socket
import struct
import subprocess
import sys
import time
import wave

import numpy as np

from tests.sip.fixtures import CaptureWriter, rtp
from voice_tools.tools.sip.scenario import template

ROOT = Path(__file__).resolve().parents[2]
PJSUA = Path(os.environ.get('VOICE_SIP_PJSUA', str(ROOT / '.local/sip-build/pjproject-2.17/pjsip-apps/bin/pjsua-aarch64-apple-darwin25.6.0')))
PREFIX = Path(os.environ.get('VOICE_SIP_BARESIP_PREFIX', str(ROOT / '.local/sip-interop-build/prefix')))
BARESIP = PREFIX / 'bin/baresip'
PJ_URL = 'https://github.com/pjsip/pjproject/blob/2.17/tests/pjsua/'
BA_URL = 'https://github.com/baresip/baresip/blob/f48c14bc35b55eb4443c627ef77f578a91e2a9ff/'
SOURCES = {
    'pj-call': PJ_URL + 'mod_call.py',
    'pj-wave': PJ_URL + 'mod_media_playrec.py',
    'pj-play': PJ_URL + 'scripts-call-wav/600_playwav_basic.py',
    'pj-bye': PJ_URL + 'scripts-sipp/uas-early-bye.xml',
    'ba-call': BA_URL + 'test/call.c',
    'ba-wave': BA_URL + 'test/play.c',
    'ba-source': BA_URL + 'test/ausrc.c',
    'sipp-pcap': 'https://github.com/SIPp/sipp/blob/v3.7.7/docs/media.rst',
    'sip2sip': 'https://sip2sip.info/help/',
    'iptel': 'https://www.iptel.org/#faq',
}


def case(id, phase, title, mode='echo', codec='PCMA', digits='', method='rfc4733', sources=None, **kw):
    return dict(id=id, phase=phase, title=title, mode=mode, codec=codec, digits=digits,
                method=method, sources=sources or ['pj-call', 'pj-wave'], **kw)


CASES = [
    case('P01', 'pjsua', 'PCMA 双向回声与对端录音'),
    case('P02', 'pjsua', 'PCMU 双向回声与对端录音', codec='PCMU'),
    case('P03', 'pjsua', '双向不同 WAV：对端提示音与客户端播放', mode='prompt', sources=['pj-play', 'pj-wave']),
    case('P04', 'pjsua', 'RFC4733 重复数字与特殊按键', digits='11220*#'),
    case('P05', 'pjsua', 'SIP INFO 数字、星号与井号', digits='12*#', method='sip_info'),
    case('P06', 'pjsua', '486 拒接保留失败原因', mode='busy', sources=['ba-call']),
    case('P07', 'pjsua', '不接听超时并清理呼叫', mode='timeout', sources=['ba-call']),
    case('P08', 'pjsua', '对端提前挂断保留录音', mode='remote_bye', sources=['pj-bye']),
    case('P09', 'pjsua', 'PCAP 转音频与按键后执行', mode='pcap', digits='1', sources=['sipp-pcap', 'pj-call', 'pj-wave']),
    case('B01', 'baresip', '独立栈 PCMA 音频回声', sources=['ba-call', 'ba-wave']),
    case('B02', 'baresip', '独立栈 PCMU 音频回声', codec='PCMU', sources=['ba-call', 'ba-wave']),
    case('B03', 'baresip', 'RFC4733 重复按键往返', digits='11220*#', sources=['ba-call']),
    case('B04', 'baresip', 'SIP INFO 输入与 RFC4733 回送', digits='12*#', method='sip_info', sources=['ba-call']),
    case('B05', 'baresip', '独立栈双向 WAV 与对端接收录音', mode='prompt', sources=['ba-source', 'ba-wave']),
    case('B06', 'baresip', '编码无交集时拒绝呼叫', mode='codec_mismatch', sources=['ba-call']),
    case('B07', 'baresip', 'PCAP 导入的音频与按键跨栈复现', mode='pcap', digits='1', sources=['sipp-pcap', 'ba-call', 'ba-wave']),
    case('N01', 'public', 'SIP2SIP 4444 公网回声', provider='sip2sip.info', user='4444', sources=['sip2sip']),
    case('N02', 'public', 'SIP2SIP 3333 公网接收音频', mode='announcement', provider='sip2sip.info', user='3333', sources=['sip2sip']),
    case('N03', 'public', 'IPTel echo 公网回声', provider='iptel.org', user='echo', sources=['iptel']),
    case('N04', 'public', 'IPTel music 公网接收音频', mode='announcement', provider='iptel.org', user='music', sources=['iptel']),
]


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def marker(path, variant=0, seconds=1.6):
    """Deterministic non-speech marker; changing pitch/envelope avoids pure-tone ambiguity."""
    t = np.arange(round(8000 * seconds)) / 8000
    f = 320 + variant * 180
    phase = 2 * np.pi * (f * t + (170 + variant * 50) * t * t)
    env = (0.65 + 0.3 * np.sin(2 * np.pi * 2.7 * t)) * np.minimum(1, t / .03) * np.minimum(1, (seconds-t) / .03)
    pcm = np.round(9500 * env * (np.sin(phase) + .2 * np.sin(phase * 1.7))).astype('<i2')
    with wave.open(str(path), 'wb') as out:
        out.setparams((1, 2, 8000, 0, 'NONE', 'not compressed')); out.writeframes(pcm.tobytes())
    return pcm


def read_wav(path):
    with wave.open(str(path), 'rb') as f:
        if f.getnchannels() != 1 or f.getsampwidth() != 2 or f.getframerate() != 8000:
            raise ValueError('acceptance expects mono 8kHz PCM16')
        return np.frombuffer(f.readframes(f.getnframes()), dtype='<i2').astype(float)


def audio_metrics(path, reference=None):
    x = read_wav(path)
    out = dict(duration_s=len(x)/8000, rms=float(np.sqrt(np.mean(x*x))) if len(x) else 0,
               samples=len(x), sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest())
    if reference:
        # Ten ordered 100 ms windows tolerate jitter-buffer time adjustment.
        # Search consistent offsets so a looping prompt cannot match different cycles.
        reference_pcm = read_wav(reference)
        starts = list(range(2400, 10400, 800)); n = 800
        if len(x) < n or len(reference_pcm) < 10400:
            out.update(correlation=0.0, matched=False, windows=[]); return out
        size = 1 << (len(x)+n-2).bit_length(); fx = np.fft.rfft(x, size)
        sums = np.r_[0., np.cumsum(x)]; sq = np.r_[0., np.cumsum(x*x)]
        energy = np.maximum(0, sq[n:]-sq[:-n]-(sums[n:]-sums[:-n])**2/n)
        all_scores = []
        for start in starts:
            y = reference_pcm[start:start+n]; y = y-y.mean()
            corr = np.fft.irfft(fx*np.fft.rfft(y[::-1], size), size)[n-1:len(x)]
            scores = corr/np.maximum(1, np.sqrt(energy)*np.linalg.norm(y))
            scores[energy<n*100**2] = 0
            all_scores.append(np.clip(scores, -1, 1))
        first = all_scores[0].copy(); offsets = []
        for _ in range(8):
            idx = int(np.argmax(first)); offsets.append(idx-starts[0])
            first[max(0, idx-800):idx+800] = 0
        options = []
        for offset in offsets:
            windows = []
            for start, scores in zip(starts, all_scores):
                lo=max(0, start+offset-640); hi=min(len(scores), start+offset+641)
                if hi <= lo:
                    windows.append(dict(correlation=0.0, offset_s=0.0)); continue
                idx=lo+int(np.argmax(scores[lo:hi]))
                windows.append(dict(correlation=float(scores[idx]), offset_s=(idx-start)/8000))
            scores=[w['correlation'] for w in windows];lags=[w['offset_s'] for w in windows]
            spread=max(lags)-min(lags); median=float(np.median(scores))
            matched=sum(s>=.65 for s in scores)>=8 and median>=.85 and spread<=.12
            options.append(dict(correlation=median, windows=windows, offset_spread_s=spread, matched=matched))
        out.update(max(options, key=lambda v:(v['matched'], v['correlation'])))
    return out


def free_ports(address='127.0.0.1'):
    for _ in range(100):
        port = random.randrange(22000, 48000, 2)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as a, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as b:
            try: a.bind((address, port)); b.bind((address, port+1)); return port
            except OSError: pass
    raise RuntimeError('no free UDP port pair')


def wait_log(proc, path, pattern, seconds=6):
    end = time.monotonic()+seconds
    while time.monotonic() < end and proc.poll() is None:
        if re.search(pattern, path.read_text(errors='replace')): return
        time.sleep(.05)
    raise RuntimeError('peer not ready; see peer.log')


def stop_peer(proc):
    if proc.poll() is None:
        try: proc.stdin.write('q\n'); proc.stdin.flush(); proc.wait(timeout=4)
        except (OSError, subprocess.TimeoutExpired): proc.terminate()
    if proc.poll() is None:
        try: proc.wait(timeout=3)
        except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=3)


def start_peer(c, out, port, media):
    log = out/'peer.log'; mode=c['mode']
    if c['phase']=='pjsua':
        args = [str(PJSUA), '--id=sip:peer@127.0.0.1:%d'%port, '--bound-addr=127.0.0.1', '--ip-addr=127.0.0.1',
                '--local-port=%d'%port, '--rtp-port=%d'%media, '--no-tcp', '--null-audio', '--no-vad', '--clock-rate=8000',
                '--max-calls=1', '--duration=1' if mode=='remote_bye' else '--duration=25', '--no-color',
                '--app-log-level=5', '--log-level=5', '--auto-rec', '--rec-file='+str(out/'peer-rx.wav')]
        if mode!='timeout': args += ['--auto-answer=486' if mode=='busy' else '--auto-answer=200']
        if mode=='prompt': args += ['--play-file='+str(out/'prompt.wav'), '--auto-play']
        else: args += ['--auto-loop']
        pattern = r'pjsua version .*started|>>>|Press .h.'
    else:
        cfg=out/'peer-config'; cfg.mkdir()
        echo=mode not in ('prompt','codec_mismatch')
        mods = ['stdio','g711','aufile','aubridge','auconv','auresamp']
        text = f'''sip_listen 127.0.0.1:{port}
net_interface 127.0.0.1
sip_transports udp
sip_trans_def udp
rtp_ports {media}-{media+10}
rtp_stats yes
call_max_calls 1
call_accept yes
audio_player {'aubridge,echo' if echo else 'aufile,'+str(out/'peer-rx.wav')}
audio_source {'aubridge,echo' if echo else 'aufile,'+str(out/'prompt.wav')}
audio_alert aubridge,alert
ausrc_srate 8000
auplay_srate 8000
ausrc_channels 1
auplay_channels 1
ausrc_format s16
auplay_format s16
audio_jitter_buffer_ms 40-80
module_path {PREFIX}/lib/baresip/modules
'''
        text += ''.join('module '+m+'.so\n' for m in mods)
        text += 'module_app account.so\nmodule_app '+('echo' if echo else 'menu')+'.so\n'
        (cfg/'config').write_text(text)
        codec='PCMU' if mode=='codec_mismatch' else c['codec']
        (cfg/'accounts').write_text(f'<sip:peer@127.0.0.1:{port};transport=udp>;regint=0;answermode=auto;audio_codecs={codec}/8000/1\n')
        args=[str(BARESIP), '-f', str(cfg), '-4', '-v', '-s', '-c', '-t', '25']; pattern=r'baresip is ready'
    stream=log.open('w')
    proc=subprocess.Popen(args, cwd=out, stdin=subprocess.PIPE, stdout=stream, stderr=subprocess.STDOUT, text=True)
    save(out/'peer-command.json', args)
    try: wait_log(proc, log, pattern)
    except Exception: stop_peer(proc); stream.close(); raise
    return proc, stream


def pcap_bundle(out):
    from voice_tools.tools.sip.pcap import read_capture, import_capture
    with wave.open(str(out/'marker.wav')) as wav: pcm=wav.readframes(wav.getnframes())
    # Pure NumPy G.711 A-law fixture encoding; audioop was removed in Python 3.13.
    linear=np.frombuffer(pcm,dtype='<i2').astype(np.int32)>>3
    mask=np.where(linear>=0,0xd5,0x55)
    magnitude=np.where(linear>=0,linear,-linear-1)
    segment=np.searchsorted([31,63,127,255,511,1023,2047,4095],magnitude)
    encoded=(((segment<<4)|((magnitude>>np.where(segment<2,1,segment))&15))^mask).astype('uint8').tobytes()
    writer=CaptureWriter(out/'source.pcap'); seq=1
    for i in range(len(pcm)//320):
        writer.write(1000+i*.02,'127.0.0.1',4000,'127.0.0.1',5000,rtp(seq,i*160,encoded[i*160:(i+1)*160]));seq+=1
        if 20 <= i < 25:
            end=i>=22; duration=min(1280,(i-19)*400)
            writer.write(1000+i*.02+.001,'127.0.0.1',4000,'127.0.0.1',5000,rtp(seq,3200,struct.pack('!BBH',1,10|(128 if end else 0),duration),101));seq+=1
    writer.close()
    data=read_capture(out/'source.pcap',[4000,5000]); stream=next(iter(data['streams']))
    import_capture(data,stream,out/'bundle',dtmf_pt=101)
    return out/'bundle/media.json'


def public_route(c, out):
    q='_sip._udp.'+c['provider']
    p=subprocess.run(['/usr/bin/dig','+time=3','+tries=1','+short',q,'SRV'],capture_output=True,text=True,timeout=8)
    records=[]
    for line in p.stdout.splitlines():
        parts=line.split()
        if len(parts)==4 and all(s.isdigit() for s in parts[:3]):records.append((int(parts[0]),int(parts[1]),int(parts[2]),parts[3].rstrip('.')))
    save(out/'dns.json',dict(query=q,stdout=p.stdout,stderr=p.stderr,records=records,at=datetime.datetime.now(datetime.timezone.utc).isoformat()))
    if not records: raise RuntimeError('No current SIP UDP SRV record; no public INVITE sent')
    _,_,port,host=sorted(records)[0]
    return 'sip:'+host+':'+str(port)+';transport=udp'


def run_case(c, base):
    out=(base/c['id']).resolve();out.mkdir()
    result=dict(c, status='ERROR', assertions=[], metrics={}, started_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    result['source_urls']=[SOURCES[s] for s in c['sources']]
    proc=stream=None
    def check(name, ok, actual=None): result['assertions'].append(dict(name=name,passed=bool(ok),actual=actual))
    try:
        marker(out/'marker.wav');marker(out/'prompt.wav',1)
        spec=template();spec['codec']=c['codec'];spec['connect_timeout_s']=12 if c['phase']=='public' else 3
        spec['max_call_s']=15; spec['network']={'sip_port':0,'rtp_port':free_ports()}
        if c['phase']=='public':
            spec['target_uri']=f"sip:{c['user']}@{c['provider']}"
            spec['account']['proxy_uri']=public_route(c,out)
            # Provider tests receive only this synthetic marker, never user recordings.
        else:
            port,media=free_ports(),free_ports()
            proc,stream=start_peer(c,out,port,media)
            spec['target_uri']=f'sip:peer@127.0.0.1:{port}'
            spec['network']['bind_address']='127.0.0.1'
        mode=c['mode'];steps=[{'action':'wait','seconds':.3}]
        if mode=='announcement':steps += [{'action':'wait','seconds':5}]
        elif mode=='pcap':steps += [{'action':'play_media','file':str(pcap_bundle(out))}]
        elif mode=='remote_bye': steps += [{'action':'wait','seconds':4}]
        else:steps += [{'action':'play','file':'marker.wav'}]
        if c['digits'] and mode!='pcap':steps += [{'action':'dtmf','digits':c['digits'],'method':c['method'],'duration_ms':160,'gap_ms':180}]
        steps += [{'action':'wait','seconds':1 if c['phase']=='public' else .5},{'action':'hangup'}]
        spec['steps']=steps;save(out/'scenario.json',spec)
        args=[sys.executable,'-m','voice_tools','--json','sip','run',str(out/'scenario.json'),'--out',str(out/'run')]
        save(out/'command.json',args)
        run=subprocess.run(args,cwd=ROOT,capture_output=True,text=True,timeout=40)
        (out/'stdout.json').write_text(run.stdout);(out/'stderr.log').write_text(run.stderr)
        result['exit_code']=run.returncode
        call=json.loads((out/'run/result.json').read_text());result['call']=call
        events=[json.loads(l) for l in (out/'run/events.jsonl').read_text().splitlines()]
        if proc:
            stop_peer(proc);stream.close();result['peer_exit_code']=proc.returncode
        peerlog=(out/'peer.log').read_text(errors='replace') if proc else ''
        code=call.get('error',{}).get('code');sip=call.get('call',{}).get('last_sip_code')
        if mode in ('busy','timeout','remote_bye','codec_mismatch'):
            check('CLI 非零退出且标记失败',run.returncode==3 and call['status']=='failed',call['status'])
            if mode=='busy':check('对端返回 486',sip==486,sip)
            if mode=='timeout':
                check('等待超时',code=='WAIT_TIMEOUT',code);check('发送 CANCEL 清理待接通呼叫','CANCEL sip:' in peerlog)
            if mode=='remote_bye':
                check('识别远端提前挂断',code=='REMOTE_HANGUP',code);check('保留接收录音',(out/'run/rx.wav').exists())
            if mode=='codec_mismatch':check('拒绝不兼容编码',sip in (488,606),sip)
        else:
            check('策略完成且 CLI 成功',run.returncode==0 and call['status']=='completed',call['status'])
            check('协商指定编码',str(call.get('call',{}).get('codec_negotiated','')).upper()==c['codec'],call.get('call',{}).get('codec_negotiated'))
            if (out/'run/rx.wav').exists():
                ref=None if mode=='announcement' else out/('prompt.wav' if mode=='prompt' else 'marker.wav')
                metrics=audio_metrics(out/'run/rx.wav',ref);result['metrics']['rx']=metrics
                check('接收 WAV 有有效音频',metrics['duration_s']>=1 and metrics['rms']>100,metrics)
                if ref:check('十段信号匹配且时间偏移一致',metrics.get('matched',False),metrics)
            else:check('接收 WAV 存在',False)
            if c['phase']=='pjsua' or (c['phase']=='baresip' and mode=='prompt'):
                if (out/'peer-rx.wav').exists():
                    m=audio_metrics(out/'peer-rx.wav',out/'marker.wav');result['metrics']['peer_rx']=m
                    check('对端录音收到发送信号',m.get('matched',False),m)
                else:check('对端录音存在',False)
            if c['digits']:
                got=''.join(re.findall(r'Incoming DTMF on call \d+: (.)',peerlog)) if c['phase']=='pjsua' else ''.join(e['digit'] for e in events if e['event']=='dtmf_received')
                check('按键顺序和重复次数一致',got==c['digits'],got)
                if c['phase']=='pjsua':check('接收端确认 DTMF 传输方式',('using SIP INFO method' if c['method']=='sip_info' else 'using RFC2833 method') in peerlog)
            if proc:check('对端退出并释放进程',proc.returncode==0,proc.returncode)
        result['status']='PASS' if all(a['passed'] for a in result['assertions']) else 'FAIL'
        if c['phase']=='public' and call['status']!='completed':result['status']='BLOCKED_EXTERNAL'
    except Exception as exc:
        result['error']=f'{type(exc).__name__}: {exc}'
        if c['phase']=='public':result['status']='BLOCKED_EXTERNAL'
    finally:
        if proc:stop_peer(proc)
        if stream and not stream.closed:stream.close()
        result['finished_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
        save(out/'acceptance.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase',choices=['pjsua','baresip','public'],required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--case',action='append',default=[])
    args=parser.parse_args();args.out=args.out.resolve();args.out.mkdir(parents=True,exist_ok=False)
    selected=[c for c in CASES if c['phase']==args.phase and (not args.case or c['id'] in args.case)]
    if not selected:parser.error('no matching cases')
    save(args.out/'case-plan.json',dict(cases=selected,sources=SOURCES))
    results=[]
    for c in selected:
        r=run_case(c,args.out);results.append(r);save(args.out/'results.json',results)
        print(c['id'],r['status'],r.get('error',''),flush=True)
        if args.phase=='public':time.sleep(2)
    return int(any(r['status']!='PASS' for r in results))


if __name__=='__main__':raise SystemExit(main())
