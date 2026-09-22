"""Deterministic workspace fixture using only installed product code and synthetic sound."""
import argparse
from pathlib import Path
import numpy as np
from voice_tools.audio.io import write_wav
from voice_tools.tools.detect import store, workspace
from voice_tools.tools.detect.definition import validate
from voice_tools.tools.detect.service import run

parser = argparse.ArgumentParser()
parser.add_argument('output', type=Path)
parser.add_argument('--standards', action='store_true')
args = parser.parse_args()
inputs = args.output / 'inputs'; inputs.mkdir(parents=True)
for name, level in [('quiet', .005), ('loud', .2)]:
    t = np.arange(64000) / 8000
    audio = np.column_stack((.1 * np.sin(2*np.pi*440*t), level * np.sin(2*np.pi*330*t)))
    write_wav(inputs / (name + '.wav'), audio.astype(np.float32), 8000)
with store.connect(args.output / 'library.sqlite3', create=True) as db:
    for version, threshold in [('1', -30), ('2', -60)]:
        definition = validate({'schema_version':'1.0','id':'level','version':version,'name':'低电平检测',
          'metrics':{'level':{'kind':'rms_dbfs','channel':1}},
          'rules':[{'id':'quiet','label':'低电平','when':{'metric':'level','op':'lt','value':threshold}}]})
        store.save_definition(db, definition)
    if args.standards:
        workspace.initialize(db)
        run(db, [inputs], [store.select_definition(db, 'level@1')], 'seed')
        for index, row in enumerate(store.evaluation_rows(db)):
            quiet = Path(row['sources'][0]).stem == 'quiet'
            workspace.save_standard(db, {'id':f'seed-{index}','expected_revision':0,'recording_id':row['recording_id'],
              'label':'低电平','start_s':0,'end_s':8,'verdict':'problem' if quiet else 'normal',
              'reviewer':'业务复核','comment':'合成验收标准','checked':True})
