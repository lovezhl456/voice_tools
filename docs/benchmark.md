# 中文电话时序评测

适用于 voice_tools 0.13.1 及以上。首版通过 SIP／FreeSWITCH 的 PCMA／PCMU 链路播放获授权中文真人录音，测量本机媒体桥上的声学时序。ASR 准确率、中文意图、业务正确性、AudioSocket 和宽带电话不在本版范围内。

## 从素材到结果

```sh
voice-tools benchmark init --out cases
# 将获授权录音放入 cases/audio，并填写场景的测试端点、speech 和标签
voice-tools sip validate cases/interrupt.json
voice-tools sip run cases/interrupt.json --dry-run --out preview
voice-tools sip run cases/interrupt.json --out call
voice-tools benchmark analyze call --out reanalysis
voice-tools sip batch cases/queue.json --out batch
voice-tools benchmark summarize batch --out summary
```

每个输出目录必须不存在。模板生成五类场景：开场等待、普通问答、明确抢话、短促附和、句中停顿。`recording-checklist.json` 列出“等一下”“嗯”“对”“我想查一下……上周那一笔”等素材；文件未提供时明确为**待补素材**，播放场景的校验会失败。开场模板无需发送录音。

音频必须是 **8kHz、单声道、PCM16 WAV**；其他采样率会拒绝，不静默降级。保留原始录音，用现有音频工具另行转换，人工核对转换后的有效语音区间。不得用合成回环结果代替真人中文验收。

生成的 `queue.json` 内嵌场景，与单次 JSON 相互独立；修改单次文件后，也要更新队列中的场景。工作台导出的队列使用当前编辑内容。

## 场景 1.1

旧 `schema_version=1.0` 的行为和退出码保持兼容。1.1 的 `benchmark` 为可选配置；启用后支持 `wait_audio` 和播放步骤的 `speech`：

机器客户端通过 `data_contracts.sip_scenario_read=["1.0","1.1"]` 判断可读版本。普通 SIP 模板的 `sip_scenario_write` 为 `1.0`，中文时序模板的 `benchmark_scenario_write` 为 `1.1`；旧字段 `sip_scenario` 保留为普通模板写出版本的兼容别名，不代表完整读取能力。

```json
{
  "schema_version": "1.1",
  "target_uri": "sip:test@example.invalid",
  "codec": "PCMA",
  "max_call_s": 60,
  "benchmark": {
    "case_id": "zh-interrupt-001",
    "language": "zh-CN",
    "tags": ["普通话", "正常音量", "安静", "明确抢话"],
    "detector": {
      "backend": "webrtcvad",
      "threshold_db": -45,
      "minimum_ms": 60,
      "join_gap_ms": 100,
      "stop_silence_ms": 200
    },
    "windows": [],
    "expectations": {"expect_interrupt": true, "stop_max_ms": 600}
  },
  "steps": [
    {"action": "wait_audio", "state": "active", "duration_ms": 60, "timeout_s": 20},
    {"action": "play", "file": "audio/interrupt.wav", "speech": [{"start_s": 0.12, "end_s": 0.8}]},
    {"action": "wait", "seconds": 5},
    {"action": "hangup"}
  ]
}
```

- `wait_audio.state` 为 `active` 或 `silent`；持续时间从该动作开始计算，仅新鲜 RX 帧能触发。全局通话超时和操作者停止仍有效。
- `speech` 是人工确认的**源 WAV 时间**，必须有序、不重叠且处于素材范围内。省略时使用活动检测候选，空数组表示没有有效语音。
- `windows` 用运行单调时钟秒指定，例如 `[{"id":"opening","start_s":1,"end_s":5}]`；空数组使用接通后的可用接收范围，越界窗口标为证据不足。
- `first_audio_max_ms`、`response_max_ms`、`stop_max_ms` 是可选声学阈值，模板不替业务擅设时延合格线。`expect_interrupt=true` 要求有效重叠和已确认停声；`false` 标记需人工判断，不能把自然停顿推断为误打断。
- 默认 WebRTC VAD 模式 2、20ms 分析帧，同时施加 -45dBFS 能量门限；最短活动 60ms、合并间隔 100ms、确认停声静音 200ms。后四项可调并随结果保存。这些是待中文录音校准的工程初值。`energy` 后端适合合成回归，不代表中文语音检测质量。

离线发现、生成模板、`sip validate` 和预演不需要 PJSUA2／WebRTC 原生依赖。真实拨号使用 PJSUA2，默认检测还需要 `pip install -e '.[vad]'`；缺依赖时在拨号前阻止执行。SIPp 压力导出拒绝时序场景，不静默丢弃测量或触发动作。

## 指标及证据边界

| 指标 | 定义与不能判断的情况 |
|---|---|
| `first_audio` | 接通且媒体可用至首段接收声音。早期媒体另列；没有首声是证据不足。 |
| `response` | 某段有效用户语音结束至之后可分离的新接收声音起点。持续中的旧声音不算新回答；未出现新起点时无法精确测量。 |
| `longest_silence` | 指定窗口的最长连续静音，并给出静音总量。连续到来的全零 PCM 仍是静音。 |
| `barge_stop` | 用户起声时确实与接收活动重叠，至旧声音片段结束，需后续连续静音确认；再次出声另记 `resumed_at_s`。无起始重叠的插话场景无效。 |

