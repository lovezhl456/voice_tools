"""Bounded functional-call queue. Each job owns a CLI process and a port slot."""
import csv
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from voice_tools.core.files import new_output, read_json, write_json
from .processes import termination_as_interrupt
from .scenario import load_scenario, number, object_fields, text


def resolve_scenario(source, base):
    """Validate embedded scenarios with the existing file/media contract."""
    source = json.loads(json.dumps(source, allow_nan=False))
    for step in source.get("steps", []) if isinstance(source, dict) else []:
        if isinstance(step, dict) and step.get("action") in ("play", "play_media"):
            path = Path(text(step.get("file"), "素材路径", 4096)).expanduser()
            step["file"] = str((base / path).resolve())
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "scenario.json"
        write_json(path, source)
        plan = load_scenario(path)
    return source, plan


def load_queue(path):
    path = Path(path).resolve()
    data = read_json(path)
    object_fields(data, {"schema_version", "kind", "concurrency", "sip_port_base", "rtp_port_base", "jobs"}, "batch")
    if data.get("schema_version") != "1.0" or data.get("kind") != "sip_batch":
        raise ValueError("批量文件必须是 schema_version=1.0、kind=sip_batch")
    limit = number(data.get("concurrency", 1), 1, 32, "concurrency", True)
    sip = number(data.get("sip_port_base", 0), 0, 65535, "sip_port_base", True)
    rtp = number(data.get("rtp_port_base", 4000), 1024, 65000, "rtp_port_base", True)
    if (sip and sip < 1024) or rtp % 2 or rtp + 4 * (limit - 1) > 65000 or rtp + limit * 4 - 1 > 65535 or sip + limit - 1 > 65535:
        raise ValueError("端口池无效：SIP 可为 0（自动分配）；RTP 须为偶数且池不能越界")
    if sip and set(range(sip, sip + limit)) & set(range(rtp, rtp + 4 * limit)):
        raise ValueError("SIP 与 RTP/RTCP 端口池不能重叠")
    entries = data.get("jobs")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 100:
        raise ValueError("jobs 必须包含 1–100 项")
    jobs = []
    for i, entry in enumerate(entries):
        object_fields(entry, {"name", "repeat", "scenario"}, f"jobs[{i}]")
        name = text(entry.get("name", f"用例 {i + 1}"), "name", 160)
        repeat = number(entry.get("repeat", 1), 1, 1000, "repeat", True)
        source, plan = resolve_scenario(entry.get("scenario"), path.parent)
        for n in range(repeat):
            jobs.append({"id": f"job-{len(jobs) + 1:05d}", "name": name, "iteration": n + 1,
                         "scenario": source, "timeout_s": 2 * plan["connect_timeout_s"] + plan["max_call_s"] + 40})
        if len(jobs) > 10000:
            raise ValueError("展开后的批量任务上限为 10000 通")
    return {"concurrency": limit, "sip_port_base": sip, "rtp_port_base": rtp, "jobs": jobs}


def stop_process(process):
    """Allow service.py to flush receipts, then kill the entire owned process group."""
    if process.poll() is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.poll() is None:
        process.kill()
    process.wait()


def run_queue(path, output, dry_run=False):
    plan = load_queue(path)  # Preflight every scenario before any call is started.
    output = new_output(output).resolve()
    output.chmod(0o700)
    rows = [{k: job[k] for k in ("id", "name", "iteration")} | {"status": "pending"} for job in plan["jobs"]]
    result = {"schema_version": "1.0", "kind": "sip_batch_result", "status": "planned" if dry_run else "running",
              "backend": "pjsua2", "concurrency": plan["concurrency"], "network_accessed": False, "jobs": rows}

    def save():
        result["counts"] = {s: sum(row["status"] == s for row in rows)
                            for s in ("pending", "running", "planned", "completed", "failed", "cancelled", "interrupted")}
        tmp = output / "batch-result.tmp"
        write_json(tmp, result)
        tmp.replace(output / "batch-result.json")

    def scenario(job, slot):
        spec = json.loads(json.dumps(job["scenario"]))
        net = spec.setdefault("network", {})
        net["sip_port"] = plan["sip_port_base"] + slot if plan["sip_port_base"] else 0
        net["rtp_port"] = plan["rtp_port_base"] + 4 * slot
        return spec

    active, next_job = {}, 0
    save()
    try:
        with termination_as_interrupt():
            while next_job < len(rows) or active:
                for slot in range(plan["concurrency"]):
                    if slot in active or next_job >= len(rows):
                        continue
                    job, row = plan["jobs"][next_job], rows[next_job]
                    next_job += 1
                    job_dir = output / job["id"]
                    job_dir.mkdir(mode=0o700)
                    write_json(job_dir / "scenario.json", scenario(job, slot))
                    row.update(slot=slot, output=f"{job['id']}/run", scenario=f"{job['id']}/scenario.json")
                    if dry_run:
                        row["status"] = "planned"
                        continue
                    log = (job_dir / "cli.log").open("wb")
                    try:
                        process = subprocess.Popen([sys.executable, "-m", "voice_tools", "--json", "sip", "run",
                                                    str(job_dir / "scenario.json"), "--out", str(job_dir / "run")],
                                                   stdout=log, stderr=log, start_new_session=True)
                    except OSError as exc:
                        log.close()
                        row.update(status="failed", error=str(exc))
                        continue
                    active[slot] = (process, log, row, time.monotonic(), job["timeout_s"])
                    row["status"] = "running"
                    result["network_accessed"] = True  # Attempted execution, not proof of SIP delivery.
                save()
                for slot, (process, log, row, started, timeout) in list(active.items()):
                    expired = time.monotonic() - started > timeout
                    if process.poll() is None and not expired:
                        continue
                    stop_process(process)
                    log.close()
                    row.update(exit_code=process.returncode, duration_s=round(time.monotonic() - started, 3))
                    try:
                        receipt = read_json(output / row["output"] / "result.json")
                        if not isinstance(receipt, dict) or receipt.get("status") not in ("completed", "failed", "interrupted"):
                            raise ValueError("子任务结果结构无效")
                        row["result"] = receipt
                        row["status"] = "completed" if process.returncode == 0 and receipt["status"] == "completed" else "failed"
                    except (OSError, ValueError) as exc:
                        row.update(status="failed", error=f"子任务未返回有效结果：{exc}")
                    if expired:
                        row.update(status="failed", error="子任务超过执行时限，已清理进程")
                    del active[slot]
                    save()
                if active:
                    time.sleep(.05)
    except KeyboardInterrupt:
        result["status"] = "interrupted"
    finally:
        for process, log, row, started, _ in active.values():
            stop_process(process)
            log.close()
            row.update(status="interrupted", exit_code=process.returncode,
                       duration_s=round(time.monotonic() - started, 3))
        for row in rows:
            if row["status"] == "pending":
                row["status"] = "cancelled"
        if result["status"] == "running":
            result["status"] = "completed" if all(row["status"] == "completed" for row in rows) else "failed"
        save()
        with (output / "summary.csv").open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.writer(stream)
            writer.writerow(["id", "name", "iteration", "status", "exit_code", "output"])
            for row in rows:
                values = [row.get(k, "") for k in ("id", "name", "iteration", "status", "exit_code", "output")]
                writer.writerow(["'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@")) else v for v in values])
    return result
