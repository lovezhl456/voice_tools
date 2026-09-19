"""显式同采集点的 PCAP 连续输入；不去重、不把不同采集点拼成一个流。"""
from pathlib import Path
import shutil
import subprocess

from .files import sha256

MAX_GROUP_BYTES = 2 * 1024 * 1024 * 1024


def merge_packets(paths, target, mergecap='mergecap'):
    paths = list(dict.fromkeys(Path(p).resolve() for p in paths))
    if not paths or len(paths) > 2048:
        raise ValueError('连续分片组须包含 1–2048 份 PCAP')
    if sum(p.stat().st_size for p in paths) > MAX_GROUP_BYTES:
        raise ValueError('连续分片组超过 2 GiB，请缩短选取窗口')
    executable = shutil.which(mergecap)
    if not executable:
        raise ValueError('跨文件重组需要本机 mergecap（Wireshark 工具集）')
    sources = [{'path': str(p), 'sha256': sha256(p), 'bytes': p.stat().st_size} for p in paths]
    target = Path(target)
    try:
        cp = subprocess.run([executable, '-F', 'pcapng', '-I', 'none', '-w', str(target), *map(str, paths)],
                            capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired as error:
        raise ValueError('mergecap 超过 180 秒，请缩小分片组') from error
    if cp.returncode:
        target.unlink(missing_ok=True)
        raise ValueError('连续分片重组失败：' + cp.stderr[-2000:])
    for row in sources:
        if sha256(row['path']) != row['sha256']:
            target.unlink(missing_ok=True)
            raise ValueError('重组期间源文件发生变化')
    target.chmod(0o600)
    return sources


def parse_groups(groups):
    result = []
    names = set()
    assigned = set()
    for group in groups:
        if len(group) < 2 or not group[0].strip() or group[0] in names:
            raise ValueError('--pcap-group 需要唯一采集点名称和至少一份 PCAP')
        names.add(group[0])
        paths = list(dict.fromkeys(Path(p).resolve() for p in group[1:]))
        if assigned.intersection(paths):
            raise ValueError('同一 PCAP 不能属于多个采集点组')
        assigned.update(paths)
        result.append((group[0], paths))
    return result
