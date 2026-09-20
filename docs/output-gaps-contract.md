# 输出间隙数据合同 1.0

本合同对应 voice_tools 0.14.1；[使用指南](output-gaps.md)、[验证记录](output-gaps-validation.md)。各对象的 `schema_version` 独立演进，不能用工具版本替代。所有录音时间都是原录音相对秒数，区间为 `[start_s, end_s)`；数字须有限。文件摘要为原始字节的 SHA-256 小写十六进制。

## 输出事件扩展

沿用 QA 的同名 `.events.json`、`schema_version: "1.0"`、`system_channel`（0 左，1 右）、`channel_verified`、AI 接管时间、人工用户语音和业务排除区间。旧字段的 QA 解释不变。新增 `output_events` 是独立扩展：

在 gaps 中，合法的旧 `ai_start_s` / `ai_end_s`、`user_speech` 和 `exclusions` 在声道已核实时参与过滤，不依赖 `output_events` 是否存在或对齐；无扩展时仍为 `acoustic_only`。新输出事件另行要求录音摘要和映射验证。未核实角色的元数据不排除声学候选。检测规则身份已更新为 `output-gaps-2`，早期规则生成的标签须重新复核，不能静默套用。

```json
{
  "schema_version": "1.0",
  "system_channel": 1,
  "channel_verified": true,
  "output_events": {
    "schema_version": "1.0",
    "audio_sha256": "替换为录音的64位小写摘要",
    "alignment_verified": true,
    "source": "播放日志与录音零点经人工核实",
    "events": [
      {"id":"expect-1","type":"tts_expected","utterance_id":"u1","start_s":1,"end_s":6},
      {"id":"play-1","type":"tts_playback","utterance_id":"u1","start_s":1,"end_s":2},
      {"id":"play-2","type":"tts_playback","utterance_id":"u1","start_s":4,"end_s":5},
      {"id":"end-1","type":"tts_end","utterance_id":"u1","at_s":6}
    ]
  }
}
```

| 事件 | 字段与含义 |
|---|---|
| `tts_expected` | 一轮输出的完整预期窗口；每个 utterance 只能一个，允许延伸至录音结束之后，以保留观察不足状态 |
| `tts_playback` | 一个实际播放分片；同一 utterance 可以多个；片尾不自动取消预期 |
| `tts_end` / `tts_cancel` | 明确完成或取消时间 `at_s`；同一 utterance 只能有一个结束/取消 |
| `tool_wait` | 工具等待区间；只有 `allows_silence: true` 才可排除 |
| `user_interrupt` | 明确用户插话区间；插话开始后原输出间隙的观察结束 |

事件 id 非空且唯一；TTS 必须有 utterance_id。播放必须处于对应预期窗口且不得发生在结束/取消后。重叠预期窗口、孤立播放、矛盾时间使本文件全部事件过滤降级，保留声学候选；格式或摘要错误属于输入错误。未核实声道角色不能通过另一轨活动消除候选。

`alignment_verified` 是提供方的显式声明，不是工具自行证明的对齐。设为 true 时必须提供正确录音摘要及非空 source。未核实映射不用于业务过滤或永久中断。永久中断还要求 AI 已有声学活动、预期窗口完整覆盖且观察达到长停顿阈值；不足时为 CENSORED。正常完成后的尾静音不推断为永久中断。

`audio prepare` 仅在已验证的原点保持转换时重绑目标音频摘要，同时保留 `source_audio_sha256`、`source_events_sha256`。无法核实映射时不能建立对齐等级。`qa freeze` 保存扩展及来源，不修改源事件。

## 证据关联清单

```json
{
  "schema_version":"1.0", "kind":"gap_evidence",
  "recordings":[{
    "audio_sha256":"替换为原录音摘要",
    "rtp":[{
      "timeline":"media/rtp-timeline-001/timeline.json",
      "sensor":"edge-a",
      "stream":{"src":"192.0.2.1","src_port":16000,"dst":"192.0.2.2","dst_port":24000,"ssrc":"0x1234"},
      "recording_start_epoch":100,
      "alignment_verified":true
    }],
    "nisqa":[{
      "results":"nisqa/results.jsonl", "provenance":"nisqa/provenance.json",
      "source_file":"call.wav", "channel":"right"
    }]
  }]
}
```

引用相对此清单目录；时序分片相对 timeline.json 目录。跨机时所有依赖必须位于任务打包根内，禁止绝对文件引用和越界引用。`source_file` 是 NISQA 行中的逻辑来源标识，不作为路径重新解释；即使它原为绝对路径，迁移也保留其原值和文件摘要。一个录音摘要只能一个 recordings 条目，每类证据最多 100 条绑定。

RTP 录音时间 = 抓包 epoch − recording_start_epoch。必须显式匹配 sensor 和完整的有方向五元组；SSRC 更换需要另一条绑定。时钟漂移声明 `clock_drift_detected: true` 或抓包时间倒退均降级为未对齐。仅支持固定偏移，不做自动时钟漂移校正。不把其他流合并填补当前流覆盖。

每个 `rtp_timeline` 1.0 清单含 sensor、sources 原始抓包摘要、complete、coverage_start_epoch/end_epoch、packet_limit_reached、truncated_packets、out_of_order_capture_timestamps 以及 chunks。分片项包含 path、bytes、sha256、rows；每片最多 8 MiB。包行包含 epoch、src/src_port、dst/dst_port、ssrc、seq、timestamp、pt、clock_rate，不含媒体载荷。导出复用现有解析，不额外启动 tshark 扫描。

