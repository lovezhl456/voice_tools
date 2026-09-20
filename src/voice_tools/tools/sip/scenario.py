"""Strict, offline scenario validation. Native SIP libraries are never imported here."""
import math
import re
import wave
from pathlib import Path

from voice_tools.core.files import read_json, sha256

VERSION = "1.0"
MAX_SECONDS = 900
URI = re.compile(r"^sip:[A-Za-z0-9_.!~*'()%+\-]+@(?:[A-Za-z0-9.-]+|\[[0-9A-Fa-f:]+\])(?::[0-9]{1,5})?(?:;transport=udp)?$")
SERVER = re.compile(r"^sip:(?:[A-Za-z0-9.-]+|\[[0-9A-Fa-f:]+\])(?::[0-9]{1,5})?(?:;transport=udp)?$")
DIGITS = "0123456789*#ABCD"


def object_fields(value, allowed, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} 必须是对象")
    unknown = set(value) - set(allowed)
    if unknown:
        raise ValueError(f"{label} 存在未知字段：{', '.join(sorted(unknown))}")
    return value


def number(value, low, high, label, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (isinstance(value, float) and not math.isfinite(value)):
        raise ValueError(f"{label} 必须是有限数值")
    if not low <= value <= high or (integer and not isinstance(value, int)):
        raise ValueError(f"{label} 须为 {low}–{high}" + (" 的整数" if integer else ""))
    return value


def text(value, label, max_length=256):
    if not isinstance(value, str) or not value or len(value) > max_length or any(ord(c) < 32 for c in value):
        raise ValueError(f"{label} 必须是非空、无控制字符的字符串")
    return value


def sip_uri(value, label="target_uri", server=False):
    value = text(value, label)
    if not (SERVER if server else URI).fullmatch(value):
        raise ValueError(f"{label} 仅支持明确的 sip: 地址和 UDP transport")
    port = re.search(r":(\d+)(?:;transport=udp)?$", value)
    if port and not 1 <= int(port[1]) <= 65535:
        raise ValueError(f"{label} 端口无效")
    return value


def wav_info(path):
    path = Path(path)
    try:
        with wave.open(str(path), "rb") as wav:
            frames, rate = wav.getnframes(), wav.getframerate()
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or rate != 8000 or wav.getcomptype() != "NONE":
                raise ValueError("播放素材必须是 8 kHz、单声道 PCM16 WAV；先转换格式")
            number(frames / rate, 0.001, MAX_SECONDS, "WAV 时长")
            if len(wav.readframes(frames)) != frames * 2:
                raise ValueError("WAV 数据被截断")
    except (wave.Error, EOFError) as exc:
        raise ValueError(f"无效 WAV：{path.name}: {exc}") from exc
    return {"file": str(path.resolve()), "duration_s": frames / rate, "frames": frames, "sha256": sha256(path)}


def resolve(base, value):
    p = Path(text(value, "素材路径", 4096)).expanduser()
    return (p if p.is_absolute() else base / p).resolve()


def validate_dtmf_events(events, duration):
    """Keep PCAP export and subsequent media loading on the same event contract."""
    if not isinstance(events, list) or len(events) > 256:
        raise ValueError("media.dtmf 必须是最多 256 项的列表")
    previous_end = -1.0
    for event in events:
        object_fields(event, {"at_s", "digit", "duration_ms", "end_observed"}, "DTMF event")
        at = number(event.get("at_s"), 0, duration, "DTMF at_s")
        ms = number(event.get("duration_ms"), 40, 8000, "DTMF duration_ms", True)
        if event.get("digit") not in list(DIGITS):
            raise ValueError("无效 DTMF digit")
        if "end_observed" in event and not isinstance(event["end_observed"], bool):
            raise ValueError("DTMF end_observed 必须为 boolean")
        if at + ms / 1000 > duration + 1 / 8000 or at + 1e-9 < previous_end:
            raise ValueError("DTMF 事件重叠、乱序或超出素材时间轴")
        previous_end = at + ms / 1000


def media_bundle(path):
    path = Path(path).resolve()
    data = read_json(path)
    object_fields(data, {"schema_version", "audio", "audio_sha256", "duration_s", "dtmf", "source", "warnings"}, "media")
    if data.get("schema_version") != VERSION:
        raise ValueError("不支持的 media schema_version")
    audio = wav_info(resolve(path.parent, data.get("audio")))
    if data.get("audio_sha256") != audio["sha256"]:
        raise ValueError("media 音频 SHA256 不匹配；素材变更后请重新导入")
    duration = number(data.get("duration_s"), 0.001, MAX_SECONDS, "media.duration_s")
    if abs(duration - audio["duration_s"]) > 1 / 8000:
        raise ValueError("media 时长与 WAV 不一致")
    events = data.get("dtmf", [])
    validate_dtmf_events(events, duration)
    return {"path": str(path), "audio": audio, "duration_s": duration, "dtmf": events,
            "warnings": data.get("warnings", [])}


def load_scenario(path):
    path = Path(path).resolve()
    source = read_json(path)
    object_fields(source, {"schema_version", "target_uri", "account", "network", "codec", "connect_timeout_s", "max_call_s", "record_early", "steps", "assertions", "benchmark"}, "scenario")
    from .assertions import validate_assertions
    assertions = validate_assertions(source.get("assertions", []))
    version = source.get("schema_version")
    if version not in (VERSION, "1.1"):
        raise ValueError("scenario.schema_version 必须为 1.0 或 1.1")
    benchmark = None
    if "benchmark" in source:
        if version != "1.1":
            raise ValueError("benchmark 需要 scenario.schema_version=1.1")
        from voice_tools.tools.benchmark.config import configuration
        benchmark = configuration(source["benchmark"])
    account = dict(object_fields(source.get("account", {}), {"id_uri", "registrar_uri", "proxy_uri", "auth"}, "account"))
    account["id_uri"] = sip_uri(account.get("id_uri", "sip:voice-tools@127.0.0.1"), "account.id_uri")
    for key in ("registrar_uri", "proxy_uri"):
        if key in account:
            account[key] = sip_uri(account[key], "account." + key, server=True)
    if "auth" in account:
        auth = object_fields(account["auth"], {"username", "realm", "password_env"}, "auth")
        text(auth.get("username"), "auth.username")
        text(auth.get("realm", "*"), "auth.realm")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text(auth.get("password_env"), "password_env")):
            raise ValueError("password_env 必须为环境变量名；不接受明文密码")
    net = dict(object_fields(source.get("network", {}), {"bind_address", "public_address", "sip_port", "rtp_port"}, "network"))
    for key in ("bind_address", "public_address"):
        if key in net:
            # PJSUA transport and RTP media use the same explicitly configured address.
            import ipaddress
            ipaddress.IPv4Address(text(net[key], "network." + key))
    net["sip_port"] = number(net.get("sip_port", 0), 0, 65535, "sip_port", True)
    net["rtp_port"] = number(net.get("rtp_port", 4000), 1024, 65000, "rtp_port", True)
    if net["rtp_port"] % 2:
        raise ValueError("rtp_port 必须为偶数，下一端口留给 RTCP")
    codec = source.get("codec", "PCMA")
    if codec not in ("PCMA", "PCMU"):
        raise ValueError("第一版 codec 仅支持 PCMA／PCMU")
    connect = number(source.get("connect_timeout_s", 30), 1, 120, "connect_timeout_s")
    maximum = number(source.get("max_call_s", 120), 1, MAX_SECONDS, "max_call_s")
    early = source.get("record_early", False)
    if not isinstance(early, bool):
        raise ValueError("record_early 必须为 boolean")
    steps = source.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 256:
        raise ValueError("steps 必须包含 1–256 个动作")
    resolved, total = [], 0.0
    fields = {"wait": {"seconds"}, "play": {"file"}, "dtmf": {"digits", "method", "duration_ms", "gap_ms"}, "play_media": {"file"}, "hangup": set()}
    if version == "1.1" and benchmark:
        fields["wait_audio"] = {"state", "duration_ms", "timeout_s"}
        fields["play"].add("speech")
        fields["play_media"].add("speech")
    for i, raw in enumerate(steps):
        if not isinstance(raw, dict) or not isinstance(raw.get("action"), str) or raw["action"] not in fields:
            raise ValueError(f"steps[{i}] 不支持的 action")
        action = raw["action"]
        object_fields(raw, fields[action] | {"action"}, f"steps[{i}]")
        step = dict(raw)
        if action == "wait":
            step["duration_s"] = number(raw.get("seconds"), 0, MAX_SECONDS, "wait.seconds")
        elif action == "play":
            step["audio"] = wav_info(resolve(path.parent, raw.get("file")))
            step["duration_s"] = step["audio"]["duration_s"]
        elif action == "play_media":
            step["media"] = media_bundle(resolve(path.parent, raw.get("file")))
            step["duration_s"] = step["media"]["duration_s"]
        elif action == "wait_audio":
            if raw.get("state") not in ("active", "silent"):
                raise ValueError("wait_audio.state 须为 active 或 silent")
            step["duration_ms"] = number(raw.get("duration_ms", 60 if raw["state"] == "active" else 200), 20, 5000, "wait_audio.duration_ms")
            step["timeout_s"] = number(raw.get("timeout_s", 15), .1, MAX_SECONDS, "wait_audio.timeout_s")
            if step["duration_ms"] > step["timeout_s"] * 1000:
                raise ValueError("wait_audio 保持时长超过等待时限")
        elif action == "dtmf":
            digits = text(raw.get("digits"), "dtmf.digits", 128)
            if any(d not in DIGITS for d in digits):
                raise ValueError("DTMF digits 只能使用 0–9、*、#、A–D")
            method = raw.get("method", "rfc4733")
            if method not in ("rfc4733", "sip_info"):
                raise ValueError("DTMF method 仅支持 rfc4733／sip_info")
            step.update(method=method, duration_ms=number(raw.get("duration_ms", 160), 40, 2000, "duration_ms", True),
                        gap_ms=number(raw.get("gap_ms", 100), 40, 2000, "gap_ms", True))
            step["duration_s"] = len(digits) * (step["duration_ms"] + step["gap_ms"]) / 1000
        elif i != len(steps) - 1:
            raise ValueError("hangup 必须是最后一个动作")
        if "speech" in step:
            if not isinstance(step["speech"], list) or len(step["speech"]) > 200:
                raise ValueError("speech 须为最多200个人工确认的源录音语音区间")
            previous_end = 0
            for span in step["speech"]:
                object_fields(span, {"start_s", "end_s"}, "speech")
                begin = number(span.get("start_s"), 0, step["duration_s"], "speech.start_s")
                end = number(span.get("end_s"), 0, step["duration_s"], "speech.end_s")
                if begin < previous_end or end <= begin:
                    raise ValueError("speech 区间须有序、不重叠且长度大于0")
                previous_end = end
        total += step.get("duration_s", 0)
        resolved.append(step)
    if total > maximum:
        raise ValueError("动作总时长超过 max_call_s")
    result = {"schema_version": version, "scenario_file": str(path), "scenario_sha256": sha256(path),
            "target_uri": sip_uri(source.get("target_uri")), "account": account, "network": net, "codec": codec,
            "connect_timeout_s": connect, "max_call_s": maximum, "record_early": early, "steps": resolved,
            "planned_duration_s": total, "assertions": assertions,
            "recording": {"rx": "received decoded PCM", "tx_source": "scheduled local source timeline; not proof of remote reception"}}
    if benchmark is not None:
        result["benchmark"] = benchmark
    return result


def template(media=None):
    return {"schema_version": VERSION, "target_uri": "sip:test@example.invalid:5060", "account": {"id_uri": "sip:tester@127.0.0.1"},
            "network": {"sip_port": 0, "rtp_port": 4000}, "codec": "PCMA", "connect_timeout_s": 30,
            "max_call_s": MAX_SECONDS if media else 120, "record_early": False,
            "steps": ([{"action": "play_media", "file": media}] if media else [{"action": "wait", "seconds": 1}, {"action": "dtmf", "digits": "1", "duration_ms": 160, "gap_ms": 100}])
            + [{"action": "wait", "seconds": 2}, {"action": "hangup"}]}
