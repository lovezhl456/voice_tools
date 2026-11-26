# 录音质检工作台

启动仅监听本机的服务。新目录首次启动时可以省略录音文件夹。

```sh
voice-tools detect serve --db /path/to/data/library.sqlite3 --out /path/to/data --port 8766
```

打开 `http://127.0.0.1:8766/recordings/`。旧配置编辑器在 `/`，旧在线工作区仍在 `/workspace`。现有数据库可继续使用。恢复已有报告时沿用原 `--db` 和 `--out`，不要直接替换数据库。

## 录音、复核和导出

在左栏选择「关联」，粘贴录音文件夹的绝对路径。可选 AI 所在声道。服务递归列出 WAV、FLAC、MP3、M4A 和 OGG。录音保留在原目录。非 PCM16 音频按需用本机 FFmpeg 转换，显示转换后相对时间。切换到另一通录音不会改变用于导出的勾选。刷新目录可发现新文件，缺失原文件仍显示在清单中。

先在全局规则库选择本次启用的规则，再标注当前录音或批量标注目录。查看命中，点击「试听前后」，确认、修正、留下误报备注或撤销。复核和规则保存在同一本机检测库。密集时间轴可按分钟展开。同一段命中多条规则时各自保留。

从顶部导出所选录音。ZIP 包含 `recordings.json`、`annotations.json`、`annotations.csv`，以及关联的规则快照和原始音频。仅数据模式允许保留已移走录音的历史。仅确认模式包括人工确认和人工修正的结果。每条记录包含原始和修正区间、人工状态和版本。相同内容的文件共用检测和复核历史，依旧可以在导出清单中分别出现。

## 全局规则

可导入原有的检测定义 1.0。窗口指标及 AND、OR、NOT 按旧合同执行。新版还支持将以下时序规则保存和导出为 JSON。

```json
{
  "schema_version": "2.0",
  "id": "reply-wait",
  "version": "1",
  "name": "应答等待超过两秒",
  "kind": "response_wait",
  "channel": "both",
  "op": "gt",
  "min_s": 2,
  "threshold_dbfs": -45
}
```

| 类型 | 判定 |
| --- | --- |
| `quiet` | 选择 `left`、`right` 或 `both`。双声道时取两轨安静区间的交集。 |
| `output_pause` | AI 声道的前后活动之间为声学停顿候选，前后均需有声。 |
| `overlap` | 取两轨活动区间的交集。 |
| `response_wait` | 调用本机逐轮延迟引擎，取用户停止至可配对的下一次 AI 起声。未配对原因写入检测记录。 |

时序规则默认双轨、严格大于、活动阈值 -45 dBFS；`ge` 表示大于等于。声学区间由 20 毫秒能量帧生成，应答起止使用逐轮引擎的证据。需要识别 AI 的规则必须确认声道映射。逐轮引擎使用 `VOICE_TOOLS_LATENCY_DIR` 或系统默认安装目录。可用 `voice-tools latency doctor` 核验，安装见逐轮延迟手册。输出等待阈值请用实际通话试听确认。

「与 AI 定规则」可在当前页面会话填写 GPT API Key 和模型 ID，也可在启动服务前设置 `OPENAI_API_KEY` 和 `VOICE_TOOLS_OPENAI_MODEL`。页面输入的 Key 只保存在页面内存中。发送给 OpenAI 的内容为规则讨论与检测能力目录，规则校验及录音分析在本机执行。旧规则版本无法覆盖，修改时使用新版本。

- [旧版检测定义](detection-rules.md)
- [逐轮延迟安装](latency-install.md)
- [原离线工作台](tabbed-workbench.md)
