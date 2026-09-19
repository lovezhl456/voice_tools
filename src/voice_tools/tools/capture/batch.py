from concurrent.futures import ThreadPoolExecutor, as_completed
import ipaddress
import json
import math
from pathlib import Path
import re
import shlex
import threading
import time

from voice_tools import __version__
from voice_tools.core.files import new_output, read_json, write_json
from .service import SSH, port, remote_directory, retrieve, utc_now, validate_capture
from .snapshots import snapshot


def scope_bpf(addresses, sip_ports, rtp_ranges):
    if not isinstance(addresses, list) or not 1 <= len(addresses) <= 64:
        raise ValueError("每台主机必须指定 1–64 个抓包 IP/CIDR")
    nets = []
    for value in addresses:
        if not isinstance(value, str):
            raise ValueError('IP/CIDR 必须为字符串')
        net = ipaddress.ip_network(value, strict=False)
        if net.prefixlen == 0 or net.network_address.is_unspecified or '%' in str(net):
            raise ValueError("批量抓包不接受通配网段或未指定地址")
        nets.append(f"net {net}" if '/' in value else f"host {net.network_address}")
    if not isinstance(sip_ports, list) or len(sip_ports) > 32 or not isinstance(rtp_ranges, list) or len(rtp_ranges) > 16:
        raise ValueError("SIP 端口最多 32 个，RTP 范围最多 16 个")
    signaling = [f"port {port(value)}" for value in sip_ports]
    media = []
    for bounds in rtp_ranges:
        if not isinstance(bounds, list) or len(bounds) != 2:
            raise ValueError("RTP 范围须为 [起始端口, 结束端口]")
        low, high = map(port, bounds)
        if low > high:
            raise ValueError("RTP 端口范围逆序")
        media.append(f"portrange {low}-{high}")
    if not signaling and not media:
        raise ValueError("至少指定 SIP 端口或 RTP 范围")
    filters = []
    if signaling:
        filters.append("((udp or tcp) and (" + " or ".join(signaling) + "))")
    if media:
        filters.append("(udp and (" + " or ".join(media) + "))")
    return "(" + " or ".join(nets) + ") and (" + " or ".join(filters) + ")"


def inventory(path):
    value = read_json(path)
    if not isinstance(value, dict):
        raise ValueError("主机清单顶层须为 JSON 对象")
    hosts = value.get("hosts")
    if value.get("schema_version") != "1.0" or not isinstance(hosts, list) or not 1 <= len(hosts) <= 16:
        raise ValueError("主机清单须为 schema_version=1.0，包含 1–16 台 hosts")
    result, names = [], set()
    for row in hosts:
        if not isinstance(row, dict) or not isinstance(row.get('host'), str):
            raise ValueError("每台主机须为包含 host 的 JSON 对象")
        unknown = set(row) - {'name','host','ssh_port','identity','interface','addresses','sip_ports','rtp_ranges','sudo','fs_cli'}
        if unknown:
            raise ValueError('主机清单含未知字段：' + ', '.join(sorted(unknown)))
        if not isinstance(row.get('fs_cli','fs_cli'),str) or not row.get('fs_cli','fs_cli').strip():
            raise ValueError('fs_cli 须为可执行文件路径')
        name = row.get("name", row.get("host", ""))
        if not isinstance(name,str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name) or name in names:
            raise ValueError("主机 name 须为唯一的字母/数字/横线/下划线，最多 64 字符")
        names.add(name)
        if not isinstance(row.get('interface','any'),str) or (row.get('identity') is not None and not isinstance(row['identity'],str)):
            raise ValueError('interface 与 identity 必须为字符串')
        SSH(row["host"], row.get("ssh_port", 22), row.get("identity"))
        validate_capture(1, 1, 2048, row.get("interface", "any"))
        if not isinstance(row.get("sudo", False), bool):
            raise ValueError("sudo 必须为 JSON 布尔值")
        result.append({**row, "name": name, "bpf": scope_bpf(row.get("addresses"), row.get("sip_ports", [5060]), row.get("rtp_ranges", []))})
    return result


