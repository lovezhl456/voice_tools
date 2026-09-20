"""Portable, constrained SIPp load packages; functional recordings stay in PJSUA2."""
import csv
import ipaddress
import math
import shlex
import shutil
import subprocess
import struct
import time
from pathlib import Path

from voice_tools.core.files import new_output, read_json, sha256, write_json
from .batch import resolve_scenario, stop_process
from .processes import termination_as_interrupt
from .scenario import number, object_fields, sip_uri, text


def validate_request(data, base):
    object_fields(data, {"schema_version", "kind", "scenario", "targets", "calls", "concurrency", "rate", "local_ip", "sip_port", "rtp_port", "timeout_s"}, "SIPp load")
    if data.get("schema_version") != "1.0" or data.get("kind") != "sipp_load":
        raise ValueError("压力配置须为 schema_version=1.0、kind=sipp_load")
    source, plan = resolve_scenario(data.get("scenario"), base)
    if plan.get("benchmark"):
        raise ValueError("SIPp 不支持媒体桥时序测量或 wait_audio，请使用 sip run/batch")
    if any(k in plan["account"] for k in ("auth", "registrar_uri", "proxy_uri")):
        raise ValueError("SIPp 压力导出暂不支持认证、REGISTER 或代理；请用功能批量测试")
    if source.get("assertions"):
        raise ValueError("SIPp 压力导出不执行功能断言，请使用功能批量测试")
    if plan["record_early"]:
        raise ValueError("SIPp 压力导出不提供接收录音，请关闭 record_early 或使用功能批量测试")
    if "[" in plan["account"]["id_uri"]:
        raise ValueError("SIPp 压力主叫地址暂不支持 IPv6")
    # Direct SIPp scenarios intentionally cover only lossless conversions.
    for step in plan["steps"]:
        if step["action"] not in ("wait", "dtmf", "hangup"):
            raise ValueError("SIPp 压力导出支持 wait、DTMF、hangup；WAV/play_media 请用功能批量或单流 sipp-prepare")
    targets = data.get("targets", [plan["target_uri"]])
    if not isinstance(targets, list) or not 1 <= len(targets) <= 1000:
        raise ValueError("targets 必须包含 1–1000 个 SIP URI")
    targets = [sip_uri(target) for target in targets]
    remote = None
    for target in targets:
        address = target[4:].split("@", 1)[1].split(";", 1)[0]
        host, sep, port = address.partition(":")
        try:
            host = str(ipaddress.IPv4Address(host))
        except ValueError as exc:
            raise ValueError("SIPp 压力目标必须使用 IPv4 地址") from exc
        endpoint = f"{host}:{int(port) if sep else 5060}"
        if remote and endpoint != remote:
            raise ValueError("同一 SIPp 包的目标必须使用相同 IPv4 网关和端口")
        remote = endpoint
    # SIPp CSV uses semicolon delimiters; UDP is already enforced by -t u1.
    targets = [target.removesuffix(";transport=udp") for target in targets]
    local_ip = str(ipaddress.IPv4Address(text(data.get("local_ip", "127.0.0.1"), "local_ip")))
    calls = number(data.get("calls", 10), 1, 1000000, "calls", True)
    concurrency = number(data.get("concurrency", 1), 1, 10000, "concurrency", True)
    rate = number(data.get("rate", 1), .01, 10000, "rate")
    sip = number(data.get("sip_port", 5062), 1024, 65535, "sip_port", True)
    rtp = number(data.get("rtp_port", 6000), 1024, 65000, "rtp_port", True)
    if rtp % 2 or sip in (rtp, rtp + 1):
        raise ValueError("RTP 端口须为偶数，且不能与 SIP/RTCP 重叠")
    timeout = number(data.get("timeout_s", 300), 1, 86400, "timeout_s", True)
    return {"schema_version": "1.0", "kind": "sipp_load", "scenario": source, "targets": targets,
            "calls": calls, "concurrency": concurrency, "rate": rate, "local_ip": local_ip,
            "sip_port": sip, "rtp_port": rtp, "timeout_s": timeout}, plan, remote


