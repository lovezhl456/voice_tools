"""显式格式准备；保持声道，不自动修复时间轴、归一化或降噪。"""
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import wave

from .io import read_wav

EXTENSIONS = {".wav", ".flac", ".mp3", ".m4a", ".ogg", ".raw", ".pcm", ".alaw", ".ulaw"}
RAW = {".raw", ".pcm", ".alaw", ".ulaw"}


def run(program, args):
    executable = shutil.which(program)
    if executable is None:
        raise ValueError(f"需要可选外部程序 {program}；请安装 FFmpeg 后重试")
    try:
        result = subprocess.run([executable] + args, capture_output=True, timeout=120)
    except subprocess.TimeoutExpired as error:
        raise ValueError(f"{program} 处理超过 120 秒") from error
    if result.returncode:
        raise ValueError(f"{program} 失败：" + result.stderr.decode("utf-8", errors="replace")[-1200:])
    return result.stdout


def raw_args(path, raw=None):
    if Path(path).suffix.lower() not in RAW:
        return []
    if not raw or set(raw) != {"format", "sample_rate", "channels"}:
        raise ValueError("裸音频须同时指定 --raw-format、--raw-sample-rate、--raw-channels")
    if raw["format"] not in {"s16le", "alaw", "mulaw"} or raw["channels"] not in (1, 2) or not 8000 <= raw["sample_rate"] <= 48000:
        raise ValueError("裸音频格式、采样率或声道数不支持")
    return ["-f", raw["format"], "-sample_rate", str(raw["sample_rate"]),
            "-ch_layout", "mono" if raw["channels"] == 1 else "stereo"]


def probe(path, raw=None):
    path = Path(path).resolve()
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError("音频文件不存在或为空")
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as stream:
                if stream.getcomptype() == "NONE" and stream.getsampwidth() == 2:
                    return {"codec": "pcm_s16le", "sample_rate": stream.getframerate(), "channels": stream.getnchannels(),
                            "channel_layout": None, "duration_s": stream.getnframes() / stream.getframerate(),
                            "start_time_s": 0.0, "native_pcm16": True}
        except (wave.Error, EOFError):
            pass
    result = json.loads(run("ffprobe", ["-v", "error", "-protocol_whitelist", "file,pipe"] + raw_args(path, raw) +
                            ["-show_streams", "-show_format", "-of", "json", str(path)]))
    streams = [s for s in result.get("streams", []) if s.get("codec_type") == "audio"]
    if len(streams) != 1:
        raise ValueError("须有且只有一条音频流；请显式选择音频流后重试")
    stream = streams[0]
    try:
        duration = float(stream.get("duration", result.get("format", {}).get("duration")))
        start = float(stream.get("start_time", result.get("format", {}).get("start_time", 0)))
        rate, channels = int(stream["sample_rate"]), int(stream["channels"])
    except (TypeError, KeyError, ValueError) as error:
        raise ValueError("无法确定录音时长、采样率或声道") from error
    if not math.isfinite(start):
        raise ValueError("录音起点无效")
    return {"codec": stream.get("codec_name"), "sample_rate": rate, "channels": channels,
            "channel_layout": stream.get("channel_layout"), "duration_s": duration,
            "start_time_s": start, "native_pcm16": False}


def validate_info(info, rate):
    if info["channels"] not in (1, 2):
        raise ValueError("只接收单/双声道；不会自动混音")
    if info.get("channel_layout") not in (None, "unknown", "mono", "stereo"):
        raise ValueError("无法明确解释通道布局；不会自动重排声道")
    if not math.isfinite(info["duration_s"]) or not 0 < info["duration_s"] <= 3600:
        raise ValueError("录音时长须大于 0 且不超过 3600 秒")
    if not 8000 <= rate <= 48000:
        raise ValueError("分析采样率须为 8–48 kHz")
    if info["duration_s"] * rate * info["channels"] * 4 > 512 * 1024 * 1024:
        raise ValueError("解码样本将超过 512 MiB；请先按已知时间轴分段")


def decode(path, output, rate, raw=None, info=None):
    info = info or probe(path, raw)
    validate_info(info, rate)
    path, output = Path(path).resolve(), Path(output)
    if output.exists():
        raise ValueError("拒绝覆盖已有转换文件")
    if info["native_pcm16"]:
        read_wav(path)  # 重采样前也验证原始帧数，不能让解码器补救截断。
    if info["native_pcm16"] and info["sample_rate"] == rate:
        shutil.copyfile(path, output)
        command = ["copy_pcm16"]
        version = None
    else:
        command = ["-v", "error", "-xerror", "-nostdin", "-n", "-protocol_whitelist", "file,pipe"] + raw_args(path, raw)
        command += ["-i", str(path), "-map", "0:a:0", "-vn", "-ar", str(rate), "-c:a", "pcm_s16le", str(output)]
        try:
            run("ffmpeg", command)
        except (ValueError, OSError):
            output.unlink(missing_ok=True)
            raise
        version = run("ffmpeg", ["-version"]).decode().splitlines()[0]
    audio = read_wav(output)
    if audio.samples.shape[1] != info["channels"]:
        output.unlink()
        raise ValueError("转换改变了声道数，已拒绝该输出")
    delta = abs(audio.duration_s - info["duration_s"])
    # 无损/PCM 解码以首样本为相对零点；有损编码的起始延迟需人工核对。
    lossless = info["codec"] in {"pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "pcm_alaw", "pcm_mulaw", "flac"}
    verified = lossless and delta <= .02 and abs(info["start_time_s"]) <= .000001
    return audio, {"kind": "identity_relative_time" if verified else "requires_manual_alignment",
                   "verified": verified, "offset_s": 0.0 if verified else None, "duration_delta_s": delta,
                   "tolerance_s": .02, "command": command, "ffmpeg_version": version}


def load_for_inspection(path, raw=None):
    info = probe(path, raw)
    validate_info(info, info["sample_rate"])
    if info["native_pcm16"]:
        return read_wav(path), info
    with tempfile.TemporaryDirectory(prefix="voice-inspect-") as directory:
        audio, _ = decode(path, Path(directory) / "decoded.wav", info["sample_rate"], raw, info)
    return audio, info
