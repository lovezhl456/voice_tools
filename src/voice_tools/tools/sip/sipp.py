"""One-call SIPp replay packages. Authentication belongs to the PJSUA2 path in v1."""
import ipaddress
import math
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

from voice_tools.core.files import new_output, read_json, sha256, write_json
from .pcap import export_stream, media_from_stream, select
from .scenario import number, sip_uri
from .processes import termination_as_interrupt


def prepare(capture, stream_id, output, target, local_ip, sip_port=5062, rtp_port=6000,
            codec=None, audio_pt=None, dtmf_pt=None):
    target = sip_uri(target)
    local_ip = str(ipaddress.IPv4Address(local_ip))
    address = target[4:].split("@", 1)[1].split(";", 1)[0]
    host, separator, port = address.partition(":")
    host = str(ipaddress.IPv4Address(host))
    remote_port = int(port) if separator else 5060
    number(sip_port, 1024, 65535, "local SIP port", True)
    number(rtp_port, 1024, 65000, "RTP port", True)
    if sip_port == rtp_port or rtp_port % 2:
        raise ValueError("SIP 与 RTP 端口应不同，RTP 端口须为偶数")
    stream = select(capture, stream_id)
    if ipaddress.ip_address(stream["src"]).version != 4:
        raise ValueError("第一版 SIPp 回放只支持 IPv4 原始流")
    # Validate mappings and reject unsupported/encrypted payloads before exporting.
    _, details = media_from_stream(stream, codec, audio_pt, dtmf_pt)
    duration = max(p["epoch"] for p in stream["packets"]) - min(p["epoch"] for p in stream["packets"]) + 0.25
    if not 0 < duration <= 900:
        raise ValueError("SIPp 回放捕获时长须为 0–900 秒")
    output = new_output(output).resolve()
    pcap = output / "selected.pcap"
    export_stream(capture, stream, pcap)
    payloads = str(details["audio_pt"])
    rtpmap = f"a=rtpmap:{details['audio_pt']} {details['codec']}/8000"
    if dtmf_pt is not None:
        payloads += f" {dtmf_pt}"
        rtpmap += f"\na=rtpmap:{dtmf_pt} telephone-event/8000\na=fmtp:{dtmf_pt} 0-15"
    # Paths are relative to the immutable package; the runner copies it to its output directory.
    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE scenario SYSTEM "sipp.dtd">
<scenario name="voice-tools single RTP replay">
  <send retrans="500"><![CDATA[
INVITE {target} SIP/2.0
Via: SIP/2.0/UDP [local_ip]:[local_port];branch=[branch]
From: <sip:voice-tools@[local_ip]:[local_port]>;tag=[call_number]
To: <{target}>
Call-ID: [call_id]
CSeq: 1 INVITE
Contact: <sip:voice-tools@[local_ip]:[local_port]>
Max-Forwards: 70
Content-Type: application/sdp
Content-Length: [len]

v=0
o=voice-tools 1 1 IN IP4 [local_ip]
s=voice-tools
c=IN IP4 [media_ip]
t=0 0
m=audio [media_port] RTP/AVP {payloads}
{rtpmap}
a=sendrecv
]]></send>
  <recv response="100" optional="true"/>
  <recv response="180" optional="true"/>
  <recv response="183" optional="true"/>
  <recv response="200" rrs="true"/>
  <send><![CDATA[
ACK [next_url] SIP/2.0
Via: SIP/2.0/UDP [local_ip]:[local_port];branch=[branch]
From: <sip:voice-tools@[local_ip]:[local_port]>;tag=[call_number]
[last_To:]
Call-ID: [call_id]
CSeq: 1 ACK
[routes]
Content-Length: 0

]]></send>
  <nop><action><exec play_pcap_audio="selected.pcap"/></action></nop>
  <pause milliseconds="{math.ceil(duration * 1000)}"/>
  <send retrans="500"><![CDATA[
BYE [next_url] SIP/2.0
Via: SIP/2.0/UDP [local_ip]:[local_port];branch=[branch]
From: <sip:voice-tools@[local_ip]:[local_port]>;tag=[call_number]
[last_To:]
Call-ID: [call_id]
CSeq: 2 BYE
[routes]
Content-Length: 0

]]></send>
  <recv response="200"/>
