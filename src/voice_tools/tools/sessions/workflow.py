"""Cross-tool orchestration through the public CLI, retaining partial artifacts."""
import os
from pathlib import Path
import signal
import subprocess
import sys

from voice_tools.core.files import new_output, write_json
from .store import epoch


def run_command(command, out, err, timeout):
    with subprocess.Popen(command, stdout=out, stderr=err, start_new_session=True) as process:
        try:
            return process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            # Stop this step's local children as well (e.g. the HOMER CLI).
            # Remote capture has its own deadline and remains recoverable.
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            except ProcessLookupError:
                pass
            raise


def investigate(job, start, end, call_id, output, audio=(), profile='1_call', nodes=(),
                skip_homer=False, include_audio=False, decode_rtp=False, timeout=600, dry_run=False, max_requests=64):
    if not epoch(start) < epoch(end) or not 30 <= timeout <= 7200 or not call_id:
        raise ValueError('需要有效窗口、Call-ID，单步骤时限为 30–7200 秒')
    if not 1 <= max_requests <= 1000:
        raise ValueError('max-requests 须为 1–1000')
    output = new_output(output); output.chmod(0o700)
    state = {'schema_version': '1.0', 'tool': 'sessions-investigation', 'call_id': call_id,
             'window': {'from': start, 'to': end}, 'steps': [], 'partial': False,
             'warning': '流程完成不代表抓包完整或故障根因已确认；保留每个工具的状态与证据。'}
    def save():
        write_json(output / 'investigation.json', state)
    def step(name, args):
        command = [sys.executable, '-m', 'voice_tools', '--json', *map(str, args)]
        record = {'name': name, 'argv': command[3:]}
        state['steps'].append(record); save()
        if dry_run:
            record.update(status='planned'); save(); return True
        stdout = output / (name + '.stdout.json'); stderr = output / (name + '.stderr.txt')
        with stdout.open('wb') as out, stderr.open('wb') as err:
            try:
                code = run_command(command, out, err, timeout)
                record.update(exit_code=code, status='complete' if code == 0 else 'partial' if code == 3 else 'failed')
            except subprocess.TimeoutExpired:
                record.update(exit_code=124, status='failed', error='步骤超时；远端任务仍受自己的时限控制，可用 job 状态/恢复命令继续')
        record.update(stdout=stdout.name, stderr=stderr.name)
        state['partial'] |= record['status'] != 'complete'; save()
        return record['status'] != 'failed'
    # Freeze media first so a slow HOMER request cannot consume its ring retention window.
    step('capture', ['capture', 'ring-fetch', '--job', job, '--from', start, '--to', end, '--out', output/'capture'])
    homer_args = ['--profile', profile, '--max-requests', max_requests]
    for node in nodes:
        homer_args += ['--node', node]
    if not skip_homer:
        step('homer-search', ['sessions', 'homer-search', '--from', start, '--to', end, '--call-id', call_id,
                              *homer_args, '--out', output/'homer-search'])
    inputs = []
    if dry_run or (output/'capture/batch.json').is_file():
        inputs += ['--batch', output/'capture']
    search_file = output/'homer-search/homer-search.json'
    if not skip_homer and (dry_run or (search_file.is_file() and search_file.stat().st_size)):
        inputs += ['--homer-json', search_file]
    if not inputs:
        state.update(partial=True, status='partial'); save(); return state
    step('index', ['sessions', 'index', *inputs, '--out', output/'index'])
    step('export', ['sessions', 'export', '--index', output/'index', '--call-id', call_id,
                     '--include-media', '--out', output/'session'])
    if not skip_homer:
        step('correlation', ['sessions', 'correlate', '--index', output/'index', '--call-id', call_id,
                             *homer_args, '--out', output/'correlation'])
    report_args = ['report', 'build', '--session-export', output/'session', '--out', output/'report']
    if not skip_homer and (dry_run or (output/'correlation/correlation.json').is_file()):
        report_args += ['--correlation', output/'correlation']
    if audio:
        report_args += ['--audio', *audio]
    if include_audio:
        report_args += ['--include-audio']
    if decode_rtp:
        report_args += ['--decode-rtp']
    step('report', report_args)
    state['status'] = 'planned' if dry_run else 'partial' if state['partial'] else 'complete'
    save(); return state
