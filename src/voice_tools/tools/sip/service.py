"""Process orchestration with lazy dependencies and private per-run output."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path

from voice_tools.core.files import new_output, read_json, sha256, write_json
from .scenario import load_scenario, template
from .processes import termination_as_interrupt


def doctor():
    try:
        probe = subprocess.run([sys.executable, "-c", "import pjsua2 as p; print(p.Endpoint().libVersion().full)"],
                               capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("PJSUA2 原生模块探测超时") from exc
    ready = probe.returncode == 0
    return {"schema_version": "1.0", "python": sys.executable, "pjsua2": {"available": ready,
            "version": probe.stdout.strip().splitlines()[-1] if ready and probe.stdout.strip() else None,
            "error": None if ready else probe.stderr.strip()[-1500:]}, "ready_for_calls": ready,
            "tshark": shutil.which("tshark"), "sipp": shutil.which("sipp"), "network_accessed": False,
            "note": "PJSUA2 用于呼叫；tshark 用于 PCAP；SIPp 用于直接回放。未检查网关互通。"}


def initialize(output):
    output = new_output(output)
    write_json(output / "scenario.json", template())
    return {"status": "created", "scenario": str((output / "scenario.json").resolve()), "target_is_placeholder": True}


def stop_worker(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=7)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def run(scenario, output, dry_run=False):
    plan = load_scenario(scenario)
    if not dry_run:
        if "example.invalid" in plan["target_uri"]:
            raise ValueError("请先将示例 target_uri 替换为实际测试端点")
        if importlib.util.find_spec("pjsua2") is None:
            raise ValueError("当前 Python 未安装 PJSUA2；运行 sip doctor 并按 docs/sip.md 构建原生绑定")
        auth = plan["account"].get("auth")
        if auth and not os.environ.get(auth["password_env"]):
            raise ValueError("缺少认证环境变量：" + auth["password_env"])
    output = new_output(output).resolve()
    output.chmod(0o700)
    if not dry_run:
        # The worker and source-timeline reconstruction must use exactly the same
        # bytes, even if the operator later edits/removes an original recording.
        for step in plan["steps"]:
            audio = step.get("audio") or step.get("media", {}).get("audio")
            if audio is None:
                continue
            assets = output / "sources"
            assets.mkdir(exist_ok=True)
            snapshot = assets / (audio["sha256"] + ".wav")
            if not snapshot.exists():
                shutil.copyfile(audio["file"], snapshot)
            if sha256(snapshot) != audio["sha256"]:
                raise ValueError("播放素材在校验后发生变化；尚未拨号，请重新运行")
            audio["source_file"], audio["file"] = audio["file"], str(snapshot)
    write_json(output / "plan.json", plan)
    if dry_run:
        result = {"schema_version": "1.0", "status": "planned", "backend": "pjsua2", "network_accessed": False,
                  "target_uri": plan["target_uri"], "steps": len(plan["steps"]), "planned_duration_s": plan["planned_duration_s"]}
        result["assertions"] = {"status": "not_evaluated", "configured": plan.get("assertions", [])}
        write_json(output / "result.json", result)
        return result
    interrupted = timed_out = False
    with termination_as_interrupt(), (output / "native.log").open("wb") as log:
        process = subprocess.Popen([sys.executable, "-m", "voice_tools.tools.sip.worker", str(output / "plan.json"), str(output)],
                                   stdout=log, stderr=log)
        try:
            code = process.wait(timeout=2 * plan["connect_timeout_s"] + plan["max_call_s"] + 20)
        except subprocess.TimeoutExpired:
            timed_out = True
            stop_worker(process)
            code = process.returncode
        except KeyboardInterrupt:
            interrupted = True
            stop_worker(process)
            code = process.returncode
    result_file = output / "result.json"
    if result_file.exists():
        try:
            result = read_json(result_file)
            if not isinstance(result, dict) or result.get("status") not in ("completed", "failed", "interrupted"):
                raise ValueError("无效原生进程结果")
        except (ValueError, OSError) as exc:
            # Preserve malformed evidence before writing the parent's failure receipt.
            shutil.copyfile(result_file, output / "worker-result.invalid.json")
            result = {"schema_version": "1.0", "backend": "pjsua2", "status": "failed",
                      "error": {"code": "WORKER_RESULT_INVALID", "message": str(exc)}}
    else:
        result = {"schema_version": "1.0", "backend": "pjsua2", "status": "failed",
                  "error": {"code": "WORKER_EXITED", "message": "原生进程未写出完整结果；检查 native.log，现有文件保留"}}
    if interrupted or code != 0:
        result["status"] = "interrupted" if interrupted else "failed"
    if timed_out:
        result["status"] = "failed"
        result["error"] = {"code": "WORKER_TIMEOUT", "message": "原生进程超过执行时限；已终止并保留现有产物"}
    result["worker_exit_code"] = code
    try:
        with wave.open(str(output / "rx.wav"), "rb") as received:
            frames, rate = received.getnframes(), received.getframerate()
            if frames <= 0:
                raise ValueError("接收录音没有音频帧")
            if received.getnchannels() != 1 or received.getsampwidth() != 2 or rate != 8000 or received.getcomptype() != "NONE":
                raise ValueError("接收录音不是预期的单声道 8 kHz PCM16")
            remaining = frames
            while remaining:
                chunk = min(remaining, 65536)
                if len(received.readframes(chunk)) != chunk * 2:
                    raise ValueError("接收录音的数据被截断")
                remaining -= chunk
            result.setdefault("recording", {})["rx_duration_s"] = frames / rate
    except (OSError, EOFError, wave.Error, ValueError) as exc:
        if result["status"] == "completed" and not result.get("expected_rejection"):
            result["status"] = "failed"
            result["error"] = {"code": "RECORDING_INCOMPLETE", "message": str(exc)}
    from .assertions import apply_assertions
    apply_assertions(plan, result, output)
    # A native error must not cause a secret to escape through structured output.
    auth = plan["account"].get("auth")
    if auth and os.environ.get(auth["password_env"]):
        secret = os.environ[auth["password_env"]]
        def redact(value):
            if isinstance(value, str): return value.replace(secret, "[REDACTED]")
            if isinstance(value, dict): return {k: redact(v) for k, v in value.items()}
            if isinstance(value, list): return [redact(v) for v in value]
            return value
        result = redact(result)
    write_json(output / "assertions.json", result["assertions"])
    write_json(result_file, result)
    return result
