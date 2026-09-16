# voice_tools

本地运行、CPU 优先的语音工具集。统一使用 `voice-tools <工具> <操作>`，各工具独立维护规则、配置和测试。

首个工具是 **双声道热线录音质检**：筛查用户轨活动结束后 AI 轨无声或延迟输出的候选片段，支持批处理、可试听报告、合成回归集和人工黄金标签。它不判断回答语义，也不直接判定模型或媒体链路故障。

## 安装

Python 3.9+，无需 GPU 或 API Key。建议使用独立虚拟环境：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
voice-tools --help
```

默认只依赖 NumPy，采用能量活动检测。可选的轻量语音检测：

```bash
python -m pip install -e '.[vad]'
```

## 跑通第一个工具

```bash
# 20 种可复现的合成场景；正弦波/噪声，不是真人录音
voice-tools qa generate --out data/demo

# 批量生成 JSONL、CSV、HTML；附带音频后可离线试听
voice-tools qa analyze data/demo --out outputs/demo --include-audio

# macOS 打开本地报告；其他系统用浏览器打开同一文件
open outputs/demo/report.html
```

每次使用新的输出目录，避免覆盖已有结果。合成场景只验证时间窗口和工程规则，不能当作生产准确率或自动成为黄金集。

## 处理自己的录音

输入为 **8–48 kHz、PCM16 WAV，最长 1 小时**。默认左轨用户、右轨 AI；可通过事件文件或参数调整。先用已知内容的受控通话核实声道，再使用 `--channels-verified`。

```bash
voice-tools qa analyze data/calls --out outputs/run-001 \
  --system-channel 1 --channels-verified --timeout 5

# 可选 CPU VAD（8/16/32/48 kHz），仍不具备语义判断能力
voice-tools qa analyze data/calls --out outputs/run-vad --backend webrtcvad
```

无事件文件也会输出候选，但证据级别是 `acoustic_only`。同名 `xxx.events.json` 可以提供 AI 接管时间、应答机会、等待和用户打断区间，使结果更可解释。见 [事件示例](examples/call.events.json) 与 [完整使用说明](docs/recording-qa.md)。

人工复核导出的 `review.csv` 后，可晋升标签并评估：

```bash
voice-tools qa promote outputs/run-001/review.csv \
  --results outputs/run-001/results.jsonl \
  --dataset-kind real --out data/golden/v1.json

voice-tools qa evaluate data/golden/v1.json \
  --results outputs/run-001/results.jsonl --out outputs/metrics-v1.json
```

未填写复核人、带时区的时间及明确判断的记录不会成为黄金数据。测试自动结果与人工黄金标签分开保存。

## 代码结构

```text
src/voice_tools/
├── cli.py                  # 统一入口
├── audio/                  # 音频读取、活动检测
├── core/                   # 文件、摘要和输出保护
└── tools/
    └── recording_qa/       # 检测 / 批处理 / 报告 / 合成 / 人工标签
tests/                      # 公共层和工具分别测试
docs/                       # 架构与各工具手册
examples/                   # 小型、无真实音频的配置示例
```

新增工具放进 `tools/<工具名>/`，注册自己的子命令；公共模块不能反向依赖具体工具。详细边界、扩展方式和阶段计划见 [架构规划](docs/architecture.md)。

## 开发与验证

```bash
python -m unittest discover -s tests -v
```

测试涵盖 20 种场景、8k/16k 采样、损坏输入、事件校验、输出保护、批量错误隔离、人工标签晋升及评估；安装 VAD extra 后额外执行 CPU VAD 测试。

提供 [Python 3.9 / 3.12 / 3.13 的 CI 模板](docs/ci.example.yml)，目前未启用。具备 GitHub workflow 写入权限后，可将其放到 `.github/workflows/tests.yml`；本地测试不依赖该权限。

`data/`、`outputs/`、真实音频、虚拟环境都不提交。报告默认仅包含分析；显式 `--include-audio` 会复制原录音，请将其与原录音按相同方式保管。
