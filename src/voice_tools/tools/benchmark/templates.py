"""Chinese case scaffolds explicitly require authorized recordings."""
from pathlib import Path

from voice_tools.core.files import new_output, write_json
from .config import DEFAULTS

CASES = (
    ("greeting", "开场等待", None, []),
    ("response", "普通问答", "question.wav", ["帮我查一下上周的订单"]),
    ("interrupt", "明确抢话", "interrupt.wav", ["等一下", "不是这个", "先别说了"]),
    ("backchannel", "短促附和", "backchannel.wav", ["嗯", "对", "好的"]),
    ("hesitation", "句中停顿", "hesitation.wav", ["我想查一下……上周那一笔"]),
)


def case(kind, detector=None):
    from voice_tools.tools.sip.scenario import template
    selected = next((r for r in CASES if r[0] == kind), None)
    if selected is None:
        raise ValueError("未知中文时序模板")
    name, title, filename, phrases = selected
    spec = template()
    spec.update(schema_version="1.1", max_call_s=60)
    spec["benchmark"] = {"case_id": name, "language": "zh-CN", "tags": [title, "普通话", "待人工复核"],
                         "detector": dict(DEFAULTS, **(detector or {})), "windows": [], "expectations": {}}
    active = {"action": "wait_audio", "state": "active", "timeout_s": 20, "duration_ms": 60}
    silent = {"action": "wait_audio", "state": "silent", "timeout_s": 20, "duration_ms": 200}
    steps = [active]
    if kind == "greeting":
        steps += [silent, {"action": "wait", "seconds": 1}]
    else:
        if kind in ("interrupt", "backchannel"):
            steps.append({"action": "wait", "seconds": 1})
            spec["benchmark"]["expectations"]["expect_interrupt"] = kind == "interrupt"
        else:
            steps.append(silent)
        steps += [{"action": "play", "file": "audio/" + filename}, {"action": "wait", "seconds": 8}]
    spec["steps"] = steps + [{"action": "hangup"}]
    return spec


def initialize(output):
    output = new_output(output)
    (output / "audio").mkdir()
    rows = []
    for kind, title, filename, phrases in CASES:
        write_json(output / f"{kind}.json", case(kind))
        rows.append({"case": kind, "title": title, "status": "待填写测试端点" if filename is None else "待补素材",
                     "file": "audio/" + filename if filename else None, "phrases": phrases,
                     "confirmed_speech_intervals": "在 play 步骤的 speech 中填写源录音 start_s/end_s；未填写时使用活动检测候选"})
    write_json(output / "recording-checklist.json", {"schema_version": "1.0", "items": rows,
        "requirements": ["8kHz 单声道 PCM16 WAV", "获授权真人录音", "注明口音、音量、噪声标签", "附和或抢话预期需要结合上下文人工定义"]})
    write_json(output / "queue.json", {"schema_version": "1.0", "kind": "sip_batch", "concurrency": 1,
        "jobs": [{"name": title, "repeat": 3, "scenario": case(kind)} for kind, title, _, _ in CASES]})
    (output / "README.md").write_text(
        "# 中文时序测试素材\n\n此目录不含真人录音，播放用例当前为待补素材。\n\n"
        "1. 依据 recording-checklist.json 放入获授权的音频，保持 8kHz 单声道 PCM16。\n"
        "2. 修改 target_uri 和执行端账号；密码通过环境变量提供。\n"
        "3. 可在 play 步骤增加 speech 数组，例如 [{\"start_s\":0.12,\"end_s\":0.8}]，时间相对于该源录音。\n"
        "4. 运行 voice-tools sip validate 场景.json；再使用 sip run/batch，或任务工作台打包执行。\n"
        "5. benchmark analyze 运行目录 --out 分析目录 可离线重算；summarize 批次目录 --out 汇总目录 可分组汇总。\n\n"
        "默认无业务时延合格阈值。配置 expectations 后才能产生时序断言；未配置不代表业务通过。\n",
        encoding="utf-8")
    return {"status": "created", "directory": str(output.resolve()), "cases": 5, "recordings": "待补素材"}
