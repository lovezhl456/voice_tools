#!/usr/bin/env python3
"""Check installed detection UI using the existing pinned Playwright and Range server."""
import argparse
from functools import partial
import json
import os
from pathlib import Path
import subprocess
import queue
import sys
import threading
from http.server import ThreadingHTTPServer

from check_review import QuietHandler, ROOT


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package-root',type=Path,required=True,help='Installed wheel package directory from check_review.py')
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    output=args.out.resolve(); package=args.package_root.resolve()
    if not list(package.glob('voice_tools-*.dist-info/RECORD')):
        parser.error('--package-root must be an installed wheel, not the source directory')
    if output.exists() and any(output.iterdir()):
        parser.error('--out must be new or empty')
    # Verify the shipped package contains every runtime file from this final source state.
    for source in (ROOT/'src/voice_tools').rglob('*'):
        if not source.is_file() or '__pycache__' in source.parts or source.suffix == '.pyc':
            continue
        installed=package/'voice_tools'/source.relative_to(ROOT/'src/voice_tools')
        if not installed.is_file() or installed.read_bytes() != source.read_bytes():
            parser.error('Installed package differs from source: '+str(source.relative_to(ROOT)))
    output.mkdir(parents=True,exist_ok=True)
    env={**os.environ,'PYTHONPATH':str(package),'VT_DETECT_PYTHON':sys.executable,'VT_DETECT_OUTPUT':str(output)}
    server=None
    editor_process=None
    editor_log=None
    result={'status':'failed','python':sys.version, 'package_root':str(package),
            'scope':'installed wheel, synthetic audio, desktop/mobile UI and CLI round trip'}
    def run_step(name,command):
        with (output/(name+'.log')).open('w') as stream:
            run=subprocess.run(command,cwd=ROOT,env=env,stdout=stream,stderr=subprocess.STDOUT)
        if run.returncode:
            raise RuntimeError(name+' failed; see '+str(output/(name+'.log')))
    try:
        run_step('fixtures',[sys.executable,str(ROOT/'tests/detect_browser/generate_fixtures.py'),
                            '--out',str(output/'site'),'--package-root',str(package)])
        server=ThreadingHTTPServer(('127.0.0.1',0),partial(QuietHandler,directory=str(output/'site')))
        threading.Thread(target=server.serve_forever,daemon=True).start()
        env['VT_DETECT_URL']=f'http://127.0.0.1:{server.server_port}'
        # Start the real editor service from the same installed package, not source imports.
        editor_log=(output/'editor-server.log').open('w')
        editor_process=subprocess.Popen([sys.executable,'-m','voice_tools','--json','detect','serve',
            str(output/'site/inputs'),'--db',str(output/'editor.sqlite3'),'--out',str(output/'editor-service'),'--port','0'],
            cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=editor_log,text=True)
        ready=queue.Queue()
        threading.Thread(target=lambda:ready.put(editor_process.stdout.readline()),daemon=True).start()
        try:
            startup=json.loads(ready.get(timeout=15))
        except (queue.Empty,ValueError) as error:
            raise RuntimeError('Editor service did not become ready; see editor-server.log') from error
        if startup.get('status') != 'listening':
            raise RuntimeError('Editor service startup failed: '+str(startup))
        env['VT_EDITOR_URL']=startup['url']
        result['editor_url']=startup['url']
        run_step('browser',['node',str(ROOT/'tests/browser/node_modules/@playwright/test/cli.js'),'test',
                            '--config',str(ROOT/'tests/detect_browser/playwright.config.cjs')])
        report=json.loads((output/'browser-results.json').read_text())
        result['stats']=report['stats']
        stats=report['stats']
        coverage={}
        def visit(suite):
            for spec in suite.get('specs', []):
                capability=spec['title'].split(' ', 1)[0]
                for test in spec['tests']:
                    coverage.setdefault(test['projectName'], []).append(capability)
            for child in suite.get('suites', []):
                visit(child)
        visit(report)
        coverage={key:sorted(values) for key,values in coverage.items()}
        expected={name:['D01','D02','D03','D04','D05','E01','E02','E03','E04'] for name in ('desktop','mobile')}
        result['capabilities']=coverage
        if coverage != expected:
            raise RuntimeError('D01–D05 and E01–E04 desktop/mobile capability coverage is incomplete')
        if stats['expected'] != 18 or any(stats[key] for key in ('unexpected','skipped','flaky')):
            raise RuntimeError('Expected D01–D05 and E01–E04 on desktop/mobile with no failures, skips or retries')
        result['status']='passed'
        print('Passed 18 installed detection/editor browser cases: '+str(output))
        return 0
    except (OSError,ValueError,RuntimeError) as error:
        result['error']=str(error); print(error,file=sys.stderr); return 1
    finally:
        if editor_process:
            editor_process.terminate()
            try:
                editor_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                editor_process.kill();editor_process.wait(timeout=5)
            if editor_process.stdout:
                editor_process.stdout.close()
        if editor_log:
            editor_log.close()
        if server:
            server.shutdown(); server.server_close()
        (output/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':
    raise SystemExit(main())
