import json
import re
from pathlib import Path
import subprocess
import unittest

from voice_tools.tools.detect import editor
from voice_tools.tools.detect.metrics import catalog
from voice_tools.tools.detect.schema import schema
from .fixtures import config


class EditorContract(unittest.TestCase):
    def node(self, values):
        script=Path(editor.__file__).with_name('editor-contract.js')
        program='''const c=require(process.argv[1]);let s='';process.stdin.on('data',d=>s+=d);process.stdin.on('end',()=>{
          const data=JSON.parse(s);console.log(JSON.stringify(data.values.map(raw=>{try{return {ok:true,value:c.normalize(typeof raw==='string'?c.parse(raw):raw,data.catalog,data.schema)}}catch(e){return {ok:false,error:e.message}}})));});'''
        run=subprocess.run(['node','-e',program,str(script)],input=json.dumps({'catalog':catalog(),'schema':schema(),'values':values}),text=True,capture_output=True,check=True)
        return json.loads(run.stdout)

    def test_rejects_unknown_fields_refs_params_and_duplicate_json_keys(self):
        variants=[]
        changes=[lambda c:c.update(unknown=True),lambda c:c.update(__proto__={'polluted':True}),
                 lambda c:c.update(constructor={'unexpected':True}),lambda c:c['metrics']['level']['params'].update(wrong=1),
                 lambda c:c['rules'][0]['when'].update(metric='absent'),lambda c:c['scope'].update(end_s=0),
                 lambda c:c['metrics']['level'].update(channel=True),lambda c:c['rules'][0].update(label='  ')]
        for change in changes:
            value=config();change(value);variants.append(value)
        text=json.dumps(config()).replace('"version": "1"','"version": "1", "version": "2"')
        results=self.node(variants+[text])
        self.assertTrue(all(not result['ok'] for result in results),results)

    def test_nested_limit_empty_all_and_invalid_thresholds_are_rejected(self):
        value=config();leaf=value['rules'][0]['when']
        for _ in range(10):leaf={'not':leaf}
        value['rules'][0]['when']=leaf
        empty=config();empty['rules'][0]['when']={'all':[]}
        too_many=config();too_many['rules'][0]['when']={'all':[{'metric':'level','op':'lt','value':0}]*129}
        self.assertTrue(all(not result['ok'] for result in self.node([value,empty,too_many])))

    def test_offline_document_is_self_contained_and_escapes_user_text(self):
        value=config(label='</script><img src=x onerror=alert(1)>')
        page=editor.document([value],back_link='review.html')
        self.assertNotIn(value['rules'][0]['label'],page)
        match=re.search(r'<script id="editorData"[^>]*>(.*?)</script>',page,re.S)
        payload=json.loads(match[1]);self.assertIsNone(payload['session'])
        self.assertEqual(payload['definitions'][0],value)
        self.assertIn('href="review.html"',page)
