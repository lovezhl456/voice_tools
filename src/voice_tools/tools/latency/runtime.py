"""Local integrity checks and process isolation; no implicit installation."""
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile

from voice_tools.core.files import read_json, sha256

ROOT = Path(__file__).parent
RESOURCES = ROOT / "resources"
DEFAULT_PREFIX = Path("~/.local/share/voice-tools/latency")


def prefix(directory=None):
    selected = directory or os.environ.get("VOICE_TOOLS_LATENCY_DIR") or DEFAULT_PREFIX
    return Path(selected).expanduser().resolve()


def manifest():
    data = read_json(RESOURCES / "manifest.json")
    for name, expected in data["resources"].items():
        if sha256(RESOURCES / name) != expected:
            raise ValueError(f"安装资源校验失败：{name}")
    return data


def environment(cache):
    env = {key: value for key, value in os.environ.items() if not key.startswith(("PYTHON", "NUMBA", "OMP_", "OPENBLAS_", "MKL_"))}
    env.update(OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1",
               XDG_CACHE_HOME=str(cache), NUMBA_CACHE_DIR=str(cache / "numba"),
               MPLCONFIGDIR=str(cache / "matplotlib"), TMPDIR=str(cache))
    return env


def stop_process(process):
    # A failed worker may exit before its children; always address the entire group.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        process.wait()
        return
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=2)


def invoke(directory, args, timeout):
    with tempfile.TemporaryDirectory(prefix="voice-tools-latency-") as folder:
        cache = Path(folder)
        # Engine is a separate session so either parent cancellation path can terminate all descendants.
        with (cache / "stdout").open("w+") as stdout, (cache / "stderr").open("w+") as stderr:
            command = [str(directory / "venv/bin/python"), "-I", str(directory / "worker.py"), str(directory)] + args
            process = subprocess.Popen(command, stdout=stdout, stderr=stderr, env=environment(cache), start_new_session=True)
            try:
                process.wait(timeout=timeout)
            except BaseException:
                stop_process(process)
                raise
            stop_process(process)
            stdout.seek(0); stderr.seek(0)
            result = stdout.read(128 * 1024)
            error = stderr.read(16 * 1024)
            if process.returncode:
                raise ValueError(f"引擎退出 {process.returncode}：{error[-4000:]}")
            return json.loads(result) if result else {}


def doctor(directory=None):
    directory = prefix(directory)
    issues = []
    info = {"ready": False, "directory": str(directory), "issues": issues, "network_accessed": False,
            "checked": "integrity_and_import_only", "analysis_performed": False}
    try:
        expected = manifest()
        ready = read_json(directory / "ready.json")
        if not isinstance(ready, dict):
            raise ValueError("就绪记录必须是 JSON 对象")
        if ready.get("manifest") != expected or ready.get("prefix") != str(directory):
            raise ValueError("就绪记录的版本或安装前缀不匹配；虚拟环境不可迁移")
        if sha256(directory / "source/src/latency_checker/detector.py") != expected["patched_detector_sha256"]:
            raise ValueError("引擎源码校验失败")
        if sha256(directory / "worker.py") != sha256(ROOT / "worker.py"):
            raise ValueError("引擎协议适配器校验失败，请使用本工具版本的新安装前缀")
        if sha256(directory / "source/LICENSE") != expected["resources"]["upstream-LICENSE.txt"]:
            raise ValueError("上游许可校验失败")
        probe = invoke(directory, ["--doctor"], 30)
        if probe.get("numpy") != "2.2.6" or probe.get("python", [])[:2] not in ([3, 11], [3, 12]):
            raise ValueError("引擎 Python/NumPy 版本不匹配")
        if probe.get("machine") != ready.get("machine"):
            raise ValueError("引擎架构与就绪记录不匹配")
        info.update(ready=True, provenance=expected, runtime=probe)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        issues.append(str(error))
    return info
