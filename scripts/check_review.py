#!/usr/bin/env python3
"""Build the shipped wheel and exercise its review pages with a local browser."""
import argparse
from datetime import datetime, timezone
from functools import partial
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import uuid

ROOT = Path(__file__).resolve().parents[1]
BROWSER = ROOT / 'tests/browser'
REQUIRED_CAPABILITIES = ('R01', 'R02', 'R03', 'R04', 'R05', 'R06', 'R07', 'R08')
OPTIONAL_TEST_ENV = ('VOICE_TOOLS_SIP_LOOPBACK', 'VOICE_TOOLS_NISQA_MODEL_DIR',
                     'VOICE_TOOLS_NISQA_AUDIO', 'VOICE_TOOLS_TEST_LATENCY_DIR',
                     'VOICE_TOOLS_TEST_QA_MODEL_DIR', 'VOICE_TOOLS_TEST_QA_AUDIO')


class QuietHandler(SimpleHTTPRequestHandler):
    """Read-only fixture server with byte ranges, like the deployed audio host."""

    def log_message(self, *args):
        pass  # Browser failures and trace artifacts contain the relevant request details.

    def send_head(self):
        self.remaining = None
        filename = Path(self.translate_path(self.path))
        header = self.headers.get('Range')
        if not header or not filename.is_file():
            return super().send_head()
        stream = filename.open('rb')
        size = os.fstat(stream.fileno()).st_size
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', header)
        start, end = 0, size - 1
        if match and (match[1] or match[2]):
            if match[1]:
                start = int(match[1])
                end = min(int(match[2]), size - 1) if match[2] else size - 1
            else:
                start = max(0, size - int(match[2]))
        else:
            start = size
        if start >= size or end < start:
            stream.close()
            self.send_response(416)
            self.send_header('Content-Range', f'bytes */{size}')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return None
        stream.seek(start)
        self.remaining = end - start + 1
        self.send_response(206)
        self.send_header('Content-Type', self.guess_type(str(filename)))
        self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.send_header('Content-Length', str(self.remaining))
        self.end_headers()
        return stream

    def end_headers(self):
        self.send_header('Accept-Ranges', 'bytes')
        super().end_headers()

    def copyfile(self, source, target):
        if self.remaining is None:
            return super().copyfile(source, target)
        remaining = self.remaining
        while remaining:
            chunk = source.read(min(65536, remaining))
            if not chunk:
                break
            target.write(chunk)
            remaining -= len(chunk)


