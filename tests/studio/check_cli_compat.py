"""Validate JS-generated scenarios with the current Python SIP CLI. No SIP calls."""
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import wave

ROOT = Path(__file__).resolve().parents[2]


def main():
    node = shutil.which('node')
    if not node:
        raise SystemExit('Node.js is required for the Studio compiler compatibility check')
    script = r'''
const C=require('./docs/sip-studio/core.js');
const env=C.defaultEnv(), item=C.makeCase(env.id), samples=[];
function add(name,update){const s=C.compile(item,env);update(s);samples.push({name,scenario:s});}
add('default',s=>{});
add('wav',s=>s.steps.splice(1,0,{action:'play',file:'assets/tone.wav'}));
add('media',s=>s.steps.splice(1,0,{action:'play_media',file:'media/media.json'}));
add('all-supported',s=>s.steps=[{action:'wait',seconds:0},{action:'play',file:'assets/tone.wav'},{action:'dtmf',digits:'0123456789*#ABCD',method:'sip_info',duration_ms:40,gap_ms:40},{action:'play_media',file:'media/media.json'},{action:'hangup'}]);
add('environment',s=>{s.account.auth={username:'tester',realm:'*',password_env:'STUDIO_TEST_PASSWORD'};s.account.registrar_uri='sip:127.0.0.1:5070';s.account.proxy_uri='sip:127.0.0.1:5070;transport=udp';s.network.bind_address='127.0.0.1';s.codec='PCMU';s.record_early=true;});
const bare=C.importScenario({schema_version:'1.0',target_uri:'sip:a@localhost',steps:[{action:'dtmf',digits:'1'}]});samples.push({name:'minimal-import',scenario:C.compile(bare.item,bare.env)});
const doc=C.importDocument(C.exportDocument(item,env));samples.push({name:'studio-roundtrip',scenario:C.compile(doc.item,doc.env)});
console.log(JSON.stringify(samples));
'''
    samples = json.loads(subprocess.check_output([node, '-e', script], cwd=ROOT))
    process_env = {**os.environ, 'PYTHONPATH': str(ROOT / 'src')}
    with tempfile.TemporaryDirectory(prefix='sip-studio-check-') as tmp:
        folder = Path(tmp)
        (folder / 'assets').mkdir()
        (folder / 'media').mkdir()
        audio = folder / 'assets/tone.wav'
        with wave.open(str(audio), 'wb') as out:
            out.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
            out.writeframes(b''.join(struct.pack('<h', int(2000 * math.sin(2 * math.pi * 440 * i / 8000))) for i in range(8000)))
        shutil.copyfile(audio, folder / 'media/audio.wav')
        manifest = {'schema_version': '1.0', 'audio': 'audio.wav', 'audio_sha256': hashlib.sha256(audio.read_bytes()).hexdigest(), 'duration_s': 1, 'dtmf': [{'at_s': 0.1, 'digit': '1', 'duration_ms': 160, 'end_observed': True}], 'source': {}, 'warnings': []}
        (folder / 'media/media.json').write_text(json.dumps(manifest))
        for sample in samples:
            scenario = folder / (sample['name'] + '.json')
            scenario.write_text(json.dumps(sample['scenario']))
            for action in ['validate', 'dry-run']:
                argv = [sys.executable, '-m', 'voice_tools', '--json', 'sip']
                argv += ['validate', str(scenario)] if action == 'validate' else ['run', str(scenario), '--dry-run', '--out', str(folder / ('plan-' + sample['name']))]
                result = subprocess.run(argv, cwd=ROOT, env=process_env, text=True, capture_output=True)
                assert result.returncode == 0, (sample['name'], action, result.stdout, result.stderr)
                json.loads(result.stdout)
            print('PASS ' + sample['name'] + ': CLI validate + dry-run')
        # The authoritative validator must reject missing assets, even when browser field checks pass.
        broken = folder / 'missing.json'
        broken.write_text(json.dumps({**samples[0]['scenario'], 'steps': [{'action': 'play', 'file': 'missing.wav'}]}))
        result = subprocess.run([sys.executable, '-m', 'voice_tools', '--json', 'sip', 'validate', str(broken)], cwd=ROOT, env=process_env, text=True, capture_output=True)
        assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
        print('PASS missing asset: CLI rejected with exit 2')
    print('8 compatibility cases passed; 14 successful CLI commands + 1 expected rejection; no SIP calls')


if __name__ == '__main__':
    main()
