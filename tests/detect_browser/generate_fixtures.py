"""Installed-package fixtures; no business recordings or source-script parity claims."""
import argparse
import json
from pathlib import Path

import numpy as np
import voice_tools
from voice_tools.audio.io import write_wav
from voice_tools.tools.detect import report, service, store
from voice_tools.tools.detect.definition import validate

parser=argparse.ArgumentParser()
parser.add_argument('--out',type=Path,required=True)
parser.add_argument('--package-root',type=Path,required=True)
args=parser.parse_args()
assert Path(voice_tools.__file__).resolve().is_relative_to(args.package_root.resolve()), voice_tools.__file__
args.out.mkdir(parents=True,exist_ok=True)
inputs=args.out/'inputs'; inputs.mkdir()
for name, amplitude in [('quiet',.005),('loud',.4),('silent',0)]:
    samples=np.zeros((64000,2),dtype=np.float32)
    samples[8000:16000,0]=.1*np.sin(2*np.pi*440*np.arange(8000)/8000)
    samples[:,1]=amplitude*np.sin(2*np.pi*330*np.arange(64000)/8000)
    write_wav(inputs/(name+'.wav'),samples,8000)
value=validate({'schema_version':'1.0','id':'level','version':'1','name':'演示电平',
  'metrics':{'rms':{'kind':'rms_dbfs','channel':1}},
  'rules':[{'id':'low','label':'AI低音量','when':{'metric':'rms','op':'lt','value':-30}},
           {'id':'quiet','label':'待核实','when':{'metric':'rms','op':'lt','value':-30}}]})
db_path=args.out/'library.sqlite3'
with store.connect(db_path,create=True) as db:
    service.run(db,[inputs],[value],'baseline')
    value['version']='2'; value['rules'][0]['when']['value']=-60
    service.run(db,[inputs],[value],'updated')
    value['id']='unsafe-label'; value['rules']=[{'id':'xss','label':'</script><img src=x onerror=alert(1)>',
        'when':{'metric':'rms','op':'lt','value':-100}}]
    service.run(db,[inputs],[value],'escaped')
    report.render(db,args.out/'report',include_audio=True,hide_paths=True)
    report.render(db,args.out/'no-audio',include_audio=False,hide_paths=True)
from voice_tools.tools.recording_qa.batch import analyze_batch
analyze_batch([inputs], args.out/'qa', include_audio=True, hide_paths=True)
print(json.dumps({'package':str(Path(voice_tools.__file__).resolve()),'report':str(args.out/'report/index.html')}))
