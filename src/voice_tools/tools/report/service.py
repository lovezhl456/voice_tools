from datetime import datetime, timezone
from contextlib import ExitStack
from pathlib import Path
import shutil
import tempfile

from voice_tools import __version__
from voice_tools.audio.io import write_wav
from voice_tools.audio.formats import load_for_inspection
from voice_tools.audio.health import inspect_health, waveform
from voice_tools.core.files import new_output, read_json, sha256, write_json
from . import pcap
from .render import render


def build(output, audio_paths=(), pcap_paths=(), captures=(), rtp_ports=(), clock_rates=None,
          include_audio=False, threshold_db=-45, max_packets=250000, tshark="tshark", title="通话媒体分析报告",
          session_exports=(), correlations=(), pcap_groups=(), rtcp_ports=(), decode_rtp=False, rtp_timeline=False):
    audio_paths = list(dict.fromkeys(Path(p).resolve() for p in audio_paths))
    sources = {Path(p).resolve(): {"ports": set(rtp_ports)} for p in pcap_paths}
    for sensor, paths in pcap_groups:
        resolved = [Path(p).resolve() for p in paths]
        for path in resolved:
            sources.pop(path, None)
        sources[resolved[0]] = {'ports': set(rtp_ports), 'paths': resolved, 'sensor': sensor}
    capture_info = []
    session_info, correlation_info = [], []
    for directory in session_exports:
        directory = Path(directory).resolve()
        meta = read_json(directory / 'session.json')
        if meta.get('tool') != 'sessions-export' or meta.get('schema_version') != '1.0':
            raise ValueError('不支持的会话导出清单')
        for record in meta.get('files', []):
            path = (directory / record['file']).resolve()
            if directory not in path.parents or sha256(path) != record['sha256']:
                raise ValueError('会话导出文件路径或 SHA-256 校验失败')
            sources[path] = {'ports': set(rtp_ports) | set(record.get('rtp_ports', [])),
                             'sensor': record.get('sensor_id', record.get('host')),
                             'call_id': meta.get('call_id'),
                             'rtcp_ports': set(record.get('rtcp_ports', [])),
                             'mappings': record.get('media_timeline', [])}
        session_info.append(meta)
    for directory in correlations:
        meta = read_json(Path(directory) / 'correlation.json')
        if meta.get('tool') != 'sessions-correlation' or meta.get('schema_version') != '1.0':
            raise ValueError('不支持的 HOMER 关联清单')
        if session_info and meta.get('call_id') not in {s['call_id'] for s in session_info}:
            raise ValueError('HOMER 关联与导出会话的 Call-ID 不一致')
        correlation_info.append(meta)
    # Capture-specific Decode As rules must not leak into unrelated PCAPs.
    for directory in captures:
        directory = Path(directory).resolve()
        meta = read_json(directory / "capture.json")
        if meta.get("schema_version") != "1.0" or meta.get("tool") != "capture":
            raise ValueError(f"不支持的抓包清单：{directory}")
        record = meta.get("pcap", {})
        if record.get("file") != "session.pcap":
            raise ValueError(f"抓包目录尚未包含已取回的 session.pcap：{directory}")
        path = directory / "session.pcap"
        if sha256(path) != record.get("sha256"):
            raise ValueError(f"抓包文件与清单 SHA-256 不一致：{directory}")
        item = sources.setdefault(path, {"ports": set(rtp_ports)})
        for flow in meta.get("flows", []):
            item["ports"].update((int(flow["local_port"]), int(flow["remote_port"])))
        capture_info.append({"directory": str(directory), "status": meta.get("status"),
                             "host": meta.get("host"), "started_at": meta.get("started_at"),
                             "finished_at": meta.get("finished_at"), "bpf": meta.get("bpf"),
                             "tcpdump_stats": meta.get("tcpdump_stats", {}),
                             'capture_health': meta.get('capture_health', {}),
                             'truncated_packets': record.get('truncated_packets'),
                             "warnings": meta.get("warnings", [])})
    if not audio_paths and not sources and not correlation_info and not session_info:
        raise ValueError("至少提供一份录音、PCAP 或抓包目录")
    if not -100 <= threshold_db <= -1 or not 1 <= max_packets <= 1000000:
        raise ValueError("活动门限须为 -100 至 -1 dBFS，max-packets 为 1–1000000")
    if any(not 1 <= int(p) <= 65535 for p in rtcp_ports):
        raise ValueError('RTCP 端口须为 1–65535')
    output = new_output(output)
    output.chmod(0o700)
    data = {"schema_version": "1.0", "tool": "report", "tool_version": __version__, "title": title,
            "created_at": datetime.now(timezone.utc).isoformat(), "audio": [], "pcaps": [], "captures": capture_info,
            "sessions": session_info, "correlations": correlation_info,
            "parameters": {"threshold_db": threshold_db, "max_packets": max_packets,
                           "clock_rates": clock_rates or {}, "include_audio": include_audio, "decode_rtp": decode_rtp, "rtcp_ports": list(rtcp_ports)},
            "conclusion": "未定位根因。录音和网络指标分别提供可复核线索；需要会话、时间轴和业务事件才能建立因果关联。"}
    for index, path in enumerate(audio_paths, 1):
        item = {"path": str(path), "name": path.name}
        try:
            item["sha256"] = sha256(path)
            audio, info = load_for_inspection(path)
            item.update(status="ok", sample_rate=audio.sample_rate, duration_s=audio.duration_s,
                        channels=audio.samples.shape[1], input_info=info, analysis_basis="decoded_pcm16",
                        health=inspect_health(audio, threshold_db), waveform=waveform(audio, bins=600))
            if include_audio:
                media = output / "audio"
                media.mkdir(exist_ok=True)
                name = f"{index:03d}-{item['sha256'][:12]}.wav"
                if info["native_pcm16"]:
                    shutil.copyfile(path, media / name)
                else:
                    write_wav(media / name, audio.samples, audio.sample_rate)
                (media / name).chmod(0o600)
                item["playback"] = "audio/" + name
                item["playback_sha256"] = sha256(media / name)
        except (ValueError, OSError) as error:
            item.update(status="error", error=str(error))
        data["audio"].append(item)
    grouped = {}
    for path, options in sources.items():
        sensor = options.get('sensor') or str(path)
        key = (sensor, options.get('call_id'))
        group = grouped.setdefault(key, {'sensor': sensor, 'call_id': options.get('call_id'),
                                         'paths': [], 'ports': set(), 'rtcp_ports': set(rtcp_ports), 'mappings': []})
        group['paths'].extend(options.get('paths', [path]))
        group['ports'].update(options['ports'])
        group['rtcp_ports'].update(options.get('rtcp_ports', []))
        group['mappings'].extend(options.get('mappings', []))
    audio_bytes = 0
    for group_number, options in enumerate(grouped.values(), 1):
        path = options['paths'][0]
        item = {"path": str(path), "name": path.name}
        try:
            with ExitStack() as cleanup:
                timeline_directory = None
                if rtp_timeline:
                    source_hashes = [sha256(p) for p in options['paths']]
                    # Publish only after all grouped sources pass the second hash.
                    staging = cleanup.enter_context(tempfile.TemporaryDirectory(prefix='.rtp-timeline-', dir=output))
                    timeline_directory = Path(staging) / 'timeline'
                item["sha256"] = source_hashes[0] if rtp_timeline else sha256(path)
                analysis = pcap.analyze_group(options['paths'], options['ports'], clock_rates, max_packets, tshark,
                    options['rtcp_ports'], options['mappings'], output / 'rtp-audio' if decode_rtp else None,
                    f'group-{group_number:03d}', 256*1048576-audio_bytes, timeline_output=timeline_directory)
                audio_bytes += analysis['media']['audio_bytes']
                if rtp_timeline:
                    if source_hashes != [sha256(p) for p in options['paths']]:
                        raise ValueError('PCAP 在分析期间发生变化，未发布 RTP 时序；请使用已停止写入的抓包')
                    timeline = analysis['timeline']
                    timeline.update(sensor=options['sensor'], call_id=options['call_id'],
                        sources=[{'sha256': digest, 'name': Path(p).name}
                                 for p, digest in zip(options['paths'], source_hashes)])
                    write_json(timeline_directory / 'timeline.json', timeline)
                    published = output / f'rtp-timeline-{group_number:03d}'
                    timeline_directory.rename(published)
                    item['rtp_timeline'] = str((published / 'timeline.json').relative_to(output))
                item.update(status="ok", sensor=options['sensor'], call_id=options['call_id'],
                            source_files=[str(p) for p in options['paths']], analysis=analysis)
        except (ValueError, OSError) as error:
            item.update(status="error", error=str(error))
        data["pcaps"].append(item)
    data["errors"] = sum(item["status"] == "error" for item in data["audio"] + data["pcaps"])
    data["partial"] = bool(data["errors"]
                           or any(item.get("analysis", {}).get("packet_limit_reached")
                                  or item.get('analysis', {}).get('truncated_packets')
                                  or item.get('analysis', {}).get('media', {}).get('partial') for item in data["pcaps"])
                           or any(item["status"] not in ("complete", "recovered") for item in capture_info))
    data['partial'] |= any(item.get('partial') for item in session_info + correlation_info)
    write_json(output / "report.json", data)
    (output / "report.html").write_text(render(data), encoding="utf-8")
    return data