def batch_command(config, directory, user, seconds, segment_seconds, limit, snaplen):
    argv = ["timeout", "--signal=INT", "--kill-after=5s", f"{seconds}s", "tcpdump", "-n", "-U",
            "-i", config.get("interface", "any"), "-s", str(snaplen), "-c", str(limit), "-Z", user,
            "-G", str(segment_seconds), "-W", str(math.ceil(seconds / segment_seconds) + 1),
            "-w", directory + "/part-%Y%m%dT%H%M%S.pcap", config["bpf"]]
    argv = (["sudo", "-n"] if config.get("sudo") else []) + argv
    # Explicit timezone for rotated filenames; this is not a host clock adjustment.
    return ["sh", "-c", "umask 077; export TZ=UTC LC_ALL=C; exec " + shlex.join(argv)]


def fragments(ssh, directory, output, max_files=1442):
    remote_directory(directory)
    listing = ssh.checked(["find", directory, "-maxdepth", "1", "-type", "f", "-name", "part-*.pcap"])
    paths = sorted(set(listing.splitlines()))
    if len(paths) > max_files:
        raise ValueError("远端分片超过本次计划上限")
    files, errors = [], []
    for path in paths:
        if str(Path(path).parent) != directory or not re.fullmatch(r"part-\d{8}T\d{6}\.pcap", Path(path).name):
            raise ValueError("远端返回计划外分片路径")
        try:
            files.append(retrieve(ssh, directory, output, Path(path).name))
        except (ValueError, OSError) as error:
            errors.append({"file": Path(path).name, "error": str(error)})
    return files, errors


def capture_host(config, output, seconds, segment_seconds, max_mib, snaplen, snapshot_seconds, max_channels,
                 stop, ssh_factory=SSH):
    output = new_output(output)
    output.chmod(0o700)
    ssh = ssh_factory(config["host"], config.get("ssh_port", 22), config.get("identity"))
    segments = math.ceil(seconds / segment_seconds) + 1
    limit = (max_mib * 1024 * 1024 - 24 * segments) // (snaplen + 16)
    meta = {"schema_version": "1.0", "tool": "capture-batch-host", "name": config["name"], "host": config["host"],
            "bpf": config["bpf"], "sip_ports": config.get("sip_ports", [5060]), "seconds": seconds,
            "segment_seconds": segment_seconds, "packet_limit": limit, "max_mib": max_mib, "snaplen": snaplen,
            "snapshot_seconds": snapshot_seconds, "files": [], "errors": [], "warnings": [], "status": "preparing"}
    manifest = output / "host.json"
    write_json(manifest, meta)
    try:
        prepared = ssh.checked(["sh", "-c", 'set -eu; umask 077; d=$(mktemp -d /tmp/voice-tools-XXXXXXXXXXXX); '
                                'printf "%s\\n%s\\n" "$d" "$(id -un)"'])
        directory, user = prepared.splitlines()
        remote_directory(directory)
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", user):
            raise ValueError("远端用户名无效")
        meta.update(remote_dir=directory, started_at=utc_now(), status="capturing")
        write_json(manifest, meta)
        deadline, count, offset = time.monotonic() + seconds, 0, 0
        with ThreadPoolExecutor(max_workers=1) as worker:
            future = worker.submit(ssh.run, batch_command(config, directory, user, seconds, segment_seconds, limit, snaplen), seconds + 40)
            while snapshot_seconds and not future.done() and not stop.is_set() and time.monotonic() < deadline:
                if count >= 20000:
                    meta["warnings"].append("达到每主机 20000 条快照上限；抓包继续，会话映射不完整。")
                    break
                try:
                    records, warnings = snapshot(ssh, config.get("fs_cli", "fs_cli"), min(max_channels, 20000-count), offset)
                    offset += max_channels
                    with (output / "sessions.jsonl").open("a", encoding="utf-8") as stream:
                        for item in records:
                            stream.write(json.dumps({**item, "host": config["name"], "window_seconds": snapshot_seconds}, ensure_ascii=False) + "\n")
                    count += len(records)
                    meta["warnings"].extend(warnings)
                except (ValueError, OSError) as error:
                    meta["warnings"].append("FS 快照失败：" + str(error))
                meta["warnings"] = list(dict.fromkeys(meta["warnings"]))[-100:]
                write_json(manifest, meta)
                # Interruptible and at most 30s; capture has its own remote deadline.
                stop.wait(min(snapshot_seconds, max(0, deadline-time.monotonic())))
            result = future.result()
        (output / "tcpdump.log").write_text(result.stderr, encoding="utf-8")
        meta.update(finished_at=utc_now(), capture_exit_code=result.returncode, snapshot_records=count)
        meta["files"], meta["errors"] = fragments(ssh, directory, output, segments)
        meta["status"] = "complete" if result.returncode == 124 and meta["files"] and not meta["errors"] else "partial"
        if result.returncode == 0:
            meta["warnings"].append("达到包数/分片上限或提前退出，未保证覆盖请求的全部时长。")
        if not any(f["has_packet_data"] for f in meta["files"]):
            meta["status"] = "no_packets"
        dropped = re.search(r"(\d+) packets? dropped by kernel", result.stderr)
        meta["kernel_drops"] = int(dropped.group(1)) if dropped else None
        if meta["kernel_drops"]:
            meta["warnings"].append("抓包点内核丢弃了数据包，不能将全部序号缺口归因于网络。")
    except (ValueError, OSError) as error:
        meta.update(status="failed", error=str(error))
    finally:
        write_json(manifest, meta)
    return meta