def compile_xml(plan):
    """Emit only known SIP messages and PCAP DTMF actions, never shell exec."""
    codec = plan["codec"]
    pt = 8 if codec == "PCMA" else 0
    rfc = any(s["action"] == "dtmf" and s["method"] == "rfc4733" for s in plan["steps"])
    caller = plan["account"]["id_uri"]
    dtmf_sdp = "\na=rtpmap:101 telephone-event/8000\na=fmtp:101 0-15" if rfc else ""
    header = f"Via: SIP/2.0/UDP [local_ip]:[local_port];branch=[branch]\nFrom: <{caller}>;tag=[call_number]\n"
    invite = f"""INVITE [field0] SIP/2.0
{header}To: <[field0]>
Call-ID: [call_id]
CSeq: 1 INVITE
Contact: <sip:voice-tools@[local_ip]:[local_port]>
Max-Forwards: 70
Content-Type: application/sdp
Content-Length: [len]

v=0
o=voice-tools [call_number] 1 IN IP4 [local_ip]
s=voice-tools-load
c=IN IP4 [media_ip]
t=0 0
m=audio [media_port] RTP/AVP {pt}{' 101' if rfc else ''}
a=rtpmap:{pt} {codec}/8000{dtmf_sdp}
a=sendrecv
"""
    def send(message, retrans=False):
        attribute = ' retrans="500"' if retrans else ''
        return f'<send{attribute}><![CDATA[\n{message}\n]]></send>'

    def dialog(method, cseq, body=""):
        content = "Content-Type: application/dtmf-relay\n" if body else ""
        return (f"{method} [next_url] SIP/2.0\n{header}[last_To:]\nCall-ID: [call_id]\nCSeq: {cseq} {method}\n"
                f"[routes]\nMax-Forwards: 70\n{content}Content-Length: [len]\n\n{body}")

    timeout = math.ceil(plan["connect_timeout_s"] * 1000)
    parts = ['<?xml version="1.0" encoding="UTF-8"?>', '<scenario name="voice-tools load">', send(invite, True),
             '<recv response="100" optional="true"/>', '<recv response="180" optional="true"/>',
             '<recv response="183" optional="true"/>', f'<recv response="200" rrs="true" rtd="true" timeout="{timeout}"/>', send(dialog("ACK", 1))]
    cseq = 2
    for index, step in enumerate(plan["steps"]):
        if step["action"] == "wait":
            if step["seconds"]:
                parts.append(f'<pause milliseconds="{math.ceil(step["seconds"] * 1000)}"/>')
        elif step["action"] == "dtmf":
            if step["method"] == "rfc4733":
                parts.append(f'<nop><action><exec play_pcap_audio="dtmf-{index}.pcap"/></action></nop>')
                parts.append(f'<pause milliseconds="{len(step["digits"]) * (step["duration_ms"] + step["gap_ms"])}"/>')
                continue
            for digit in step["digits"]:
                if step["method"] == "sip_info":
                    parts.extend([send(dialog("INFO", cseq, f'Signal={digit}\nDuration={step["duration_ms"]}\n'), True),
                                  f'<recv response="200" timeout="{timeout}"/>'])
                    cseq += 1
                parts.append(f'<pause milliseconds="{step["duration_ms"] + step["gap_ms"]}"/>')
    parts.extend([send(dialog("BYE", cseq), True), f'<recv response="200" timeout="{timeout}"/>',
                  '<ResponseTimeRepartition value="10,20,50,100,150,200,500,1000"/>', '</scenario>'])
    # Mark INVITE as the beginning of the response-time measurement.
    return "\n".join(parts).replace('<send retrans="500">', '<send retrans="500" start_rtd="true">', 1) + "\n"


