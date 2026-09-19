"""Detached, stdlib-only server-side number capture supervisor."""
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys

from voice_tools.core.files import read_json
from voice_tools.core.capture_contract import selectors, publish
from .remote import Agent


def check(config):
    if sys.version_info < (3, 9):
        raise ValueError('远端需要 Python 3.9 或更新版本')
    missing = [name for name in ('dumpcap', 'tshark', 'mergecap') if not shutil.which(name)]
    if missing:
        raise ValueError('远端缺少工具：' + ', '.join(missing))
    selectors(config['selection']['callers'], config['selection']['callees'])


def main():
    action, directory = sys.argv[1:]
    root = Path(directory)
    config = read_json(root / 'config.json')
    os.umask(0o077)
    try:
        check(config)
        if action == 'check':
            return
        if action != 'run':
            raise ValueError('Unknown number capture action')
        publish(root, config, status='capturing')
        Agent(root, config).run()
        # Packet analysis only needs the SSH user's stopped, owned capture files.
        if os.geteuid() == 0 and config['uid'] != 0:
            os.setgroups([])
            os.setgid(config['gid'])
            os.setuid(config['uid'])
        # A separate process group lets the deadline stop tshark/mergecap as well.
        child = subprocess.Popen([sys.executable, '-m', 'voice_tools.tools.sessions.number_bundle', str(root)], start_new_session=True)
        try:
            code = child.wait(timeout=config['processing_seconds'])
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL); child.wait()
            raise ValueError('远端分析超时，原始抓包和任务目录保留') from None
        if code:
            if read_json(root / 'number-status.json').get('status') != 'failed':
                raise ValueError('远端拆包失败，原始抓包保留；查看 number-status.json')
        else:
            receipt = read_json(root / 'number-status.json')
            if receipt.get('status') not in ('complete', 'partial') or not receipt.get('archive'):
                raise ValueError('拆包进程退出但没有有效最终回执，原始抓包保留')
    except Exception as error:
        publish(root, config, status='failed', error=str(error)[:1000])
        if action == 'check':
            raise


if __name__ == '__main__':
    main()
