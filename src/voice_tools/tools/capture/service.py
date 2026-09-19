"""仅从验证过的端点生成 BPF；SSH 与远端 shell 分别引用参数。"""
from datetime import datetime, timezone
import ipaddress
from pathlib import Path
import re
import shlex
import subprocess
import time
from urllib.parse import unquote
from uuid import UUID

from voice_tools import __version__
from voice_tools.core.files import new_output, sha256, write_json


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def port(value):
    if isinstance(value, bool) or not str(value).isdigit() or not 1 <= int(value) <= 65535:
        raise ValueError(f"无效端口：{value}")
    return int(value)


def flow(local_ip, local_port, remote_ip, remote_port, **extra):
    a, b = ipaddress.ip_address(local_ip), ipaddress.ip_address(remote_ip)
    if a.version != b.version or a.is_unspecified or b.is_unspecified or '%' in str(a) + str(b):
        raise ValueError("媒体 IP 须为同一地址族的具体地址，不支持通配地址或 IPv6 zone")
    return dict(local_ip=str(a), local_port=port(local_port), remote_ip=str(b),
                remote_port=port(remote_port), **extra)


def bpf_for(flows):
    if not flows:
        raise ValueError("未获得媒体端点，拒绝生成宽泛抓包过滤器")
    expressions = []
    for raw in flows:
        f = flow(**raw)
        a, p, b, q = (f[k] for k in ("local_ip", "local_port", "remote_ip", "remote_port"))
        expressions.append(f"((src host {a} and src port {p} and dst host {b} and dst port {q}) "
                           f"or (src host {b} and src port {q} and dst host {a} and dst port {p}))")
    exact = "udp and (" + " or ".join(dict.fromkeys(expressions)) + ")"
    fragments = []
    for raw in flows:
        f = flow(**raw)
        a, b = f['local_ip'], f['remote_ip']
        pair = f"((src host {a} and dst host {b}) or (src host {b} and dst host {a}))"
        fragments.append(f"({pair} and {fragment_bpf()})")
    return "(" + exact + ") or (" + " or ".join(dict.fromkeys(fragments)) + ")"


def fragment_bpf():
    # Noninitial fragments have no ports. IPv6 extension chains cannot reliably
    # use libpcap port primitives: admit scoped TCP/UDP and filter after reassembly.
    return "((ip and (ip proto 6 or ip proto 17) and (ip[6:2] & 0x1fff != 0)) or (ip6 protochain 6 or ip6 protochain 17))"