def covered_capabilities(report):
    covered = {}
    def visit(suite):
        for spec in suite.get('specs', []):
            match = re.match(r'(R\d{2})\b', spec['title'])
            if match:
                for test in spec['tests']:
                    covered.setdefault(test['projectName'], []).append(match[1])
        for child in suite.get('suites', []):
            visit(child)
    visit(report)
    return {project: sorted(ids) for project, ids in covered.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, help='New or empty evidence directory')
    parser.add_argument('--wheel', type=Path, help='Reuse an already built voice-tools wheel')
    parser.add_argument('--project', choices=('desktop', 'mobile'))
    parser.add_argument('--grep', help='Run matching Playwright test titles only')
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output = (args.out or ROOT / '.artifacts' / f'review-{stamp}-{uuid.uuid4().hex[:6]}').resolve()
    if output.exists() and any(output.iterdir()):
        parser.error('--out must be a new or empty directory; previous evidence is preserved')
    output.mkdir(parents=True, exist_ok=True)
    env = {key: value for key, value in os.environ.items() if key not in OPTIONAL_TEST_ENV}
    result = {'status': 'failed', 'started_at': stamp, 'python': sys.version,
              'scope': 'synthetic audio and deterministic model evidence; installed wheel UI, not model accuracy',
              'selection': {'project': args.project, 'grep': args.grep},
              'coverage': 'selected_cases' if args.project or args.grep else 'full_baseline', 'commands': []}
    server = None
    server_thread = None

    def run_step(name, command, command_env=None):
        result['phase'] = name
        result['commands'].append({'phase': name, 'argv': list(map(str, command))})
        print(f'[{name}]', flush=True)
        log = output / f'{name}.log'
        with log.open('w', encoding='utf-8') as stream:
            process = subprocess.run(list(map(str, command)), cwd=ROOT, env=command_env or env,
                                     stdout=stream, stderr=subprocess.STDOUT)
        if process.returncode:
            print(log.read_text(errors='replace')[-5000:], file=sys.stderr)
            raise RuntimeError(f'{name} failed ({process.returncode}); see {log}')

    try:
        result['phase'] = 'prerequisites'
        node = shutil.which('node')
        cli = BROWSER / 'node_modules/@playwright/test/cli.js'
        if not node or not cli.is_file():
            raise RuntimeError('Node.js 20+ and npm ci --prefix tests/browser are required; see docs/testing.md')
        installed = json.loads((cli.parent / 'package.json').read_text())['version']
        required = json.loads((BROWSER / 'package.json').read_text())['devDependencies']['@playwright/test']
        if installed != required:
            raise RuntimeError('Playwright version differs from the lockfile; run npm ci --prefix tests/browser')
        result['node'] = subprocess.check_output([node, '--version'], text=True).strip()
        if int(result['node'].lstrip('v').split('.')[0]) < 20:
            raise RuntimeError('Browser acceptance requires Node.js 20+')
        result['playwright'] = installed
        result['commit'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        result['worktree_status'] = subprocess.check_output(['git', 'status', '--short'], cwd=ROOT, text=True)
        wheels = output / 'wheels'
        wheels.mkdir()
        if args.wheel:
            wheel = args.wheel.resolve()
            if not wheel.is_file() or not wheel.name.startswith('voice_tools-') or wheel.suffix != '.whl':
                raise RuntimeError('--wheel must name an existing voice-tools wheel')
            shutil.copy2(wheel, wheels / wheel.name)
        else:
            run_step('build', [sys.executable, '-m', 'pip', 'wheel', '--disable-pip-version-check',
                               '--no-index', '--no-deps', '--no-build-isolation',
                               '--wheel-dir', wheels, ROOT])
        candidates = list(wheels.glob('voice_tools-*.whl'))
        if len(candidates) != 1:
            raise RuntimeError('Expected exactly one voice-tools wheel')
        wheel = candidates[0]
        result['wheel_sha256'] = hashlib.sha256(wheel.read_bytes()).hexdigest()
        package = output / 'package'
        run_step('install', [sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check',
                             '--no-index', '--no-deps', '--target', package, wheel])
        env['PYTHONPATH'] = str(package)
        env['VT_REVIEW_PYTHON'] = sys.executable
        env['VT_REVIEW_OUTPUT'] = str(output)
        run_step('fixtures', [sys.executable, BROWSER / 'generate_fixtures.py', '--out', output / 'site',
                              '--package-root', package])
        server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(output / 'site')))
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        env['VT_REVIEW_URL'] = f'http://127.0.0.1:{server.server_port}'
        command = [node, cli, 'test', '--config', BROWSER / 'playwright.config.cjs']
        if args.project:
            command += ['--project', args.project]
        if args.grep:
            command += ['--grep', args.grep]
        run_step('browser', command)
        report = json.loads((output / 'browser-results.json').read_text())
        stats = report['stats']
        result['stats'] = stats
        result['capabilities'] = covered_capabilities(report)
        if not stats['expected'] or any(stats[key] for key in ('unexpected', 'skipped', 'flaky')):
            raise RuntimeError('Browser acceptance requires executed tests with no failures, skips or flaky passes')
        if result['coverage'] == 'full_baseline':
            expected = {project: list(REQUIRED_CAPABILITIES) for project in ('desktop', 'mobile')}
            if result['capabilities'] != expected:
                raise RuntimeError('Required R01–R08 desktop/mobile capability coverage is incomplete')
        result['status'] = 'passed'
        print(f"Passed {stats['expected']} browser cases. Evidence: {output}")
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        result['error'] = str(error)
        print(f'Acceptance failed: {error}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        result['status'] = 'interrupted'
        return 130
    finally:
        if server:
            if server_thread and server_thread.is_alive():
                server.shutdown()
                server_thread.join(timeout=5)
            server.server_close()
        report_path = output / 'browser-results.json'
        if report_path.exists() and 'stats' not in result:
            try:
                report = json.loads(report_path.read_text())
                result['stats'] = report['stats']
                result['capabilities'] = covered_capabilities(report)
            except (ValueError, KeyError):
                result['results_error'] = 'Incomplete browser JSON report; see browser.log'
        result['finished_at'] = datetime.now(timezone.utc).isoformat()
        (output / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
