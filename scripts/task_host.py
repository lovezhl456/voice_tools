#!/usr/bin/env python3
"""Small host controller: prepare once, run offline, retain detached containers."""
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import uuid

REPO = Path(__file__).resolve().parents[1]
IMAGE = os.environ.get('VT_IMAGE', 'voice-tools-executor:0.12.1')


def docker(*args, capture=False):
    return subprocess.run(['docker', *args], check=True, text=True, stdout=subprocess.PIPE if capture else None)


def main(args):
    if not args or args[0] in ('-h', '--help'):
        print('vt prepare [--platform linux/amd64|linux/arm64]\nvt task pack|check|run|status|logs|stop|collect|review|workbench ...\nrun starts a detached container and returns its run ID. Set VT_EXECUTOR_DIR / VT_MODELS_DIR for local runtime files.')
        return 0
    if args[0] == 'prepare':
        if args[1:] and (len(args) != 3 or args[1] != '--platform' or args[2] not in ('linux/amd64', 'linux/arm64')):
            raise ValueError('prepare 仅接受 --platform linux/amd64 或 linux/arm64')
        native = ['--build-arg', 'NATIVE_IMAGE='+os.environ['VT_NATIVE_IMAGE']] if os.environ.get('VT_NATIVE_IMAGE') else []
        docker('build', *args[1:], *native, '-f', str(REPO/'docker/task/Dockerfile'), '-t', IMAGE, str(REPO))
        return 0
    if len(args) < 2 or args[0] != 'task': raise ValueError('使用 vt task <action>')
    action = args[1]
    cwd = Path.cwd().resolve()
    state = cwd / '.vt' / 'runs'
    if action in ('status', 'logs', 'stop', 'collect'):
        if len(args) < 3 or not re.fullmatch('run-[a-f0-9]{16}', args[2]): raise ValueError('请提供 vt task run 返回的运行编号')
        rid = args[2]; folder = state / rid; name = 'vt-' + rid
        if not folder.is_dir(): raise ValueError('当前工作目录下没有此运行编号')
        if action == 'stop':
            docker('stop', '--time', '30', name)
            return 0
        running = docker('inspect', '--format', '{{.State.Running}}', name, capture=True).stdout.strip() == 'true'
        receipt_path = folder / 'run.json'
        if not running and receipt_path.is_file():
            receipt = json.loads(receipt_path.read_text())
            if receipt.get('status') == 'running':
                from datetime import datetime, timezone
                receipt.update(status='interrupted', finished_at=datetime.now(timezone.utc).isoformat(), reason='容器已结束，回执未正常收尾')
                for step in receipt['steps']:
                    if step['status'] == 'running': step['status'] = 'interrupted'
                    elif step['status'] == 'pending': step['status'] = 'cancelled'
                temporary = receipt_path.with_suffix('.tmp'); temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2)); temporary.replace(receipt_path)
        if action == 'logs':
            docker('logs', '--tail', '100', name)
            # Include per-step stderr tails, even while execution is still active.
            args = ['task', 'logs', str(folder)]
        else: args = ['task', action, str(folder), *args[3:]]
    # No automatic image pull during normal use.
    image_id = docker('image', 'inspect', '--format', '{{.Id}}', IMAGE, capture=True).stdout.strip()
    config = {}
    if '--profile' in args:
        index = args.index('--profile') + 1
        if index >= len(args): raise ValueError('--profile 缺少路径')
        path = Path(args[index]).resolve()
        if cwd not in path.parents: raise ValueError('executor 配置须放在当前工作目录内')
        config = json.loads(path.read_text())
    network = 'none'
    if action == 'run' and config.get('network_allowed'):
        network = os.environ.get('VT_NETWORK', 'bridge')
        if network not in ('bridge', 'host'): raise ValueError('VT_NETWORK 只能为 bridge 或 host')
        if network == 'host' and platform.system() == 'Darwin' and os.environ.get('VT_DESKTOP_HOST_NETWORK') != '1':
            raise ValueError('Mac host 网络需要 Docker Desktop 4.34+ 且已启用该功能；核对后设置 VT_DESKTOP_HOST_NETWORK=1')
    base = ['run', '--init', '--pull=never', '--network', network, '--user', f'{os.getuid()}:{os.getgid()}',
            '-e', 'HOME=/executor', '-e', 'VT_HOST_PLATFORM='+platform.system().lower(), '-e', 'VT_IMAGE_ID='+image_id,
            '-e', 'VT_CONTAINER=1', '-e', 'VT_NETWORK_MODE='+os.environ.get('VT_NETWORK', 'bridge'),
            '--mount', f'type=bind,source={cwd},target={cwd}', '-w', str(cwd)]
    for env, target in [('VT_MODELS_DIR', '/models/nisqa'), ('VT_EXECUTOR_DIR', '/executor')]:
        if os.environ.get(env):
            path = Path(os.environ[env]).expanduser().resolve()
            if not path.is_dir(): raise ValueError(env+' 必须为已有目录')
            base += ['--mount', f'type=bind,source={path},target={target},readonly']
    if action == 'run':
        if '--out' in args: raise ValueError('vt 自动管理运行目录，不接受 --out；原生 task run 支持自选目录')
        for name in config.get('secret_env', []):
            if not re.fullmatch('[A-Za-z_][A-Za-z0-9_]*', name): raise ValueError('secret_env 无效')
            base += ['--env', name]
        # Check synchronously before creating a detached run or reserving its directory.
        docker(*base, '--rm', IMAGE, '--json', 'task', 'check', *args[2:])
        rid = 'run-' + uuid.uuid4().hex[:16]
        folder = state / rid; folder.mkdir(parents=True, mode=0o700)
        base += ['-e', 'VT_RUN_ID='+rid]
        if platform.system() == 'Linux' and network == 'host': base += ['--cap-add', 'NET_RAW']
        base += ['--detach', '--name', 'vt-' + rid]
        try:
            cid = docker(*base, IMAGE, '--json', *args, '--out', str(folder), capture=True).stdout.strip()
        except BaseException:
            folder.rmdir(); raise
        print(json.dumps({'run_id':rid, 'container':cid, 'directory':str(folder), 'status':'starting'}, ensure_ascii=False))
    else:
        docker(*base, '--rm', IMAGE, '--json', *args)
    return 0


if __name__ == '__main__':
    try: sys.exit(main(sys.argv[1:]))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print('vt: '+str(error), file=sys.stderr); sys.exit(2)
