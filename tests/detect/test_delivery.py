from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from voice_tools.cli import main
from voice_tools.core.files import write_json
from voice_tools.tools.detect import report, service, store
from voice_tools.tools.detect.cli import export_rows
from voice_tools.tools.detect.schema import schema
from .fixtures import config, wav


class Delivery(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.db=self.root/'library.sqlite3'
        self.source=wav(self.root/'sample.wav'); self.config=self.root/'config.json'
        write_json(self.config,config())

    def cli(self,*args):
        output=io.StringIO()
        with redirect_stdout(output):
            code=main(['--json','detect', *map(str,args)])
        result=json.loads(output.getvalue()); self.assertEqual(code,result['exit_code'])
        return code,result

    def test_config_and_batch_query_cli_roundtrip(self):
        self.assertEqual(self.cli('config-add',self.config,'--db',self.db)[0],0)
        exported=self.root/'export.json'
        self.assertEqual(self.cli('config-export','level@1','--db',self.db,'--out',exported)[0],0)
        self.assertEqual(json.loads(exported.read_text()),config())
        code,result=self.cli('run',self.source,'--definition','level@1','--db',self.db,'--batch','one')
        self.assertEqual(code,1); self.assertTrue(result['ok'])
        code,result=self.cli('query','--db',self.db,'--tag','低电平','--status','pending')
        self.assertEqual(result['summary']['count'],1)
        row=result['summary']['findings'][0]
        self.assertEqual(self.cli('review',row['id'],'--db',self.db,'--expected-revision','0','--status','confirmed','--reviewer','tester')[0],0)
        code,result=self.cli('history',row['id'],'--db',self.db)
        self.assertEqual(result['summary']['history'][0]['revision'],1)
        self.assertEqual(self.cli('review',row['id'],'--db',self.db,'--expected-revision','0','--status','confirmed','--reviewer','tester')[0],2)

    def test_invalid_input_and_partial_exit_envelope(self):
        self.assertEqual(self.cli('run',self.source,'--db',self.db)[0],2)
        bad=self.root/'bad.wav'; bad.write_text('broken')
        code,result=self.cli('run',self.source,bad,'--config',self.config,'--db',self.db)
        self.assertEqual(code,3); self.assertFalse(result['ok'])
        self.assertEqual(result['summary']['errors'],1)
        self.assertEqual(self.cli('query','--db',self.root/'absent.sqlite3')[0],2)

    def test_csv_formula_injection_is_escaped_and_json_preserves_label(self):
        with store.connect(self.db,create=True) as db:
            service.run(db,[self.source],[config(label='=HYPERLINK("x")')])
            rows=store.query(db)
        target=self.root/'result.csv'; export_rows(target,rows)
        import csv
        with target.open(encoding='utf-8-sig',newline='') as stream:
            self.assertTrue(list(csv.DictReader(stream))[0]['label'].startswith("'="))
        target=self.root/'result.json'; export_rows(target,rows)
        self.assertEqual(json.loads(target.read_text())[0]['label'],'=HYPERLINK("x")')
        with self.assertRaises(ValueError):
            export_rows(target,rows)

    def test_report_audio_integrity_embedded_data_and_missing_source(self):
        label='</script><img src=x onerror=alert(1)>'
        with store.connect(self.db,create=True) as db:
            service.run(db,[self.source],[config(label=label)])
            result=report.render(db,self.root/'report',True,True)
            self.assertEqual(result['audio_unavailable'],0)
            payload=json.loads((self.root/'report/results.json').read_text())
            self.assertEqual(payload['findings'][0]['label'],label)
            self.assertEqual(payload['records'][0]['sources'],['sample.wav'])
            self.assertEqual(len(payload['records'][0]['playback_sources']),3)
            html=(self.root/'report/index.html').read_text()
            self.assertNotIn(label,html)
            self.assertNotIn('__WAVEFORM_',html)
            self.assertNotIn('__DATA__',html)
            self.assertIn('createReviewWaveform',html)
            self.source.write_text('changed')
            result=report.render(db,self.root/'changed',True)
            self.assertEqual(result['audio_unavailable'],1)
            payload=json.loads((self.root/'changed/results.json').read_text())
            self.assertNotIn('playback_sources',payload['records'][0])
            self.assertTrue(payload['records'][0]['waveform']['channels'])

    def test_schema_and_catalog_are_discoverable(self):
        code,result=self.cli('schema')
        self.assertEqual(result['summary'],schema())
        self.assertEqual(self.cli('catalog')[0],0)
        with self.assertRaises(ValueError):
            from voice_tools.tools.detect.definition import validate
            value=config(); value['metrics']['level']['params']={'window_s':3}; validate(value)

    def test_import_packet_cli_and_export_reopening(self):
        self.cli('run',self.source,'--config',self.config,'--db',self.db,'--batch','one')
        with store.connect(self.db) as db:
            row=store.query(db)[0]
            packet={'schema_version':'1.0','library_id':store.library_id(db),'reviews':[
                {'finding_id':row['id'],'expected_revision':0,'status':'corrected','label':'修正标签',
                 'start_s':1,'end_s':3,'reviewer':'tester','comment':'changed'}],'manual':[]}
        path=self.root/'review.json'; write_json(path,packet)
        self.assertEqual(self.cli('review-import',path,'--db',self.db)[0],0)
        self.assertEqual(self.cli('report','--db',self.db,'--out',self.root/'report')[0],0)
        snapshot=json.loads((self.root/'report/results.json').read_text())
        self.assertEqual(snapshot['findings'][0]['label'],'修正标签')
        self.assertEqual(snapshot['findings'][0]['revision'],1)