</scenario>
'''
    (output / "scenario.xml").write_text(xml, encoding="utf-8")
    plan = {"schema_version": "1.0", "kind": "sipp_replay", "target_uri": target, "remote": f"{host}:{remote_port}",
            "local_ip": local_ip, "sip_port": sip_port, "rtp_port": rtp_port, "duration_s": duration,
            "runtime_limit_s": math.ceil(duration) + 40, "codec": details["codec"], "audio_pt": details["audio_pt"], "dtmf_pt": dtmf_pt,
            "source_sha256": capture["sha256"], "stream_id": stream_id,
            "files": {name: sha256(output / name) for name in ("scenario.xml", "selected.pcap")},
            "limitations": ["只执行 1 次无注册、无 Digest 的 IPv4 G.711 UDP 呼叫。", "对端须接受所选 codec 和 telephone-event PT；不自动改写原 RTP。",
                            "可选 live capture 保存原始包；本路径不直接生成接收 WAV。", "某些 SIPp 构建／系统需要原始套接字权限；工具不自动提权。"]}
    write_json(output / "plan.json", plan)
    return plan


def load_package(path):
    path = Path(path).resolve()
    plan = read_json(path / "plan.json")
    if not isinstance(plan, dict) or plan.get("schema_version") != "1.0" or plan.get("kind") != "sipp_replay":
        raise ValueError("无效 SIPp 回放包")
    required = {"target_uri", "local_ip", "remote", "sip_port", "rtp_port", "runtime_limit_s", "limitations", "files"}
    if not required <= set(plan) or not isinstance(plan["files"], dict):
        raise ValueError("SIPp 回放包缺少必要字段")
    for name in ("scenario.xml", "selected.pcap"):
        if plan.get("files", {}).get(name) != sha256(path / name):
            raise ValueError(f"SIPp 包摘要不匹配：{name}；请重新 prepare")
    # No arbitrary extra arguments or file paths are accepted from a package.
    sip_uri(plan["target_uri"])
    ipaddress.IPv4Address(plan["local_ip"])
    if not isinstance(plan["remote"], str) or plan["remote"].count(":") != 1:
        raise ValueError("SIPp remote 必须是 IPv4:port")
    host, port = plan["remote"].split(":")
    ipaddress.IPv4Address(host)
    number(int(port), 1, 65535, "remote port", True)
    number(plan["sip_port"], 1024, 65535, "sip_port", True)
    number(plan["rtp_port"], 1024, 65000, "rtp_port", True)
    number(plan["runtime_limit_s"], 1, 940, "runtime_limit_s", True)
    # SIPp XML has an exec action. Accept only our generated media-play action, no commands.
    import xml.etree.ElementTree as ET
    try:
        tree = ET.fromstring((path / "scenario.xml").read_text())
    except ET.ParseError as exc:
        raise ValueError("无效 SIPp XML") from exc
    for action in tree.iter("exec"):
        if action.attrib != {"play_pcap_audio": "selected.pcap"}:
            raise ValueError("SIPp 包含非媒体播放 exec；拒绝执行")
    return path, plan


def check_raw_socket_permission(binary):
    # Darwin PCAPPLAY uses raw UDP. Fail before INVITE, rather than leaving an
    # answered remote call when the playback thread subsequently aborts.
    # Linux executables may carry CAP_NET_RAW independently of this Python process.
    if sys.platform != "darwin":
        return
    info = Path(binary).stat()
    if info.st_uid == 0 and info.st_mode & stat.S_ISUID:
        return
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_UDP)
    except PermissionError as exc:
        raise ValueError("尚未拨号：当前 macOS 进程没有 SIPp PCAP play 所需的原始 UDP 套接字权限；请在具备相应权限的环境执行，或使用 PJSUA2 的 PCAP 内容回放。工具不自动提权。") from exc
    else:
        probe.close()


def stop_process(process):
    if process and process.poll() is None:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def run_package(path, output, binary="sipp", capture_interface=None, tshark="tshark", dry_run=False):
    package, plan = load_package(path)
    executable = shutil.which(binary)
    if not dry_run and not executable:
        raise ValueError("缺少 SIPp；需要启用 PCAP play 的 SIPp 构建")
    if not dry_run:
        check_raw_socket_permission(executable)
    capture_bin = shutil.which(tshark) if capture_interface else None
    if capture_interface and not dry_run and not capture_bin:
        raise ValueError("live capture 需要 tshark")
    output = new_output(output).resolve()
    command = [executable or binary, plan["remote"], "-sf", "scenario.xml", "-t", "u1", "-m", "1", "-l", "1", "-r", "1",
               "-i", plan["local_ip"], "-p", str(plan["sip_port"]), "-mi", plan["local_ip"], "-mp", str(plan["rtp_port"]),
               "-timeout", str(plan["runtime_limit_s"]) + "s", "-timeout_error", "-nostdin", "-trace_err"]
    result = {"schema_version": "1.0", "backend": "sipp", "status": "planned" if dry_run else "failed", "command": command,
              "capture_enabled": bool(capture_interface), "business_assertions": "not_evaluated", "warnings": plan["limitations"]}
    write_json(output / "plan.json", plan)
    if dry_run:
        write_json(output / "result.json", result)
        return result
    for name in ("scenario.xml", "selected.pcap"):
        shutil.copyfile(package / name, output / name)
        if sha256(output / name) != plan["files"][name]:
            raise ValueError("SIPp 包在复制时发生变化；尚未拨号")
    capture = process = None
    with termination_as_interrupt(), (output / "sipp.log").open("wb") as log, (output / "capture.log").open("wb") as capture_log:
        try:
            if capture_interface:
                capture_cmd = [capture_bin, "-n", "-i", capture_interface, "-f", f"udp and port {plan['rtp_port']}",
                               "-a", f"duration:{plan['runtime_limit_s'] + 3}", "-w", str(output / "media.pcapng")]
                capture = subprocess.Popen(capture_cmd, stdout=capture_log, stderr=capture_log)
                capture_file = output / "media.pcapng"
                until = time.monotonic() + 5
                while capture.poll() is None and time.monotonic() < until:
                    if capture_file.exists() and capture_file.stat().st_size >= 24:
                        break
                    time.sleep(.05)
                if capture.poll() is not None or not capture_file.exists() or capture_file.stat().st_size < 24:
                    raise ValueError("抓包进程启动失败；尚未拨号，请查看 capture.log")
            process = subprocess.Popen(command, cwd=output, stdout=log, stderr=log)
            code = process.wait(timeout=plan["runtime_limit_s"] + 5)
            result["sipp_exit_code"] = code
            result["status"] = "completed" if code == 0 else "failed"
            if code:
                result["error"] = {"code": "SIPP_FAILED", "message": "SIPp 未完成场景；请检查 sipp.log"}
        except (ValueError, OSError, subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            result["error"] = {"code": "REPLAY_ERROR", "message": str(exc) or "操作者终止"}
        finally:
            stop_process(process)
            stop_process(capture)
            if capture and capture.returncode not in (0, 130, -signal.SIGINT):
                result["status"] = "failed"
                result["error"] = {"code": "CAPTURE_FAILED", "message": "媒体抓包未正常完成；检查 capture.log"}
    if capture_interface and result["status"] == "completed":
        from .pcap import read_capture
        try:
            recorded = read_capture(output / "media.pcapng", [plan["rtp_port"]], tshark)
            result["captured_rtp_packets"] = sum(len(s["packets"]) for s in recorded["streams"].values())
            if not result["captured_rtp_packets"]:
                raise ValueError("未捕获 RTP 媒体")
        except (ValueError, OSError) as exc:
            result["status"] = "failed"
            result["error"] = {"code": "CAPTURE_INCOMPLETE", "message": str(exc)}
    write_json(output / "result.json", result)
    return result