片段按实际样本数计时；消息长短不决定固定 20ms 时长。未确认停声只给已观察活动的下界，不能当成成功打断。挂断／清理阶段的静音不参与停声成功判定。声音片段不等于语义轮次，用户的句中停顿可能被分成多个声学片段，须人工复查。

新增文件：

| 文件 | 内容 |
|---|---|
| `bridge_rx.wav` | 抖动缓冲后的本机接收媒体桥 PCM |
| `bridge_tx.wav` | 发送素材回调实际提供给本机媒体桥的 PCM；多个播放段按样本拼接 |
| `media-frames.jsonl` | 方向、样本位置、单调时钟、序号、媒体代次和源播放步骤／样本位置 |
| `media-observation.json` | 格式、完整性、队列丢帧、媒体重建与时钟间断 |
| `benchmark.json` | 配置、指标、声音片段、早期媒体、执行状态、无效原因和证据 SHA-256 |

`rx.wav` 与 `tx_source.wav` 保留原义；后者仍是基于调度时刻的素材参考轨，不能替代新观测。TX 桥回调不证明 RTP 上线、远端收到或扬声器发声；RX 回调间隔不能用来计算线上的 RTP 到包抖动。需要这些指标时保留原始 PCAP 另测。

回调仅复制 PCM、时间和来源并入有界队列；VAD、JSON 和 WAV 写入在工作循环中执行。丢帧、写入失败、媒体重建、时间轴中断或音频截断会禁止精确时延结果。原始音频仍保留供人工排查。报告摘要固定绑定输入证据，`result.json` 因随后附加断言形成循环，不包含在摘要表内。

配置阈值后，时序断言附加到既有 `assertions.json`；证据不足不能当成通过。执行失败原因不被时序断言覆盖。`benchmark analyze` 的退出码为完成 0、超限 1、证据不足 3。批量汇总保留有效、失败、无效、证据不足四类计数，并按标签分组；分位数标明有效**测量数**，包含失败通话中仍有效的数值，小样本 P95/P99 仅是经验统计。

`benchmark analyze/summarize` 返回 1 且提供有效的 `findings` 机器回执时，任务执行器将步骤记为 `findings`，允许后续依赖步骤继续处理产物；真实输入错误返回 2，仍记为失败。进程崩溃或回执损坏不会因退出码同为 1 而被当成超限报告。批次汇总要求合法的 `sip_batch_result` 1.0 回执、状态和非空任务数组，空对象或损坏结构不会生成成功汇总。取消／中断且尚未产生输出的任务保留在统计分母内；合法回执中的音频证据缺失仍按失败或证据不足计入。

## 任务包与工作台

```sh
voice-tools task workbench --out workbench
voice-tools task pack task.json --root project --out task.vtask.zip
voice-tools task check task.vtask.zip --profile executor.json
voice-tools task run task.vtask.zip --profile executor.json --out task-run
voice-tools task collect task-run --out result.vresult.zip
voice-tools task review result.vresult.zip --out review
```

以运行时 `voice-tools schema --tool task` 为准确接口。工作台“中文时序”页可编辑模板、起声触发、检测参数、测量窗口、预期和人工语音区间，下载场景、对应任务和三次重复队列。文件选择器只填写文件名，录音须放入提示的 `cases/audio/`；它不会上传录音，也不会直接拨号。场景依赖 1.1；页面提示最低工具版本，任务包清单记录实际打包工具版本。

既有打包器递归收集场景引用的 WAV／媒体包；`speech`、检测参数和窗口直接保存在 JSON 内，并由任务包清单校验摘要。执行机沿用 `network_allowed`、`environments.lab.sip` 覆盖、环境变量凭据、超时、停止和结果收集。默认禁止联网时，真实呼叫的预检会拒绝且不访问线路；只有获授权测试端点可写入执行机配置。

结果复查的“时序评测”页显示 RX／TX 片段、首声／插话／停声／再次出声标记、指标表和异常说明。点击指标或片段会打开对应 PCM 并定位到音频样本时间，运行时钟与拼接 WAV 偏移不会混用。每步最多预览 200 份时序报告，超限明确提示；完整 JSON 和音频仍在结果包中。批次汇总页显示四类数量、有效测量数和分组。

复查可从本地静态文件打开；经 HTTP 提供音频时，服务器应支持字节 Range 请求，否则浏览器可能无法跳转到指定时刻。页面不连接 SIP 服务。

## 验收与来源

执行分层验收见 [验证记录](benchmark-validation.md)。真实中文验收仍需获授权真人素材、具体 FreeSWITCH 目标／账号及每个用例的预期；普通话／口音、音量、噪声须分组记录，并分别保留机器声学测量和人工判断。

参考 [tvbench](https://github.com/ictinnovations/telephony-voice-agent-benchmark/tree/fdc4410316110a163c25efecb53aa66459526b7c)，固定提交 `fdc4410316110a163c25efecb53aa66459526b7c`（MIT）。上游独立克隆在相邻目录，本实现没有运行时路径依赖、复制其源码或引入其 AudioSocket 服务。参考时序测量／故障模拟思路，并独立修正连续静音、新回答误并入停声和固定帧长等边界。许可说明见 [第三方清单](../THIRD_PARTY_NOTICES.md)。
