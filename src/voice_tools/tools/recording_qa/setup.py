"""Explicit setup for automatic QA; analysis never invokes this module."""
import json
from pathlib import Path
import subprocess
import sys

from . import DEFAULT_MODEL_DIRECTORY

# Keep the base NumPy constraint when pip resolves the optional runtime together.
RUNTIME_REQUIREMENTS = ('numpy>=1.24,<3', 'onnxruntime>=1.18,<2', 'scipy>=1.10,<2')


def select_component(component, json_output=False):
    if component is not None:
        return component
    if json_output or not sys.stdin.isatty():
        raise ValueError('非交互／JSON 模式请指定 --component all、runtime 或 model')
    print(f'安装到当前 Python：{sys.executable}\n'
          '1. 全部：CPU 运行库 + Silero 模型\n'
          '2. 仅 CPU 运行库（NumPy、ONNX Runtime、SciPy）\n'
          '3. 仅 Silero 模型（约 2.3 MB，MIT）\n'
          '0. 取消', file=sys.stderr)
    choices = {'1': 'all', '2': 'runtime', '3': 'model', '0': None}
    while True:
        print('请选择 [0–3]：', end='', file=sys.stderr, flush=True)
        try:
            answer = input().strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if answer in choices:
            return choices[answer]
        print('请输入 0、1、2 或 3。', file=sys.stderr)


def runtime_status():
    """Run only in a fresh interpreter, after pip may have replaced NumPy."""
    try:
        import numpy
        import onnxruntime
        import scipy
        from scipy.signal import resample_poly  # noqa: F401 -- test the actual import used by inference
        if 'CPUExecutionProvider' not in onnxruntime.get_available_providers():
            raise ValueError('ONNX Runtime 缺少 CPUExecutionProvider')
        return {'ready': True, 'versions': {'numpy': numpy.__version__,
                'onnxruntime': onnxruntime.__version__, 'scipy': scipy.__version__}}
    except (ImportError, OSError, ValueError) as error:
        return {'ready': False, 'issues': [str(error)]}


def read_process_json(command, timeout):
    process = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if process.stderr:
        print(process.stderr, end='', file=sys.stderr)
    try:
        value = json.loads(process.stdout)
    except ValueError as error:
        raise ValueError(f'检查子进程未返回 JSON（退出码 {process.returncode}）') from error
    if process.returncode not in (0, 1):
        raise ValueError(value.get('error', {}).get('message', f'子进程退出码 {process.returncode}'))
    return value


def install_runtime():
    command = [sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check',
               '--no-input', '--only-binary=:all:', *RUNTIME_REQUIREMENTS]
    print('安装 CPU 运行库；pip 日志输出到 stderr。', file=sys.stderr)
    # Inherit stderr so long downloads show progress without polluting JSON stdout.
    process = subprocess.run(command, stdout=sys.stderr, stderr=sys.stderr, timeout=900)
    if process.returncode:
        raise ValueError(f'pip 安装失败（退出码 {process.returncode}）；请检查 stderr，修复后重试')
    code = ('import json; from voice_tools.tools.recording_qa.setup import runtime_status; '
            'print(json.dumps(runtime_status()))')
    result = read_process_json([sys.executable, '-c', code], timeout=60)
    if not result['ready']:
        raise ValueError('运行库检查失败：' + '; '.join(result['issues']))
    return result


def qa_command(action, directory_flag, directory):
    return [sys.executable, '-m', 'voice_tools', '--json', 'qa', action,
            directory_flag, str(directory)]


def install(component, directory=None):
    if component not in ('all', 'runtime', 'model'):
        raise ValueError('安装组件须为 all、runtime 或 model')
    directory = (Path(directory) if directory else DEFAULT_MODEL_DIRECTORY).expanduser().resolve()
    selected = ['runtime', 'model'] if component == 'all' else [component]
    result = {'selected': selected, 'python': sys.executable, 'model_directory': str(directory),
              'components': {}, 'completed': False, 'ready': False}
    print(f'Python：{sys.executable}\n模型目录：{directory}', file=sys.stderr)
    for name in selected:
        try:
            if name == 'runtime':
                details = install_runtime()
            else:
                print('下载或校验固定版本 Silero 模型。', file=sys.stderr)
                details = read_process_json(qa_command('model-download', '--out', directory), 120)['summary']
            result['components'][name] = {'status': 'completed', **details}
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            # A completed component remains reusable if another component fails.
            result['components'][name] = {'status': 'failed', 'error': str(error)}
    try:
        check = read_process_json(qa_command('model-doctor', '--model-dir', directory), 60)['summary']
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        check = {'ready': False, 'issues': [str(error)], 'network_accessed': False}
    result['check'] = check
    result['ready'] = check['ready']
    components_ok = all(item['status'] == 'completed' for item in result['components'].values())
    result['completed'] = components_ok and (component != 'all' or result['ready'])
    return result


def setup_message(result):
    if result['completed'] and result['ready']:
        return '所选组件安装完成；整通自动质检已就绪。'
    messages = ['所选组件安装完成；整通自动质检尚未就绪。' if result['completed'] else '安装或检查未完成；可保留成功组件，修复后重试。']
    messages.extend(f"{name}: {item['error']}" for name, item in result['components'].items() if item['status'] == 'failed')
    messages.extend(result['check'].get('issues', []))
    return '\n'.join(messages)
