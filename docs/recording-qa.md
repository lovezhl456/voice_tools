# 双声道录音质检

[返回工具导航](../README.md)

## 快速开始

按 [README](../README.md#安装与命令导航) 安装后，先用合成场景体验：

```bash
voice-tools qa generate --out data/demo
voice-tools qa analyze data/demo --out outputs/demo --include-audio
open outputs/demo/report.html  # macOS；其他系统用浏览器打开同一文件
```

这些场景是正弦波和噪声，只验证工程规则，不代表生产准确率，也不会自动成为黄金集。每次使用新的输出目录。

处理自己的 **8–48 kHz、PCM16 WAV，最长 1 小时**：默认左轨用户、右轨 AI。先用已知内容的受控通话核实声道，再加 `--channels-verified`。

```bash
voice-tools qa analyze data/calls --out outputs/run-001 \
  --system-channel 1 --channels-verified --timeout 5

# 可选 CPU VAD（8/16/32/48 kHz），先安装 .[vad]
voice-tools qa analyze data/calls --out outputs/run-vad --backend webrtcvad
```

未提供事件文件时会输出 `acoustic_only` 候选。同名 `xxx.events.json` 可补充 AI 接管、应答机会、等待和打断；见下文与[事件示例](../examples/call.events.json)。

人工复核导出的 `review.csv` 后，可晋升标签并评估：

```bash
voice-tools qa promote outputs/run-001/review.csv \
  --results outputs/run-001/results.jsonl \
  --dataset-kind real --out data/golden/v1.json

voice-tools qa evaluate data/golden/v1.json \
  --results outputs/run-001/results.jsonl --out outputs/metrics-v1.json
```

复核人、带时区的时间和明确判断是标签晋升的必要信息。具体判定边界、输出和评估方式见下文。

## 输入与判定

这个工具寻找 **应答机会之后没有检测到 AI 轨输出，或输出晚于阈值** 的片段。输入是双声道 PCM16 WAV；声道 0 为左、1 为右。配置值必须对应真实录音系统的角色，不能凭声道位置推断身份。单声道返回 `INSUFFICIENT_EVIDENCE`，不尝试复制声道或做说话人分离。

先支持明确的 WAV 合同，避免隐式重采样或混音改变证据。其他格式可由外部 FFmpeg 转为 `pcm_s16le`，保留双声道和时间基准；不要把单声道转成伪双轨。

默认参数：20 ms 帧、−45 dBFS、最短活动 160 ms、合并停顿 300 ms、应答阈值 5 秒。它们是开发起点，需用你的线路和人工标签标定。能量检测去除帧均值，避免直流偏移被当作活动；不足最短活动的尖峰先过滤，再合并短停顿。

`--backend webrtcvad` 使用 CPU WebRTC VAD（需安装 `.[vad]`）。它能减少部分非语音活动的影响，但会漏掉轻声、短音节或受噪声干扰；它不是 ASR、说话人识别或语义模型。默认能量检测下的“无输出”是声学活动缺失；VAD 下是未检出语音活动。两者都只是候选。

## 两种证据等级

| 等级 | 条件 | 可以解释什么 |
|---|---|---|
| `acoustic_only` | 缺少显式应答事件、角色验证或 AI 接管时间之一 | 指定轨活动结束后的另一轨静默现象，需核对 IVR、角色和用户是否说完 |
| `event_aligned` | 有应答事件、已验证角色、已知 AI 接管时间 | 在业务确认的应答窗口中筛查缺失/延迟，仍需试听和链路日志 |

没有事件文件时，从用户轨活动结束生成 `auto-0001` 等机会；用户停顿不等于语义说完。缺少 AI 接管时间时会包含全通话范围，报告明确警告可能包含 IVR 或转接。

## 事件文件

`call.wav` 自动读取旁边的 `call.events.json`，示例见 [call.events.json](../examples/call.events.json)。所有时间均为**相对录音起点的秒数**，区间按 `[start_s, end_s)` 处理；不得混入 Unix 时间或另一份剪辑的时间轴。

- `schema_version`：当前为 `"1.0"`。
- `system_channel`：0 或 1。未指定 CLI 参数时从这里读取，否则默认 1；CLI 显式选择与事件不一致会报错。
- `channel_verified`：布尔值，表示已通过受控通话核实声道，不是自动检测出来的。
- `ai_start_s` / `ai_end_s`：AI 接管/退出时间；未给 end 则使用录音末尾。事件中的时间优先于 `--ai-start`。
- `opportunities`：业务日志或人工确认的应答机会，`id` 唯一、`at_s` 为用户说完/首次问候的计时起点。可设 `expects_response: false`，可设 `window_end_s` 限制观察终点。
- `user_speech`：人工或业务提供的用户讲话区间，用来截断等待窗口；若提供则采用该列表，缺省才从音频估算。
- `exclusions`：按业务规则应排除的区间，例如用户已获告知的工具等待，建议附 `reason`。

不提供 `opportunities` 字段时推导机会；显式 `"opportunities": []` 表示已确认没有应答机会，不会重新推导。进入新的应答机会、用户打断、等待区间或 AI 退出时，上一窗口结束，不拿后一轮回答补前一轮漏答。

## 结果含义

| 状态 | 解释 |
|---|---|
| `NO_OUTPUT_CANDIDATE` | 完整观察到超时阈值，未检测到系统轨活动 |
| `LATE_OUTPUT_CANDIDATE` | 首次系统轨活动的延迟达到或超过阈值 |
| `OUTPUT_NEEDS_REVIEW` | 阈值内有活动；可能是噪声、音乐、残留提示音，需要试听 |
| `CENSORED` | 在阈值前发生挂机、打断、等待或录音结束，不能按完整超时判定 |
| `EXCLUDED` | 机会不在应答阶段、落在排除区间，或明确不要求回复 |
| `INSUFFICIENT_EVIDENCE` | 单轨或重复声道，无法按独立双方轨道解释 |
| `NO_OPPORTUNITIES` | 未获得应答机会；不是“质检通过” |

持续底噪在能量检测下仍会产生 `OUTPUT_NEEDS_REVIEW`，不能解释为“AI 已回答”。重复声道包括两轨都静音；即使活动不同，也可能存在串音。当前不自动判断相关性串音，不从波形判定 LLM、TTS、RTP、播放或录音链路的责任。

## 批量命令与报告

```bash
voice-tools qa analyze data/calls data/another.wav \
  --out outputs/run-002 --timeout 5 --include-audio
```

目录递归扫描 `.wav`（大小写均可），重复的输入路径只处理一次。每个文件独立处理，损坏录音/事件文件不会中断其他文件。当前逐文件串行，避免并发加载长录音放大内存；单文件最长 3600 秒，且 float32 样本数组不超过 512 MiB。总进程内存还包含原始 PCM 和检测临时数组；高采样率长通话会提前拒绝，需按业务事件分段或先重采样。

输出包含：

| 文件 | 用途 |
|---|---|
| `run.json` | 版本、执行时间、文件/错误/候选统计 |
| `results.jsonl` | 逐录音完整结果、参数、事件快照、输入/事件 SHA-256 |
| `summary.csv` | 逐文件汇总 |
| `review.csv` | 逐应答机会的人工复核表，判断列默认留空 |
| `report.html` | 中文可读报告，附带音频时可以试听对应窗口 |
| `audio/` | 仅在 `--include-audio` 时复制原始录音，按摘要命名避免同名覆盖 |

报告记录本机原路径。音频只在显式指定时复制；分享报告前应自行确认不包含需要保密的路径、业务日志或录音。工具本身不上传任何内容。

退出码：`0` 完成（可含候选）；`1` 仅在 `--fail-on-findings` 且有候选；`2` 参数或整体输入错误；`3` 批次中部分文件处理失败（优先于候选退出码）。输出目录必须为空或不存在。

## 合成回归集

```bash
voice-tools qa generate --out data/demo-16k --sample-rate 16000 --seed 20260916
```

包含正常活动、无声、迟答、长用户发言、挂机、工具等待、打断、尖峰、持续噪声、直流偏移、轻声、未知角色、未知接管时间、重复声道、声道互换、首次问候、无需回复、多轮、无事件、单轨等 20 个场景。`manifest.json` 的预期是人工编写的工程规则预期，生成器不调用检测器计算答案。

它们主要是正弦波，不具备真人语音统计特征；VAD 不应拿这些场景的能量预期当准确率指标。回归测试在 8k/16k 下校验能量规则，VAD 单独做接口/采样率测试。

## 人工复核与黄金集

1. 使用 HTML 试听或原始录音核实每个机会，并结合已授权取得的业务日志。
2. 编辑 `review.csv`：保留 `sample_id`、`audio_sha256`、机会 ID、时间窗口；填写 `decision`、`reviewer`、`reviewed_at`、可选 `notes`。
3. 判断可用 `missing`（缺失）、`delayed`（迟答）、`audible`（有可听回答）、`exclude`（无需应答）、`uncertain`（无法判断）。复核时间示例 `2026-09-16T23:00:00+08:00`。
4. 执行 `qa promote`。空白行跳过，半填/重复/摘要不符/窗口改变/没有复核人或时区的行拒绝晋升。
5. 为新版本运行分析，再使用 `qa evaluate` 评估同一黄金标签集。

`sample_id` 由音频摘要与事件文件摘要生成；同一音频的不同业务事件不能互相冒用标签。未提供事件的自动机会编号会随检测器参数变化，因此晋升稳定黄金集前建议将人工确认的机会固化为事件文件，并重新分析、复核。评估遇到缺少预测或窗口改变会报错，不能静默缩小样本量。

指标是候选二分类：`missing/delayed` 为正例，`audible` 为负例，`exclude/uncertain` 明示计数但不计分。报告输出 TP/FP/FN/TN、precision、recall；分母为零的指标为 `null`。有活动却无有效回答的噪声可能成为假阴性，需要通过真实标注发现并调节检测方法。

`--dataset-kind synthetic` 与 `real` 必须明确选择；人工复核过的合成声音仍是合成数据。本仓库不交付伪造的人工黄金标签。真实黄金集需要补齐不同线路、音量、噪声、编解码和业务等待策略的样本，最好独立复核分歧并保留版本。
