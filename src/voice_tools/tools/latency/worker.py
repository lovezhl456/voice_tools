"""Standalone protocol worker, copied into the external prefix; only NumPy is needed.

Load the pinned detector module directly, avoiding upstream's eager librosa/web/mono
imports. No voice_tools package or host PYTHONPATH is used by this interpreter.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import resource
import sys
import time
import wave

import numpy as np


def detector(prefix):
    path = prefix / "source/src/latency_checker/detector.py"
    spec = importlib.util.spec_from_file_location("voice_tools_pinned_detector", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.FinalDetector


def analyze(prefix, request):
    started = time.monotonic()
    path = Path(request["input"])
    with wave.open(str(path), "rb") as reader:
        rate, count = reader.getframerate(), reader.getnframes()
        if reader.getnchannels() != 2 or reader.getsampwidth() != 2 or reader.getcomptype() != "NONE":
            raise ValueError("engine requires stereo PCM16 WAV")
        if rate not in (8000, 16000, 24000, 32000, 44100, 48000) or not 0 < count / rate <= 3600 or count * 2 * 4 > 512 * 1024**2:
            raise ValueError("unsupported rate, duration or decoded size")
        # Allocate one float32 interleaved array; stream decoding bounds transient copies.
        samples = np.empty((count, 2), dtype=np.float32)
        digest = hashlib.sha256()
        offset = 0
        while offset < count:
            raw = reader.readframes(min(65536, count - offset))
            if not raw or len(raw) % 4:
                raise ValueError("truncated WAV samples")
            digest.update(raw)
            block = np.frombuffer(raw, dtype="<i2").reshape(-1, 2)
            samples[offset:offset + len(block)] = block
            offset += len(block)
        samples /= 32768.0
    identical = bool(np.array_equal(samples[:, 0], samples[:, 1]))
    silent = not bool(np.any(samples))
    system = 0 if request["system_channel"] == "left" else 1
    result = detector(prefix)(sample_rate=rate, **request["parameters"]).detect_turns(samples[:, system], samples[:, 1 - system])
    # Store extrema for both tracks, independent of display page size; no extra full-size array.
    width = max(1, (count + 1599) // 1600)
    waveform = []
    for channel in range(2):
        waveform.append([[round(float(np.min(samples[start:start + width, channel])), 5),
                          round(float(np.max(samples[start:start + width, channel])), 5)] for start in range(0, count, width)])
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {"protocol_version": "1.0", "detection": result, "sample_rate": rate, "frames": count,
            "duration_s": count / rate, "pcm_sha256": digest.hexdigest(), "identical_channels": identical,
            "silent": silent, "waveform": {"tracks": waveform, "bin_frames": width, "sample_rate": rate},
            "resources": {"wall_seconds": time.monotonic() - started,
                          "peak_rss_bytes": int(usage if sys.platform == "darwin" else usage * 1024),
                          "rss_scope": "isolated_engine_process", "machine": platform.machine()}}


def main():
    prefix = Path(sys.argv[1])
    if sys.argv[2] == "--doctor":
        detector(prefix)
        print(json.dumps({"python": list(sys.version_info[:3]), "numpy": np.__version__, "machine": platform.machine()}))
    else:
        request = json.loads(Path(sys.argv[2]).read_text())
        result = analyze(prefix, request)
        Path(sys.argv[3]).write_text(json.dumps(result, allow_nan=False))
        print(json.dumps({"protocol_version": "1.0", "written": True}))


if __name__ == "__main__":
    main()