class SSH:
    def __init__(self, host, ssh_port=22, identity=None):
        # Host aliases and user@host accepted. No options, shell syntax, or scp paths.
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@-]*", host):
            raise ValueError("host 须为 SSH 配置别名或 user@hostname；IPv6 请使用 SSH 别名")
        self.host, self.port = host, port(ssh_port)
        self.options = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                        "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=5",
                        "-o", "ServerAliveCountMax=3"]
        if identity:
            self.options += ["-i", str(Path(identity).expanduser().resolve())]

    def run(self, argv, timeout=30):
        command = ["ssh", *self.options, "-p", str(self.port), "-T", self.host, shlex.join(argv)]
        try:
            return subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as error:
            raise ValueError("SSH 操作超时；远端抓包由独立 timeout 限时，可用 capture fetch 取回") from error

    def checked(self, argv, timeout=30):
        result = self.run(argv, timeout)
        if result.returncode:
            raise ValueError(f"远端命令失败 ({result.returncode})：{result.stderr[-2000:].strip()}")
        return result.stdout.strip()

    def copy(self, remote, local):
        try:
            result = subprocess.run(["scp", *self.options, "-P", str(self.port),
                                     f"{self.host}:{remote}", str(local)],
                                    capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired as error:
            raise ValueError("SCP 超时，远端文件保留，可用 capture fetch 重试") from error
        if result.returncode:
            raise ValueError(f"SCP 失败：{result.stderr[-2000:].strip()}")


def dump_vars(ssh, fs_cli, uuid):
    uuid = str(UUID(uuid))
    value = ssh.checked([fs_cli, "-x", f"uuid_dump {uuid}"])
    if not value or value.startswith("-ERR"):
        raise ValueError(f"通话 {uuid} 不存在、已结束或 fs_cli 查询失败")
    result = {}
    for line in value.splitlines():
        key, sep, val = line.partition(":")
        if sep:
            result[key.strip().removeprefix("variable_")] = unquote(val.strip())
    return result


def discover(ssh, uuid, fs_cli="fs_cli", include_peer=True):
    uuid = str(UUID(uuid))
    primary = dump_vars(ssh, fs_cli, uuid)
    legs = [(uuid, primary)]
    warnings = []
    peer = next((primary.get(k) for k in ("bridge_uuid", "signal_bond", "Other-Leg-Unique-ID")
                 if primary.get(k) and primary[k] != uuid), None)
    if include_peer and peer:
        try:
            legs.append((str(UUID(peer)), dump_vars(ssh, fs_cli, peer)))
        except ValueError as error:
            warnings.append(f"桥接腿查询失败，未纳入抓包：{error}")
    flows = []
    for leg_id, values in legs:
        try:
            flows.append(flow(values.get("local_media_ip", ""), values.get("local_media_port", ""),
                              values.get("remote_media_ip", ""), values.get("remote_media_port", ""),
                              leg_uuid=leg_id))
        except ValueError as error:
            if leg_id == uuid:
                raise ValueError(f"主通话没有有效媒体端点（可能尚未接通或 bypass media）：{error}") from error
            warnings.append(f"桥接腿 {leg_id} 无有效媒体端点，未纳入：{error}")
    return flows, warnings


def validate_capture(seconds, max_mib, snaplen, interface):
    if not 1 <= seconds <= 3600 or not 1 <= max_mib <= 512 or not 64 <= snaplen <= 65535:
        raise ValueError("时长须为 1–3600 秒，大小为 1–512 MiB，snaplen 为 64–65535")
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.:@-]{0,63}", interface):
        raise ValueError("无效抓包接口名称")
    return (max_mib * 1024 * 1024 - 24) // (snaplen + 16)


def capture_argv(remote_dir, user, bpf, interface, seconds, packet_limit, snaplen, sudo):
    argv = ["timeout", "--signal=INT", "--kill-after=5s", f"{seconds}s", "tcpdump",
            "-n", "-U", "-i", interface, "-s", str(snaplen), "-c", str(packet_limit),
            "-Z", user, "-w", remote_dir + "/session.pcap", bpf]
    return (["sudo", "-n"] if sudo else []) + argv


def dumpcap_argv(remote_dir, bpf, interface, seconds, max_mib, snaplen, sudo=False):
    # dumpcap uses decimal kB; reserve one maximum-sized record for its boundary.
    kilobytes = (max_mib * 1048576 - snaplen - 512) // 1000
    argv = ['timeout', '--signal=INT', '--kill-after=5s', f'{seconds + 5}s',
            'dumpcap', '-q', '-P', '-i', interface, '-f', bpf, '-s', str(snaplen),
            '-a', f'duration:{seconds}', '-a', f'filesize:{kilobytes}',
            '-w', remote_dir + '/session.pcap']
    return (['sudo', '-n'] if sudo else []) + argv


def remote_directory(value):
    if not re.fullmatch(r"/tmp/voice-tools-[A-Za-z0-9]{6,32}", value):
        raise ValueError("远端目录须为本工具创建的 /tmp/voice-tools-<随机字符>")
    return value


