"""Discover public argument contracts without importing business implementations."""
import functools
import json
import subprocess
import sys

SETUP = {('homer', 'init'), ('homer', 'login'), ('homer', 'logout'), ('nisqa', 'download'), ('qa', 'model-download')}
EXECUTABLES = {'tshark', 'sipp', 'fs_cli'}
RUNTIME_PATHS = {'identity', 'model_dir', 'qa_model_dir', 'visqol_dir', 'latency_dir', 'ca_file'}
FILE_OUTPUTS = {('qa', 'promote'), ('qa', 'evaluate'), ('qa', 'assess-evaluate'), ('homer', 'export')}


@functools.lru_cache(maxsize=1)
def catalog():
    command = [sys.executable, '-m', 'voice_tools', 'schema']
    result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=True)
    tree = json.loads(result.stdout)['cli']['commands']
    entries = {}
    for tool, group in tree.items():
        if tool == 'task':
            continue
        for action, spec in group.get('commands', {}).items():
            if (tool, action) in SETUP:
                continue
            args = list(spec.get('arguments', []))
            if tool == 'homer':
                seen = {a['name'] for a in args}
                args += [a for a in group.get('arguments', []) if a['name'] not in seen and a['name'] != 'version']
            for arg in args:
                arg['role'] = ('input_group' if arg['name'] == 'pcap_group' else
                               'output' if arg['name'] in ('out', 'output') else
                               'runtime' if arg['name'] in RUNTIME_PATHS | EXECUTABLES or (tool == 'homer' and arg['name'] == 'config') else
                               'input' if arg['type'] == 'Path' or (tool == 'homer' and arg['name'] == 'input') else 'value')
            entries[f'{tool}.{action}'] = {'tool': tool, 'action': action, 'arguments': args,
                'mutually_exclusive_groups': spec.get('mutually_exclusive_groups', []),
                'output_kind': 'file' if (tool, action) in FILE_OUTPUTS else 'directory'}
    return entries


def capabilities(step):
    tool, action, params = step['tool'], step['action'], step.get('params', {})
    network = tool == 'capture' or (tool == 'homer' and not (action in ('schema',) or (action == 'analyze' and params.get('input'))))
    network |= tool == 'sessions' and action in ('correlate', 'homer-search', 'investigate')
    network |= tool == 'sip' and action in ('run', 'batch', 'sipp-run', 'sipp-load')
    # Offline plans supplied to business commands do not all mean no network (UUID/HOMER).
    raw = tool == 'sip' and action == 'sipp-run'
    needs = set()
    if tool in ('capture',): needs.add('ssh')
    if tool == 'sip' and action in ('run', 'batch'): needs.add('pjsua2')
    if tool == 'sip' and action in ('sipp-run', 'sipp-load'): needs.add('sipp')
    if (tool == 'sip' and action.startswith('pcap-')) or tool == 'report' or (tool == 'sessions' and action in ('index', 'export', 'investigate')):
        needs.add('tshark')
    if tool == 'audio' and action == 'prepare': needs.add('ffmpeg')
    if tool == 'nisqa': needs.add('nisqa')
    if tool == 'visqol': needs.add('visqol')
    if tool == 'latency': needs.add('latency')
    if tool == 'qa' and action == 'assess' and not params.get('rules_only'):
        needs.add('qa_model')
    if params.get('dry_run') and tool == 'sip':
        network = raw = False
        needs.difference_update({'pjsua2', 'sipp'})
    if params.get('dry_run') and tool == 'capture' and action in ('batch', 'by-number'):
        network = False
        needs.discard('ssh')
    return {'network': bool(network), 'linux_raw': raw, 'dependencies': sorted(needs)}


def argv(spec, params):
    result = ['homer', spec['action']] if spec['tool'] == 'homer' else ['--json', spec['tool'], spec['action']]
    for arg in spec['arguments']:
        name = arg['name']
        if name not in params:
            continue
        value = params[name]
        flag = arg['flags'][:1]
        if arg['type'] == 'boolean':
            if value: result += flag
            continue
        if arg['repeatable']:
            for row in value:
                result += flag + [str(x) for x in (row if isinstance(row, list) else [row])]
        else:
            result += flag + [str(x) for x in (value if isinstance(value, list) else [value])]
    return result