启用时序导出时，在分析前后对每个分组源 PCAP 计算 SHA-256；仅在摘要一致后发布时序目录。分析期间文件变化或消失会保留错误报告并返回部分失败，不发布该组时序；默认 report 不增加这些摘要扫描。`aligned` 还要求选定流覆盖全部待关联区间；即使清单 complete=true，只要某一区间首尾覆盖不足，来源摘要也为 partial。

导入顺序跨分片连续；每条绑定最多读取 100 万包。序号按 16 位、RTP 时间戳按 32 位回绕处理，保留重复、乱序、序号大跳变/源重启候选、时间戳倒退线索。间隙关联还包括窗口外最近的前后包；到包最大间隔可跨越窗口边界。`forward_sequence_jump_candidates` 不是最终丢包数；重启启发式也不是经过校准的根因判断。缺少匹配流或覆盖末端不足为 partial，不能当作无丢包。

NISQA provenance 1.0 包含 kind=nisqa_provenance、results、results_sha256、sources；来源项有 file、sha256、unchanged。必须同时匹配结果摘要、原录音摘要及 AI 声道。只关联 start_seconds/end_seconds 与间隙重叠的原始片段，原 scores/status/reason 不变；不产生新的间隙音质分。`offset_s` 非零还需 alignment_verified；相同原录音的零偏移天然共用相对时间。

旧 NISQA 去掉 provenance，可显式作为 unverified 旁证。旧 RTP 使用 `report` 替代 timeline，只展示匹配 sensor/流的整流统计。摘要错误绝不能降级成“已验证”。

| 状态 | 解释 |
|---|---|
| not_provided / not_bound | 没有提供可选证据 / 没有当前摘要的绑定；不导致失败 |
| available | 清单可处理；逐来源检查等级 |
| aligned | 已核对合同与声明的时间映射；不证明根因 |
| unverified / unaligned | 旧结果人工绑定 / 时间未核实，不获得已对齐等级 |
| legacy_summary | 仅整流汇总，不能定位当前间隙 |
| partial / no_matching_segments | 时序覆盖不足或没有对应评分区间 |
| error | 显式证据缺失、损坏、摘要/声道/采集点错误；退出 3，录音检测保留 |

## 结果、身份和标签

`results.jsonl` 一行一个录音：schema_version=1.0、tool=gaps、tool_version、input、audio_sha256，以及可用的 events/events_sha256、preparation、result、evidence、waveform、playback_sources。单文件错误记录 error；部分检测额度错误可同时保留 result 和 error。

result 含配置 config、fingerprint、duration_s、sample_rate、channels、channel_verified、health、warnings、status、gaps、candidate_count、cluster_count。文件状态为 NO_CANDIDATES / REVIEW_REQUIRED / INSUFFICIENT_EVIDENCE；无候选不等于业务通过。gap 含 id、type、start_s/end_s、original_start_s/original_end_s、duration_ms、status、reason_code、evidence_level；过滤后的每一片保留原始区间。类型为 dead_air、micro_dropout、clustered_short_gaps、terminal_interruption。聚集仅引用 members，不增加独立候选计数。

fingerprint 由算法版本、录音摘要、规范化事件与全部检测配置的 canonical JSON SHA-256 生成。gap id 由 fingerprint 和区间内容生成（前 24 位）；不依赖输入路径、批次顺序、旁证或复核标签。来源路径、source 描述和不参与检测的旁证不进入身份计算；有效事件按 id 排序。分片展示不改变这些身份。调整规则、事件或区间后旧标签必须重新核查，不自动迁移。

CSV 的固定顺序：

```text
schema_version,audio_sha256,fingerprint,gap_id,origin,type,start_s,end_s,decision,reviewer,reviewed_at,notes
```

origin=automatic 的 id/type/区间必须与结果完全一致（时间容差 1e-6 秒）。origin=manual 使用 `manual-` 前缀唯一 id、type=manual_gap，区间位于录音内部；与自动结果分开保存。decision 是 confirmed / normal / excluded / uncertain；须有非空复核人和带时区的 ISO 时间。自动候选允许未标注空行，但其余人工字段必须空。重复 id、篡改区间、缺时区、跨规则身份、QA 旧标签均拒绝。CSV 导出对公式前缀和前置单引号转义，页面导入还原一层保护，不反复累加。

`summary.csv`、`run.json`、`report.html`、`review.html`、`review.csv` 都是独立产物。页面需要 --include-audio 才能试听和下载片段；片段时间映射记录原录音摘要、检测身份、间隙 id、原区间、采样率和声道。任务复查从受信任安装资源重新构建页面，不执行包内 HTML。

## 资源与错误边界

单批最多 200 个录音；PCM16 WAV 8–48 kHz、单/双轨、最长 3600 秒、解码样本最多 512 MiB；单事件文件最多 4 MiB、最多 10 万事件。每录音保留最多 2000 个切分区间（聚集另列），超额明确报告部分错误。批次 JSONL 预算 12 MiB，超额录音记录错误，前部结果保留。时序单行最多 1 MiB，单绑定最多 100 万包。任务原有 16 MiB 结构化文件及总包额度保持不变。长录音适合按明确时间映射分段复核，前端内存不等于检测器内存。

错误示例：把 WAV 重新编码但沿用旧摘要会拒绝事件/已验证旁证；更换 system-channel 而不改事件角色会拒绝；只写播放 stop 没有 tts_end/cancel 不会取消预期；tts_expected 超出录音只给观察不足；工具等待未声明 allows_silence 不豁免；时序缺片保留声学结果并退出 3。退出码完整定义见使用指南。