def retrieve(ssh, remote_dir, output, filename="session.pcap"):
    remote_directory(remote_dir)
    if not re.fullmatch(r"(?:session|part-[0-9]{8}T[0-9]{6})\.pcap", filename):
        raise ValueError("不支持的抓包文件名")
    remote = remote_dir + "/" + filename
    before = ssh.checked(["sha256sum", remote]).split()[0]
    if not re.fullmatch(r"[0-9a-f]{64}", before):
        raise ValueError("远端 SHA-256 无效")
    temp = output / (filename + ".part")
    ssh.copy(remote, temp)
    after = ssh.checked(["sha256sum", remote]).split()[0]
    if sha256(temp) != before or before != after:
        raise ValueError("PCAP 校验不一致，远端可能仍在写入；保留 .part 文件，稍后 fetch")
    with temp.open("rb") as stream:
        header = stream.read(24)
    if len(header) < 24 or header[:4] not in (b"\xd4\xc3\xb2\xa1", b"\xa1\xb2\xc3\xd4", b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d"):
        raise ValueError("取回的文件不是有效的经典 PCAP 文件头；保留 .part 供检查")
    from .remote import bounds
    details = bounds(temp)
    target = output / filename
    temp.replace(target)
    target.chmod(0o600)
    return {"file": target.name, "sha256": before, "bytes": target.stat().st_size,
            "has_packet_data": details['packets'] > 0, 'packets': details['packets'],
            'truncated_packets': details['truncated_packets']}


def start(ssh, output, *, uuid=None, manual_flows=None, fs_cli="fs_cli", include_peer=True,
          interface="any", seconds=60, max_mib=64, snaplen=2048, sudo=False, dry_run=False, backend='tcpdump'):
    if backend not in ('tcpdump', 'dumpcap'):
        raise ValueError('backend 须为 dumpcap 或 tcpdump')
    limit = validate_capture(seconds, max_mib, snaplen, interface)
    warnings = ["端点为查询时的快照；不跟随 re-INVITE、NAT 重绑定或新桥接腿。",
                "不自动包含 SIP、独立 RTCP 端口或其他主机。",
                "为重组保留受限地址间的后续 IPv4 分片及 IPv6 TCP/UDP；这些包在抓取时无法严格按端口筛选。"]
    if uuid:
        flows, extra = discover(ssh, uuid, fs_cli, include_peer)
        warnings.extend(extra)
    else:
        flows = [flow(**item) for item in (manual_flows or [])]
    bpf = bpf_for(flows)
    output = new_output(output)
    output.chmod(0o700)
    manifest = {"schema_version": "1.0", "tool": "capture", "tool_version": __version__,
                "created_at": utc_now(), "host": ssh.host, "ssh_port": ssh.port,
                "flows": flows, "bpf": bpf, "interface": interface, "seconds": seconds,
                "max_mib": max_mib, "snaplen": snaplen, "packet_limit": limit,
                "backend": backend, "size_policy": 'actual_file_bytes' if backend == 'dumpcap' else 'worst_case_packet_count',
                "warnings": warnings, "status": "planned"}
    manifest_path = output / "capture.json"
    write_json(manifest_path, manifest)
    if dry_run:
        return manifest
    try:
        # Private directory and user-owned output let tcpdump drop privilege with -Z.
        prepared = ssh.checked(["sh", "-c", 'set -eu; umask 077; '
                                'd=$(mktemp -d /tmp/voice-tools-XXXXXXXXXXXX); '
                                'touch "$d/session.pcap"; printf "%s\\n%s\\n" "$d" "$(id -un)"'])
        remote_dir, user = prepared.splitlines()
        remote_directory(remote_dir)
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", user):
            raise ValueError("远端用户名格式不受支持")
        manifest.update(remote_dir=remote_dir, status="capturing", started_at=utc_now())
        write_json(manifest_path, manifest)
        started = time.monotonic()
        argv = (dumpcap_argv(remote_dir, bpf, interface, seconds, max_mib, snaplen, sudo) if backend == 'dumpcap'
                else capture_argv(remote_dir, user, bpf, interface, seconds, limit, snaplen, sudo))
        result = ssh.run(argv,
                         timeout=seconds + 40)
        elapsed = time.monotonic() - started
        (output / "tcpdump.log").write_text(result.stderr, encoding="utf-8")
        manifest.update(capture_exit_code=result.returncode, finished_at=utc_now())
        manifest["tcpdump_stats"] = {key: int(match.group(1)) if match else None
                                     for key, pattern in (("captured", r"(\d+) packets? captured"),
                                                          ("received_by_filter", r"(\d+) packets? received by filter"),
                                                          ("dropped_by_kernel", r"(\d+) packets? dropped by kernel"))
                                     for match in [re.search(pattern, result.stderr)]}
        if manifest["tcpdump_stats"]["dropped_by_kernel"]:
            warnings.append("tcpdump 报告抓包点内核丢弃；PCAP 序号缺口不能全部归因于网络。")
        manifest["pcap"] = retrieve(ssh, remote_dir, output)
        manifest["status"] = ("complete" if result.returncode == 124 and manifest["pcap"]["has_packet_data"]
                              else "no_packets" if result.returncode in (0, 124) else "partial")
        if result.returncode == 0:
            if manifest["pcap"]["has_packet_data"]:
                manifest["status"] = "partial"
            warnings.append("tcpdump 达到包数上限或提前退出；抓包时长可能短于请求时长。")
        if backend == 'dumpcap':
            from .remote import capture_statistics
            manifest['capture_health'] = capture_statistics(result.stderr)
            manifest['elapsed_seconds'] = elapsed
            manifest['status'] = ('complete' if result.returncode == 0 and elapsed >= seconds - .5
                                  else 'partial') if manifest['pcap']['has_packet_data'] else 'no_packets'
            warnings[:] = [w for w in warnings if not w.startswith('tcpdump 达到包数')]
            if manifest['status'] == 'partial':
                warnings.append('dumpcap 提前退出、达到文件额度或异常结束，未覆盖全部请求时长。')
        if manifest['pcap']['truncated_packets'] or manifest['tcpdump_stats']['dropped_by_kernel'] or any(
                manifest.get('capture_health', {}).get(key) for key in ('capture_dropped_packets', 'kernel_dropped_packets', 'interface_dropped_packets')):
            manifest['status'] = 'partial'
            warnings.append('抓包包含截断包或采集点报告了丢弃，完整性受限。')
        if result.returncode not in (0, 124):
            warnings.append("远端抓包异常退出；已取回文件，但须检查 tcpdump.log 和 PCAP 完整性。")
    except KeyboardInterrupt as error:
        manifest.update(status="interrupted", error="本地操作中断；远端抓包最长持续到原定时限，可稍后 fetch")
        raise ValueError(manifest["error"]) from error
    except (ValueError, OSError) as error:
        manifest.update(status="failed", error=str(error))
        raise
    finally:
        manifest["updated_at"] = utc_now()
        write_json(manifest_path, manifest)
    return manifest


def fetch(ssh, remote_dir, output):
    remote_directory(remote_dir)
    output = new_output(output)
    output.chmod(0o700)
    manifest = {"schema_version": "1.0", "tool": "capture", "tool_version": __version__,
                "created_at": utc_now(), "host": ssh.host, "remote_dir": remote_dir,
                "warnings": ["恢复取回不验证原抓包进程退出状态；请结合原 capture.json 和日志。"]}
    try:
        manifest["pcap"] = retrieve(ssh, remote_dir, output)
        manifest["status"] = "recovered" if manifest["pcap"]["has_packet_data"] else "no_packets"
        if manifest['pcap']['truncated_packets']:
            manifest['status'] = 'partial'
    except (ValueError, OSError) as error:
        manifest.update(status="failed", error=str(error))
        raise
    finally:
        write_json(output / "capture.json", manifest)
    return manifest
