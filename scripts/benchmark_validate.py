#!/usr/bin/env python3
"""Localhost synthetic call -> portable task -> collected review. No Chinese quality claim."""
import argparse
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from tests.sip.fixtures import tone
from voice_tools.core.files import new_output, read_json, write_json
from voice_tools.tools.benchmark.templates import case
from voice_tools.tools.sip.scenario import load_scenario
from voice_tools.tools.sip.batch import load_queue
from voice_tools.tools.task import bundle, runner, review
from voice_tools.tools.task.contract import validate


def available_rtp():
    for port in range(26000, 45000, 2):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as a, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as b:
            try:
                a.bind(("127.0.0.1", port))
                b.bind(("127.0.0.1", port + 1))
                return port
            except OSError:
                continue
    raise RuntimeError("没有可用的本机RTP端口")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--browser-downloads", type=Path)
    parser.add_argument("--batch", action="store_true", help="经 sip batch 执行一项队列并生成汇总")
    args = parser.parse_args()
    output = new_output(args.out).resolve()
    source = output / "source"
    (source / "cases/audio").mkdir(parents=True)
    tone(source / "cases/audio/interrupt.wav", seconds=.4)
    if args.browser_downloads:
        for name, destination in (("interrupt.json", "cases/interrupt.json"), ("queue.json", "cases/queue.json"), ("task.json", "task.json")):
            shutil.copyfile(args.browser_downloads / name, source / destination)
        load_scenario(source / "cases/interrupt.json")
        load_queue(source / "cases/queue.json")
        validate(read_json(source / "task.json"))
    else:
        spec = case("interrupt", {"backend": "energy"})
        spec["steps"][2]["speech"] = [{"start_s": .1, "end_s": .3}]
        spec["steps"][3]["seconds"] = 1.8
        spec["benchmark"]["expectations"]["stop_max_ms"] = 600
        write_json(source / "cases/interrupt.json", spec)
        write_json(source / "task.json", {"schema_version": "1.0", "id": "timing-loopback", "title": "合成回环时序证据",
            "inputs": {"scenario": "cases/interrupt.json"}, "steps": [
                {"id": "call", "tool": "sip", "action": "run", "environment": "lab", "params": {"scenario": {"input": "scenario"}}},
                {"id": "timing", "tool": "benchmark", "action": "analyze", "depends_on": ["call"],
                 "params": {"run_dir": {"step": "call", "path": "data"}}}]})
    if args.batch:
        scenario = read_json(source / "cases/interrupt.json")
        write_json(source / "cases/queue.json", {"schema_version": "1.0", "kind": "sip_batch", "concurrency": 1,
            "rtp_port_base": available_rtp(), "jobs": [{"name": "合成回环", "repeat": 1, "scenario": scenario}]})
        write_json(source / "task.json", {"schema_version": "1.0", "id": "timing-batch", "title": "时序批次回环",
            "inputs": {"queue": "cases/queue.json"}, "steps": [
                {"id": "calls", "tool": "sip", "action": "batch", "environment": "lab", "params": {"queue": {"input": "queue"}}},
                {"id": "summary", "tool": "benchmark", "action": "summarize", "depends_on": ["calls"],
                 "params": {"batch_dir": {"step": "calls", "path": "data"}}}]})
    bundle.pack(source / "task.json", source, output / "task.vtask.zip")
    log = (output / "peer.log").open("w")
    peer = subprocess.Popen([sys.executable, "-m", "tests.sip.loopback_peer", "--out", str(output / "peer"),
                             "--mode", "timing_reply", "--duration", "25"], cwd=ROOT, stdout=log, stderr=log)
    try:
        deadline = time.monotonic() + 5
        ready = output / "peer/ready.json"
        while not ready.exists() and peer.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        if not ready.exists():
            raise RuntimeError("本机测试端未就绪；检查peer.log")
        port = read_json(ready)["sip_port"]
        write_json(output / "executor.json", {"schema_version": "1.0", "network_allowed": True, "environments": {"lab": {"sip": {
            "target_uri": f"sip:peer@127.0.0.1:{port}", "network": {"bind_address": "127.0.0.1", "sip_port": 0, "rtp_port": available_rtp()}}}}})
        # Delete the original inputs to prove the transferred archive is sufficient.
        shutil.rmtree(source)
        checked = runner.check(output / "task.vtask.zip", output / "executor.json")
        if not checked["ready"]:
            raise RuntimeError(json.dumps(checked, ensure_ascii=False))
        result = runner.run(output / "task.vtask.zip", output / "run", output / "executor.json")
        runner.collect(output / "run", output / "result.vresult.zip")
        page = review.review(output / "result.vresult.zip", output / "review")
        evidence = {"status": result["status"], "steps": [{"id": s["id"], "status": s["status"]} for s in result["steps"]],
                    "review": page["index"], "browser_downloads_validated": bool(args.browser_downloads),
                    "scope": "合成音频、127.0.0.1 SIP回环与跨目录迁移；无真人中文或外部线路"}
        write_json(output / "validation.json", evidence)
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
        return 0 if result["status"] in ("completed", "findings") else 1
    finally:
        if peer.poll() is None:
            peer.terminate()
        peer.wait(timeout=5)
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
