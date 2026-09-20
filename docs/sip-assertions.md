# SIP 结构化测试断言（0.8.1）

在 `scenario.json` 顶层添加 `assertions`，使用原有 `sip validate`、`sip run --dry-run`、`sip run`。断言适用于 PJSUA2 单通话路径；独立 SIPp 回放和工作台原型暂不读取此配置。省略或使用空数组时保留原有动作完成判定。

```json
"assertions": [
  {"id": "answer", "type": "response_code", "codes": [200]},
  {"id": "media", "type": "received_rtp", "min_packets": 10},
  {"id": "audible", "type": "effective_audio", "min_duration_s": 0.5, "threshold_dbfs": -40},
  {"id": "menu", "type": "dtmf", "digits": "11#", "match": "exact"},
  {"id": "ringback", "type": "tone", "frequencies_hz": [440, 480], "min_duration_s": 0.12}
]
```

按被测端实际行为选取项目，不应把示例全部作为默认通过条件。完整可校验样例见 [assertions.json](../examples/sip/assertions.json)，测试地址为占位符，需自行替换。

| type | 配置与默认值 | 证据及通过条件 |
|---|---|---|
| `response_code` | 必填 `codes`，200–699 的非空整数列表 | 初始呼叫建立阶段实际收到的最后一个 INVITE 最终响应在列表内；接通后冻结，排除 REGISTER、INFO、BYE、re-INVITE 和本地超时状态码 |
| `received_rtp` | `min_packets=1`，正整数 | PJSUA2 `getStreamStat().rtcp.rxStat.pkt` 的接收包数下界达到阈值；不使用发送包数或 WAV 时长代替 |
| `effective_audio` | 必填 `min_duration_s`，0.02–900；`threshold_dbfs=-40`，-90–0 | `rx.wav` 按 20 ms 分帧，去直流偏移后的 RMS 达到阈值的累计时长足够；不是 VAD、语音识别或可懂度评分 |
| `dtmf` | 必填 `digits`，1–128 个 `0–9*#ABCD`；`match=exact`，可选 `contains` | 原生接收回调形成有序字符串；exact 完全相等，contains 连续子串匹配；保留真实重复数字，不读取 `dtmf_sent`。支持原生 RFC4733 / SIP INFO 接收，不检测带内 DTMF |
| `tone` | 必填 `frequencies_hz`（1–4 个 100–3500 Hz 频率，间距至少 25 Hz）、`min_duration_s`（0.04–900）；`threshold_dbfs=-40`；`min_power_ratio=0.6`（0.5–1） | 40 ms 接收音频窗口同时拟合指定正弦/余弦；拟合解释能量占比达标，每个频率功率至少占窗口能量 10%；连续命中时长达标。时长分辨率 40 ms，按完整窗口计，不识别振铃节奏、忙音语义或任意音乐 |

每项可提供唯一 `id`，省略时依序生成 `assertion_1` 等。最多 64 项，全部必须通过。未知字段、NaN、布尔冒充数值、错误类型、重复 id 均在拨号前拒绝。

## 结果、退出码与失败边界

`result.json.assertions` 和单独 `assertions.json` 内容相同：包含 `schema_version=1.0`、聚合 `status`、`counts` 和逐项 `items`。每项都有 `id/type/expected/actual/status/evidence/reason`；`evidence` 指向运行目录内文件或原生指标。原始接收应答与 DTMF 保留在 `events.jsonl`。

- `passed`：证据支持预期条件。
- `failed`：完整观测或确定的应答码表明不符合条件。
- `insufficient_evidence`：没有接收证据、录音损坏，或观测中断且已有证据不能证明达标。缺失数据不当作零。
- 聚合顺序为 failed → insufficient_evidence → passed；无配置为 `not_configured`。预演为 `not_evaluated`，只保存规范化配置。

动作成功但任一断言失败／证据不足时，`status=failed`、`error.code=ASSERTIONS_NOT_PASSED`，CLI 退出 **3**；`execution_status` 保留断言前的执行结果。原有运行错误或操作者中断不会被断言通过覆盖。退出 0 仍不证明 IVR 业务语义正确，`business_assertions=not_evaluated` 专门保留这一边界。

预期拒接例如 `codes:[486]`：若从未接通且收到了匹配所有应答码断言的最终拒接响应，记录 `expected_rejection=true`，媒体步骤全部跳过、`steps_completed=0`、`steps_skipped` 保留数量；不强制要求 rx.wav。其他同时配置的媒体断言仍照常评估，缺失证据不会通过。未匹配的拒接、无响应和本地超时仍失败；中间认证挑战不会提前结束测试。

RTP 统计在轮询和本地主动挂断前采样。媒体流替换可能重置计数，因此报告最大已观察接收计数作为保守下界，不跨重建流简单相加。下界达到阈值即可通过；低于阈值只有在完整执行、单一流且挂断前最终采样成功时判失败，其余为证据不足。对端提前挂断即使已有部分断言通过，也保留运行失败。

有效音频和音调只读取 `rx.wav`，包含抖动缓冲／丢包补偿的影响。默认只覆盖接通后录音；`record_early=true` 才纳入早期媒体。DTMF 观测覆盖本次呼叫原生回调直到清理结束。首版不提供按步骤、时间窗或 ASR 文本断言；音调命中不触发提前挂断。

## 参考与验证

参考 [VoIP Patrol 固定提交](https://github.com/jchavanton/voip_patrol/tree/5b0de9799899e5d041c6f3432f3b878de5285f59) README 中的 `expected_cause_code`、`rtp_stats`、`detect_tone` 和 `tones`（多频同时出现）。这里采用项目自己的 JSON 配置、三态证据和结果聚合；未引入或复制其 C++ 运行时。

```sh
python -m unittest tests.sip.test_assertions tests.sip.test_regressions -v
VOICE_TOOLS_SIP_LOOPBACK=1 python -m unittest tests.sip.test_loopback -v
```

第一组为离线测试；第二组使用可选 PJSUA2，仅绑定 127.0.0.1 的独立 SIP 对端，发送合成音频／DTMF。生产网关、NAT 和运营商线路需另外验收。

2026-09-19 本轮验收：开启本机 SIP 集成测试的全仓 `unittest discover -s tests -v` 共 **299 项全部通过，无跳过**，其中 **19 项真实本机 SIP 测试**。覆盖重复 RFC4733 按键、SIP INFO 按键、440/480 Hz 双频、静音 RTP、无 RTP、预期拒接和无响应；所有呼叫只发往 127.0.0.1。
