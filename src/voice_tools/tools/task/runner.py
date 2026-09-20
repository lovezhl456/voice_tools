"""Sequential CLI execution with evidence-preserving receipts and explicit profiles."""
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid

from voice_tools import __version__
from voice_tools.core.files import new_output, read_json, sha256, write_json
from .bundle import unpack, relocate_inputs, create_archive, task_mapping
from .catalog import catalog, argv, capabilities
from .contract import ID, validate, references, no_secrets, object_fields, relative


def now(): return datetime.now(timezone.utc).isoformat()


def atomic(path, value):
    temporary = path.with_suffix('.tmp')
    write_json(temporary, value); os.replace(temporary, path)


def profile(path=None):
    data = read_json(path) if path else {'schema_version': '1.0', 'environments': {}, 'network_allowed': False}
    object_fields(data, {'schema_version', 'environments', 'network_allowed', 'model_dir', 'visqol_dir', 'secret_env'}, 'executor')
    if data.get('schema_version') != '1.0' or not isinstance(data.get('environments', {}), dict): raise ValueError('执行机配置格式无效')
    if not isinstance(data.get('network_allowed', False), bool): raise ValueError('network_allowed 须为布尔值')
    names = data.get('secret_env', [])
    import re
    if not isinstance(names, list) or any(not isinstance(n, str) or not re.fullmatch('[A-Za-z_][A-Za-z0-9_]*', n) for n in names):
        raise ValueError('secret_env 只能包含环境变量名称')
    no_secrets(data)
    for key in ('model_dir', 'visqol_dir'):
        if key in data and (not isinstance(data[key], str) or not data[key]): raise ValueError(f'{key} 须为路径字符串')
    tools = {entry['tool'] for entry in catalog().values()}
    for name, env in data.get('environments', {}).items():
        if not ID.fullmatch(name): raise ValueError('执行环境名称无效')
        object_fields(env, {'params', 'sip'}, f'environment {name}')
        if not isinstance(env.get('params', {}), dict) or not isinstance(env.get('sip', {}), dict): raise ValueError('环境参数格式无效')
        if any(tool not in tools or not isinstance(params, dict) for tool, params in env.get('params', {}).items()): raise ValueError('环境工具参数须为已知工具的对象')
    return data


def resolve(value, root, mapping, outputs):
    if isinstance(value, list):
        result = []
        for item in value:
            resolved = resolve(item, root, mapping, outputs)
            result.extend(resolved if isinstance(item, dict) and isinstance(resolved, list) else [resolved])
        return result
    if not isinstance(value, dict): return value
    if 'input' in value:
        base = root / mapping['inputs'][value['input']]
    elif 'step' in value:
        base = outputs[value['step']]
    else: raise ValueError('无效文件引用')
    suffix = value.get('path')
    if suffix:
        relative(suffix, glob=True)
        if not base.is_dir(): raise ValueError('只有目录引用可以附加 path')
        matches = sorted(base.glob(suffix))
        if not matches: raise ValueError(f'引用没有匹配产物：{suffix}')
        for match in matches:
            if base.resolve() not in match.resolve().parents and match.resolve() != base.resolve(): raise ValueError('产物引用越界')
        return [str(p) for p in matches] if any(c in suffix for c in '*?[') else str(matches[0])
    if not base.exists(): raise ValueError(f'缺少输入或前序产物：{base.name}')
    return str(base)


