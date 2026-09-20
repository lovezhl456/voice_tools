# 双轨录音延迟分析

`latency` 是可选的独立工具：在已有双声道录音中，测量 Human → AI 与 AI → Human 的相邻有效片段间隔。它使用固定版本的 latency_checker 及受控补丁，不替代 QA、SIP 或 benchmark，不更改黄金标签、业务 SLA 或场景。

先阅读 [安装说明](latency-install.md)，实现约定见 [集成协议](latency-integration.md)，实际验证范围见 [验收记录](latency-validation.md)。

## 输入与声道

仅接受双声道 PCM16 WAV，采样率为 8、16、24、32、44.1、48 kHz。每份最长 3600 秒，同时必须满足解码 float32 双声道数组不超过 512 MiB；例如 48 kHz 的大小限制先于时长限制生效。这不是总进程内存上限。

必须显式提供 `--system-channel left|right`，另一轨为人声。同批输入使用相同映射。其他格式先用 `voice-tools audio prepare --help` 按实际合同显式转换；工具不自动混音，不拼接 SIP 收发录音，不推断声道身份。不同采集点、失配的两轨或漂移时钟，不能据此得到可靠的业务延迟。

```sh
voice-tools latency doctor
voice-tools --json latency analyze demo/audio/normal.wav --system-channel right --out results/one --include-audio
voice-tools --json latency batch demo/audio demo/audio/normal.wav --system-channel right --out results/batch --include-audio
voice-tools schema --tool latency
```

目录递归收集 WAV。规范化后的相同路径仅处理一次，并按路径排序。不同路径的相同 PCM 内容保留，标记 `duplicate_of_file_index`，按实际处理的文件和轮次参与汇总；这不是独立样本量的证明。无 WAV 的目录记为文件错误。

## 参数的唯一来源

[代码契约](../src/voice_tools/tools/latency/contract.py) 是参数单位、范围、默认值与退出码的唯一维护来源。`voice-tools schema --tool latency` 的 `latency_contract` 输出完整表；`cli.arguments` 的 `null` 表示未显式覆盖，实际默认值由该契约解析。结果保存全部有效参数、`default/explicit` 来源以及固定算法常量。

能量阈值的单位是归一化采样的 **均方能量 × 10⁶**，不能直接使用 QA 的 dBFS 参数。毫秒参数必须为 10ms 的整数倍，否则拒绝；不会悄悄截断。每文件超时用 `--timeout`，任务步骤自己的 `timeout_s` 同时有效，以先触发者为准。

## 结果与解释

| 文件 | 内容 |
| --- | --- |
| `run.json` | 运行状态、参数、算法来源、完整批次统计与索引引用 |
| `files.jsonl` | 每文件成功、错误、中断、摘要、重复标记和详情的相对路径 |
| `turns.csv` | 全部有效轮次，两个方向独立标记 |
| `recordings/000001/recording.json` | 录音身份、逐片段去向、逐轮数据、完整参数和资源记录 |
| `recordings/000001/audio.wav` | 仅在 `--include-audio` 时复制的试听音频 |
| `index.html`、`latency.js/css` | 本地报告；详情按需加载，不使用 CDN |

录音身份为文件 SHA-256，与机器路径无关；PCM SHA-256 还用于识别音频内容重复。结果保留输入路径用于追溯，公开分享前需要考虑路径和音频信息。

两个方向分别计算均值、中位数、P95、最小和最大值。分位数采用排序样本上的线性插值；跨文件直接汇总逐轮数据。无有效轮次时为 `null`，不会显示零延迟。

Human → AI 配对覆盖率的分母是满足最短时长条件的全部人声片段，分子是完成配对的这些片段。零分母为 `null`。重叠、被后续片段替代、未配对和短片段均有去向；**配对覆盖率不等于应答成功率**。双轨完全相同、全静音或无有效配对时为 `insufficient_evidence`，不会显示“通过”。

执行完成、存在有效测量、业务正确性是三个不同结论。退出码的精确定义见上述代码契约和 CLI schema；退出 0 也可能没有有效测量，退出 3 要检查文件错误和已完成产物。Ctrl-C/任务停止保留中断记录。输出目录必须不存在，异常不回退成成功。

## 报告查看

通过支持 HTTP Range 的本地服务器查看。仓库提供：

```sh
python scripts/serve_latency.py --root results --port 8088
# 打开 http://127.0.0.1:8088/one/
```

报告支持双轨波形、逐轮试听、录音和轮次分页、延迟图表及完整下载。波形最多每轨 1600 格，列表每页 50 条；不会影响统计或导出。没有复制音频时仍可查看测量数据，但无法试听。验收使用 HTTP/206，不承诺 `file://` 双击行为。

独立报告的音频开关不改变任务包收集输入录音、结果包保留工作输入的既有行为。任务复查从结构化 JSON 重新渲染，不执行包内 HTML/脚本。

## 与现有功能的关系

QA 保留自己的检测、机会定义与黄金标签。benchmark 使用 SIP 媒体桥运行时钟，latency 时间原点是被分析录音起点，两者不能自动合并。NISQA/ViSQOL 的听感评分不是时序延迟。这些工具的算法和默认行为保持原样。合成信号上 20ms 的验收界限，不是中文真实通话准确率承诺。