def dtmf_pcap(step):
    """Classic Ethernet PCAP with exact RFC4733 duration, gap and end redundancy."""
    packets = [struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)]
    seq = 0
    for i, digit in enumerate(step["digits"]):
        start = i * (step["duration_ms"] + step["gap_ms"])
        duration = step["duration_ms"]
        events = [(ms, False) for ms in range(0, duration, 20)] + [(duration + n, True) for n in (0, 1, 2)]
        for ms, end in events:
            event = struct.pack("!BBH", "0123456789*#ABCD".index(digit), 0x8A if end else 10, min(ms + 20, duration) * 8)
            rtp = struct.pack("!BBHII", 0x80, 101 | (0x80 if ms == 0 else 0), seq % 65536, start * 8, 0x56544C31) + event
            udp = struct.pack("!HHHH", 6000, 6002, len(rtp) + 8, 0) + rtp
            ip = bytearray(struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), seq % 65536, 0, 64, 17, 0,
                                       bytes([127, 0, 0, 1]), bytes([127, 0, 0, 1])))
            check = sum(struct.unpack("!10H", ip))
            while check >> 16:
                check = (check & 65535) + (check >> 16)
            ip[10:12] = struct.pack("!H", ~check & 65535)
            frame = bytes(12) + b"\x08\x00" + ip + udp
            stamp = start + ms
            packets.append(struct.pack("<IIII", stamp // 1000, stamp % 1000 * 1000, len(frame), len(frame)) + frame)
            seq += 1
    return b"".join(packets)


def media_files(plan):
    return {f"dtmf-{i}.pcap": dtmf_pcap(step) for i, step in enumerate(plan["steps"])
            if step["action"] == "dtmf" and step["method"] == "rfc4733"}


def argv_for(request, remote, executable="sipp"):
    return [executable, remote, "-sf", "scenario.xml", "-inf", "targets.csv", "-i", request["local_ip"],
            "-p", str(request["sip_port"]), "-mi", request["local_ip"], "-mp", str(request["rtp_port"]),
            "-t", "u1", "-r", str(request["rate"]), "-rp", "1000", "-l", str(request["concurrency"]),
            "-m", str(request["calls"]), "-timeout", f'{request["timeout_s"]}s', "-timeout_error", "-nostdin",
            "-trace_stat", "-stf", "statistics.csv", "-fd", "1", "-trace_err", "-error_file", "errors.log"]


def export_package(path, output):
    request, plan, remote = validate_request(read_json(path), Path(path).resolve().parent)
    output = new_output(output).resolve()
    output.chmod(0o700)
    write_json(output / "load.json", request)
    (output / "scenario.xml").write_text(compile_xml(plan), encoding="utf-8")
    (output / "targets.csv").write_text("SEQUENTIAL\n" + "\n".join(request["targets"]) + "\n", encoding="utf-8")
    for name, content in media_files(plan).items():
        (output / name).write_bytes(content)
    command = argv_for(request, remote)
    (output / "run.sh").write_text('#!/bin/sh\nset -eu\ncd -- "$(dirname -- "$0")"\nexec ' + shlex.join(command) + "\n", encoding="utf-8")
    (output / "run.sh").chmod(0o700)
    result = {"schema_version": "1.0", "kind": "sipp_load_package", "status": "exported", "network_accessed": False,
              "command": command, "files": {name: sha256(output / name) for name in ("load.json", "scenario.xml", "targets.csv", "run.sh", *media_files(plan))},
              "limitations": ["仅 SIP/UDP/IPv4；无 REGISTER、Digest、代理、接收录音或业务断言。",
                              "RFC4733 使用生成的 RFC4733 PCAP，需要 SIPp PCAP 功能及系统发包权限，固定 telephone-event PT 101。",
                              "等待/按键场景不发送连续语音 RTP；不是语音带宽压力测试。"]}
    write_json(output / "manifest.json", result)
    return result


def run_load(path, output, executable="sipp", dry_run=False):
    path = Path(path).resolve()
    # Regenerate and compare executable inputs. A modified package cannot smuggle shell/XML actions.
    request, plan, remote = validate_request(read_json(path / "load.json"), path)
    if (path / "scenario.xml").read_text() != compile_xml(plan):
        raise ValueError("scenario.xml 与 load.json 不一致，请重新导出")
    if (path / "targets.csv").read_text() != "SEQUENTIAL\n" + "\n".join(request["targets"]) + "\n":
        raise ValueError("targets.csv 与 load.json 不一致，请重新导出")
    for name, content in media_files(plan).items():
        if (path / name).read_bytes() != content:
            raise ValueError(f"{name} 与 load.json 不一致，请重新导出")
    binary = shutil.which(executable) if not dry_run else executable
    if not binary:
        raise ValueError("未找到 SIPp；参见 docs/sipp.md 安装后，用 --sipp 指定可执行文件")
    output = new_output(output).resolve()
    output.chmod(0o700)
    for name in ("load.json", "scenario.xml", "targets.csv", *media_files(plan)):
        shutil.copyfile(path / name, output / name)
    command = argv_for(request, remote, str(Path(binary).resolve()) if not dry_run else binary)
    result = {"schema_version": "1.0", "kind": "sipp_load_result", "backend": "sipp", "status": "planned",
              "network_accessed": False, "command": command, "calls_requested": request["calls"],
              "concurrency": request["concurrency"], "rate": request["rate"], "business_assertions": "not_evaluated"}
    write_json(output / "result.json", result)
    if dry_run:
        return result
    started = time.monotonic()
    process = None
    result.update(status="running", network_accessed=True)
    write_json(output / "result.json", result)
    try:
        with termination_as_interrupt(), (output / "sipp.log").open("wb") as log:
            process = subprocess.Popen(command, cwd=output, stdout=log, stderr=log, start_new_session=True)
            try:
                code = process.wait(timeout=request["timeout_s"] + 10)
                result.update(status="completed" if code == 0 else "failed", exit_code=code)
            except subprocess.TimeoutExpired:
                result.update(status="failed", error="SIPp 超过总时限，已终止；本次压力采集不完整")
            except KeyboardInterrupt:
                result.update(status="interrupted", error="用户中断，保留已有统计与日志")
    except KeyboardInterrupt:
        result.update(status="interrupted", error="用户中断，保留已有统计与日志")
    except OSError as exc:
        result.update(status="failed", error=str(exc))
        raise
    finally:
        if process is not None:
            stop_process(process)
            result["exit_code"] = process.returncode
        result["duration_s"] = round(time.monotonic() - started, 3)
        try:
            with (output / "statistics.csv").open(newline="") as stream:
                last = None
                for row in csv.DictReader(stream, delimiter=";"):
                    last = row
            if last is None:
                raise ValueError("统计文件没有数据行")
            result["statistics"] = {key: int(last[column]) for key, column in (
                ("created", "TotalCallCreated"), ("successful", "SuccessfulCall(C)"), ("failed", "FailedCall(C)"), ("active", "CurrentCall"))}
            stats = result["statistics"]
            if result["status"] == "completed" and (stats["created"] != request["calls"] or stats["successful"] != request["calls"] or stats["failed"] or stats["active"]):
                result.update(status="failed", error="SIPp 统计未证明请求的全部呼叫成功结束")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            result["statistics_error"] = str(exc)
            if result["status"] == "completed":
                result.update(status="failed", error="SIPp 未生成有效完整统计，不能判为完成")
        write_json(output / "result.json", result)
    return result