def parameters(step, config):
    values = dict(step.get('params', {}))
    selected = step.get('environment')
    envs = config.get('environments', {})
    if selected and selected not in envs: raise ValueError(f'缺少执行环境：{selected}')
    env = envs.get(selected or 'default', {})
    overrides = env.get('params', {}).get(step['tool'], {})
    spec = catalog()[f"{step['tool']}.{step['action']}"]
    allowed = {a['name']: a for a in spec['arguments']}
    for key, value in overrides.items():
        if key not in allowed or allowed[key]['role'] in ('input', 'input_group', 'output'):
            raise ValueError(f'执行环境不能覆盖 {step["tool"]}.{key}')
        values[key] = value
    for name in ('model_dir', 'visqol_dir'):
        if name in allowed and name in config: values[name] = config[name]
    for name, value in values.items():
        if allowed[name]['role'] == 'runtime' and (not isinstance(value, str) or not value):
            raise ValueError(f'{name} 须为执行端路径或命令名称')
    # Reuse the task's scalar/type/choice contract for executor-side overrides.
    validate({'schema_version': '1.0', 'id': 'executor-check', 'title': '执行端参数校验', 'inputs': {},
              'steps': [{'id': step['id'], 'tool': step['tool'], 'action': step['action'],
                         'params': {name: value for name, value in values.items() if allowed[name]['role'] == 'value'}}]})
    for arg in spec['arguments']:
        if arg['required'] and arg['role'] != 'output' and arg['name'] not in values:
            raise ValueError(f'{step["id"]} 缺少必填参数 {arg["name"]}')
    for group in spec.get('mutually_exclusive_groups', []):
        count = sum(key in values and values[key] is not False for key in group['arguments'])
        if count > 1 or (group['required'] and count == 0): raise ValueError(f'互斥参数应选择一项：{group["arguments"]}')
    return values, env


def check(path, config_path=None):
    config = profile(config_path)
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder) / 'package'; manifest = unpack(path, root, 'voice_task')
        task = validate(read_json(root / 'task.json'))
        mapping = task_mapping(root, task)
        relocate_inputs(root, mapping)
        issues, steps = [], []
        if manifest['tool_version'] != __version__: issues.append(f"任务版本 {manifest['tool_version']} 与执行器 {__version__} 不一致")
        for step in task['steps']:
            item = {'id': step['id'], **capabilities(step), 'issues': []}
            try:
                params, environment = parameters(step, config)
                resolved_params = {}
                for key, value in params.items():
                    if any('step' in ref for ref in references(value)): continue
                    if list(references(value)):
                        resolved = resolve(value, root, mapping, {})
                        resolved_params[key] = resolved
                        # SIPp load packages with generated RTP DTMF use raw PCAP replay.
                        if step['tool'] == 'sip' and step['action'] == 'sipp-load' and key == 'package' and not params.get('dry_run'):
                            load = Path(resolved) / 'load.json'
                            if load.is_file() and any(s.get('action') == 'dtmf' and s.get('method', 'rfc4733') == 'rfc4733' for s in read_json(load).get('scenario', {}).get('steps', [])):
                                item['linux_raw'] = True
                    else: resolved_params[key] = value
                if step['tool'] == 'sip' and step['action'] in ('run', 'validate') and 'scenario' in resolved_params:
                    params_for_validation = sip_environment(resolved_params, step, environment, Path(folder))
                    checked = subprocess.run([sys.executable, '-m', 'voice_tools', '--json', 'sip', 'validate', str(params_for_validation['scenario'])], capture_output=True, text=True, timeout=30)
                    if checked.returncode: item['issues'].append('SIP 场景离线校验失败：' + checked.stdout[-1500:])
                    if step['action'] == 'run' and not params.get('dry_run'):
                        scenario = read_json(params_for_validation['scenario'])
                        detector = scenario.get('benchmark', {}).get('detector', {})
                        if scenario.get('benchmark') and detector.get('backend', 'webrtcvad') == 'webrtcvad' and importlib.util.find_spec('webrtcvad') is None:
                            item['issues'].append('中文时序评测缺少可选依赖 webrtcvad')
                if step['tool'] == 'sip' and step['action'] == 'batch' and 'queue' in resolved_params:
                    from voice_tools.tools.sip.batch import load_queue
                    params_for_validation = sip_environment(resolved_params, step, environment, Path(folder))
                    queue_plan = load_queue(params_for_validation['queue'])
                    needs_vad = any(job['scenario'].get('benchmark') and job['scenario']['benchmark'].get('detector', {}).get('backend', 'webrtcvad') == 'webrtcvad' for job in queue_plan['jobs'])
                    if not params.get('dry_run') and needs_vad and importlib.util.find_spec('webrtcvad') is None:
                        item['issues'].append('中文时序批量评测缺少可选依赖 webrtcvad')
                if item['network'] and not config.get('network_allowed', False): item['issues'].append('执行机配置未启用业务网络')
                if item['linux_raw'] and os.environ.get('VT_HOST_PLATFORM', platform.system().lower()) != 'linux':
                    item['issues'].append('原始包发送须在 Linux 执行')
                if item['network'] and step['tool'] == 'sip' and os.environ.get('VT_CONTAINER') == '1' and os.environ.get('VT_NETWORK_MODE') != 'host':
                    item['issues'].append('容器内 SIP 任务需要 VT_NETWORK=host 及可达的 SIP/RTP 地址')
                for name in item['dependencies']:
                    if name == 'pjsua2':
                        if importlib.util.find_spec('pjsua2') is None: item['issues'].append('缺少 PJSUA2')
                    elif name in ('nisqa', 'visqol'):
                        args = [sys.executable, '-m', 'voice_tools', '--json', name, 'doctor']
                        directory = params.get('model_dir' if name == 'nisqa' else 'visqol_dir')
                        if directory: args += ['--model-dir' if name == 'nisqa' else '--visqol-dir', directory]
                        result = subprocess.run(args, capture_output=True, timeout=30)
                        if result.returncode: item['issues'].append(f'{name} 环境／模型未就绪')
                    elif not shutil.which(params.get(name, name)): item['issues'].append(f'缺少 {name}')
            except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
                item['issues'].append(str(error))
            issues += [step['id'] + ': ' + text for text in item['issues']]
            steps.append(item)
        for name in config.get('secret_env', []):
            if not os.environ.get(name): issues.append(f'缺少执行端环境变量 {name}')
        return {'ready': not issues, 'issues': issues, 'steps': steps, 'task_id': task['id'], 'network_accessed': False,
                'input_bytes': sum(row['bytes'] for row in manifest['files']),
                'tool_version': __version__, 'package_sha256': sha256(path)}