def run_batch(config_path, output, seconds=300, segment_seconds=60, max_mib=512, snaplen=2048,
              snapshot_seconds=10, max_channels=100, dry_run=False, ssh_factory=SSH):
    hosts = inventory(config_path)
    if not 1 <= seconds <= 86400 or not 10 <= segment_seconds <= 3600 or not 1 <= max_mib <= 16384:
        raise ValueError("批量时长 1–86400 秒，分片间隔 10–3600 秒，每主机总额度 1–16384 MiB")
    if not 0 <= snapshot_seconds <= 30 or (snapshot_seconds and snapshot_seconds < 5) or not 1 <= max_channels <= 500:
        raise ValueError("FS 快照间隔为 0（关闭）或 5–30 秒，每轮最大腿数 1–500")
    if not 64 <= snaplen <= 65535:
        raise ValueError("snaplen 须为 64–65535")
    output = new_output(output)
    output.chmod(0o700)
    meta = {"schema_version": "1.0", "tool": "capture-batch", "tool_version": __version__, "created_at": utc_now(),
            "seconds": seconds, "segment_seconds": segment_seconds, "max_mib_per_host": max_mib,
            "snaplen": snaplen, "snapshot_seconds": snapshot_seconds, "status": "planned", "hosts": [],
            "plans": [{"name": h["name"], "host": h["host"], "bpf": h["bpf"]} for h in hosts],
            "warnings": ["各主机尽力并发启动，不是硬件同步抓包；跨机器时序比较需校准系统时钟。",
                         "FS 快照是有间隔且有限额的取样，短通话和加密 SIP 可能无法建立映射。"]}
    write_json(output / "batch.json", meta)
    if dry_run:
        return meta
    stop = threading.Event()
    try:
        with ThreadPoolExecutor(max_workers=len(hosts)) as pool:
            jobs = {pool.submit(capture_host, h, output / h["name"], seconds, segment_seconds, max_mib,
                                snaplen, snapshot_seconds, max_channels, stop, ssh_factory): h for h in hosts}
            try:
                for future in as_completed(jobs):
                    host = jobs[future]
                    try:
                        result = future.result()
                    except Exception as error:
                        result = {"name": host["name"], "status": "failed", "error": str(error)}
                    meta["hosts"].append({"name": host["name"], "manifest": host["name"] + "/host.json", "status": result["status"]})
                    meta["status"] = "capturing"
                    write_json(output / "batch.json", meta)
            except KeyboardInterrupt:
                stop.set()
                meta["status"] = "interrupted"
                write_json(output / "batch.json", meta)
                raise
        meta["status"] = "complete" if all(h["status"] == "complete" for h in meta["hosts"]) else "partial"
    finally:
        meta["finished_at"] = utc_now()
        write_json(output / "batch.json", meta)
    return meta


def fetch_batch(ssh, remote_dir, output):
    output = new_output(output)
    output.chmod(0o700)
    files, errors = fragments(ssh, remote_dir, output, 8642)
    meta = {"schema_version": "1.0", "tool": "capture-batch-host", "name": ssh.host, "host": ssh.host,
            "remote_dir": remote_dir, "files": files, "errors": errors, "status": "recovered" if files and not errors else "partial",
            "warnings": ["仅恢复分片，未验证原抓包是否正常结束；请保留原 batch/host 清单和 FS 快照。"]}
    write_json(output / "host.json", meta)
    return meta