def merge(a, b):
    result = dict(a)
    for key, value in b.items(): result[key] = merge(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else value
    return result


def sip_environment(params, step, env, root):
    if step['tool'] != 'sip' or not env.get('sip'): return params
    key = 'queue' if step['action'] == 'batch' else 'scenario' if step['action'] in ('run', 'validate') else None
    if not key: raise ValueError('该 SIP 操作不支持 scenario 环境覆盖；请使用其命令参数')
    allowed = {'target_uri', 'account', 'network', 'codec', 'connect_timeout_s', 'max_call_s', 'record_early'}
    if set(env['sip']) - allowed: raise ValueError('SIP 环境不能改写动作或断言')
    path = Path(params[key]); data = read_json(path)
    def update(scenario):
        scenario = merge(scenario, env['sip'])
        for action in scenario.get('steps', []):
            if action.get('action') in ('play', 'play_media'):
                action['file'] = str((path.parent / action['file']).resolve())
        return scenario
    if key == 'queue':
        data = dict(data); data['jobs'] = [{**job, 'scenario': update(job['scenario'])} for job in data['jobs']]
    else: data = update(data)
    target = root / 'effective-input.json'; write_json(target, data)
    return {**params, key: str(target)}


def classify(tool, action, code, payload):
    if code < 0: return 'interrupted'
    if code == 0:
        if tool == 'capture' and action == 'ring-start': return 'remote_running'
        return 'completed'
    if code == 1 and tool == 'qa': return 'findings'
    if code == 1 and tool == 'benchmark' and action in ('analyze', 'summarize'):
        # A crashed subprocess may also exit 1; only a valid findings receipt permits dependants to run.
        if (isinstance(payload, dict) and payload.get('ok') is True
                and payload.get('status') == 'findings' and payload.get('exit_code') == code):
            return 'findings'
        return 'failed'
    if code == 1 and tool == 'nisqa': return 'insufficient_evidence'
    if code == 6 and tool == 'homer': return 'partial'
    if code == 3 and tool == 'sip':
        summary = (payload or {}).get('summary', {})
        if summary.get('execution_status') == 'completed':
            status = summary.get('assertions', {}).get('status')
            if status == 'failed': return 'findings'
            if status == 'insufficient_evidence': return 'insufficient_evidence'
        if summary.get('status') in ('assertion_failed', 'assertions_failed'): return 'findings'
        if summary.get('status') == 'insufficient_evidence': return 'insufficient_evidence'
    if code == 3 and tool not in ('sip', 'homer'): return 'partial'
    return 'failed'


def stop(process):
    if process.poll() is None:
        # KeyboardInterrupt lets existing CLI finally blocks stop their own sessions.
        os.killpg(process.pid, signal.SIGINT)
        try: process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try: process.wait(timeout=2)
            except subprocess.TimeoutExpired: os.killpg(process.pid, signal.SIGKILL); process.wait()


def redact_logs(folder, config):
    """Never persist declared secret environment values in process logs."""
    names = set(config.get('secret_env', []))
    names.update(k for k in os.environ if any(part in k.upper() for part in ('PASSWORD', 'TOKEN', 'SECRET')))
    secrets = sorted({os.environ[n] for n in names if os.environ.get(n)}, key=len, reverse=True)
    for name in ('stdout.json', 'stderr.log'):
        path = folder / name
        if not path.exists() or not secrets: continue
        # Stream by lines; subprocess logs can be much larger than the preview.
        target = path.with_suffix('.redacted')
        with path.open(errors='replace') as source, target.open('w') as output:
            for line in source:
                for secret in secrets: line = line.replace(secret, '[REDACTED]')
                output.write(line)
        os.replace(target, path)


def run(path, output, config_path=None):
    preflight = check(path, config_path)
    if not preflight['ready']: raise ValueError('执行前检查未通过：' + '；'.join(preflight['issues']))
    config = profile(config_path); output = new_output(output).resolve()
    output.chmod(0o700)
    expanded = preflight['input_bytes']
    if shutil.disk_usage(output).free < expanded * 2 + Path(path).stat().st_size + 64 * 1024**2:
        raise ValueError('磁盘空间不足以同时保留原始输入与执行工作副本')
    shutil.copyfile(path, output / 'task.vtask.zip')
    unpack(path, output / 'original', 'voice_task')
    shutil.copytree(output / 'original', output / 'work')
    root = output / 'work'; task = validate(read_json(root / 'task.json')); mapping = task_mapping(root, task)
    write_json(output / 'path-map.json', relocate_inputs(root, mapping))
    receipt = {'schema_version': '1.0', 'kind': 'task_run', 'run_id': os.environ.get('VT_RUN_ID') or 'run-' + uuid.uuid4().hex[:16],
        'task_id': task['id'], 'title': task['title'], 'tool_version': __version__, 'status': 'running', 'started_at': now(),
        'execution_directory': str(output),
        'package_sha256': preflight['package_sha256'], 'environment': {'python': sys.version.split()[0], 'machine': platform.machine(),
            'system': platform.system(), 'host_platform': os.environ.get('VT_HOST_PLATFORM', platform.system().lower()),
            'image': os.environ.get('VT_IMAGE_ID'), 'secret_env_names': config.get('secret_env', [])},
        'steps': [{k: step[k] for k in ('id', 'tool', 'action')} | {'status': 'pending'} for step in task['steps']]}
    write_json(output / 'preflight.json', preflight)
    write_json(output / 'executor-summary.json', config)
    outputs = {}; statuses = {}; current = None
    old = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    def save(): atomic(output / 'run.json', receipt)
    save()
    try:
        for step, row in zip(task['steps'], receipt['steps']):
            deps = set(step.get('depends_on', [])) | {ref['step'] for ref in references(step.get('params', {})) if 'step' in ref}
            if any(statuses.get(d) in ('failed', 'interrupted', 'skipped') for d in deps):
                row.update(status='skipped', reason='前序依赖失败'); statuses[step['id']] = 'skipped'; save(); continue
            folder = output / 'steps' / step['id']; folder.mkdir(parents=True)
            outputs[step['id']] = folder
            row.update(status='running', started_at=now(), directory=str(folder.relative_to(output)))
            save()
            try:
                spec = catalog()[f"{step['tool']}.{step['action']}"]
                params, env = parameters(step, config)
                params = {key: resolve(value, root, mapping, outputs) if list(references(value)) else value for key, value in params.items()}
                params = sip_environment(params, step, env, folder)
                if step['tool'] == 'sip' and step['action'] == 'sipp-load' and not params.get('dry_run'):
                    load = read_json(Path(params['package']) / 'load.json')
                    raw = any(s.get('method', 'rfc4733') == 'rfc4733' and s.get('action') == 'dtmf' for s in load.get('scenario', {}).get('steps', []))
                    if raw and os.environ.get('VT_HOST_PLATFORM', platform.system().lower()) != 'linux':
                        raise ValueError('此压力包需要原始包发送，须在 Linux 执行')
                for arg in spec['arguments']:
                    if arg['role'] == 'output':
                        params[arg['name']] = str(folder / ('output.json' if spec['output_kind'] == 'file' else 'data'))
                # Files selected by scalar options must resolve to exactly one path.
                for arg in spec['arguments']:
                    value = params.get(arg['name'])
                    if arg['role'] == 'input' and arg['nargs'] is None and not arg['repeatable'] and isinstance(value, list):
                        if len(value) != 1: raise ValueError(f'{arg["name"]} 必须且只能匹配一个文件')
                        params[arg['name']] = value[0]
                command = [sys.executable, '-m', 'voice_tools', *argv(spec, params)]
                write_json(folder / 'invocation.json', {'tool': step['tool'], 'action': step['action'], 'params': params})
                with (folder / 'stdout.json').open('wb') as stdout, (folder / 'stderr.log').open('wb') as stderr:
                    timed_out = False
                    current = subprocess.Popen(command, stdout=stdout, stderr=stderr, start_new_session=True)
                    try: code = current.wait(timeout=step.get('timeout_s', 3600))
                    except subprocess.TimeoutExpired:
                        stop(current); code = current.returncode; timed_out = True; row['reason'] = '超过步骤时限，已停止本次子进程并保留证据'
                current = None
                redact_logs(folder, config)
                try: payload = read_json(folder / 'stdout.json')
                except (ValueError, OSError): payload = None
                row.update(exit_code=code, status=classify(step['tool'], step['action'], code, payload))
                if timed_out: row['status'] = 'interrupted'
                if isinstance(payload, dict) and payload.get('error'):
                    row['reason'] = payload['error'].get('message', '工具返回错误')
                if payload is None and code == 0: row.update(status='failed', reason='缺少有效机器回执')
                if row['status'] == 'remote_running': row['reason'] = '远端环形任务仍按既定时限运行；需后续取回／停止步骤'
                if any(statuses.get(d) in ('partial', 'insufficient_evidence') for d in deps): row['input_evidence_partial'] = True
            except (ValueError, OSError, subprocess.SubprocessError) as error:
                row.update(status='failed', reason=str(error))
            row['finished_at'] = now(); statuses[step['id']] = row['status']; save()
    except KeyboardInterrupt:
        if current: stop(current)
        if 'folder' in locals(): redact_logs(folder, config)
        for row in receipt['steps']:
            if row['status'] == 'running': row.update(status='interrupted', reason='执行已中断，保留已生成证据')
            elif row['status'] == 'pending': row.update(status='cancelled')
        receipt['status'] = 'interrupted'
    finally:
        signal.signal(signal.SIGTERM, old)
        if receipt['status'] != 'interrupted':
            states = {row['status'] for row in receipt['steps']}
            receipt['status'] = ('failed' if states & {'failed', 'skipped', 'interrupted'} else
                                 'partial' if states & {'partial', 'remote_running', 'insufficient_evidence'} else
                                 'findings' if 'findings' in states else 'completed')
        receipt['finished_at'] = now(); save()
    return receipt


def collect(run_dir, output):
    run_dir = Path(run_dir).resolve(); receipt = read_json(run_dir / 'run.json')
    if receipt.get('kind') != 'task_run' or receipt.get('status') == 'running': raise ValueError('运行尚未结束或缺少有效回执')
    # Original inputs and every generated artifact are included; execution-machine profiles are not.
    entries = {}
    for path in run_dir.rglob('*'):
        if path.is_symlink(): raise ValueError('结果目录中有符号链接，拒绝带回未声明的文件')
        if path.is_file() and path.name not in ('executor-summary.json',):
            entries[path.relative_to(run_dir).as_posix()] = path
    return create_archive(output, entries, 'voice_result', {'task_id': receipt['task_id'], 'run_id': receipt['run_id'], 'status': receipt['status']})
