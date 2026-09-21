# 整通自动质检

日常批量质检使用 `voice-tools qa assess`：工程规则检查全部录音，本地 CPU 语音模型提供第二层证据，按整通输出结论，人工处理例外与抽检。原 `qa analyze`、逐机会标注和黄金集评估继续保留。

## 安装与运行

已安装基础 CLI 后，在选定的 Python 环境中执行；菜单可选择全部、仅运行库或仅模型：

```bash
voice-tools qa setup
# 脚本／无终端环境：一次安装运行库、下载模型并检查
voice-tools qa setup --component all
```

`setup` 使用当前 CLI 的 Python 安装依赖，支持缓存与重试。安装范围、离线准备、退出码和排障见[安装说明](automatic-qa-install.md)。只在显式选择模型安装或调用 `model-download` 时下载固定模型；模型约 2.3 MB、MIT 许可。`model-doctor` 和 `assess` 不安装、不下载、不上传录音，使用 ONNX Runtime CPU 并显式禁用遥测。摘要不匹配的权重拒绝加载。模型来源和摘要见[模型清单](../src/voice_tools/tools/recording_qa/resources/silero.json)与[第三方声明](../THIRD_PARTY_NOTICES.md)。

先核实录音系统的左右声道映射，以及 AI 接管时间。若整个录音范围确实都是 AI 会话、右轨为 AI，可批量使用：

```bash
voice-tools qa assess data/calls --out outputs/assessment-001 \
  --system-channel 1 --channels-verified --ai-start 0 --include-audio
open outputs/assessment-001/report.html
```

支持 8–48 kHz、单／双声道 PCM16 WAV，单通最长 1 小时、单批最多 200 个文件。真实单声道可分析，但不能独立验证双方时序，进入待复核。其他格式先用 `audio prepare` 转换并保留来源。读取同名 `.events.json` 和 `.provenance.json`；显式声道参数优先于文件映射，但冲突会拒绝，不能静默交换角色。缺少已核实的声道或接管时间不会自动通过。

自定义模型位置可用 `--model-dir /path/to/model`。没有模型时，可显式使用 `--rules-only` 完成工程检查并生成报告；全部结果进入人工队列。缺少模型的普通调用会先失败，不生成一批伪正常结果。

## 四层处理

1. **工程检查全部录音**：格式、声道、重复／相关性、削波、声学活动、应答窗口和输出间隙。每个文件独立记录处理状态；坏文件或推理失败保留为异常执行记录。
2. **语音模型交叉检查**：固定 Silero VAD 6.2.2，独立处理两个声道，逐录音／逐声道重置模型状态。非 8/16 kHz 输入通过有抗混叠滤波的有理重采样处理。模型没有覆盖完整录音、输出无效或与声学证据冲突时进入复核。
3. **按整通汇总**：优先使用显式应答事件；没有事件时，将相邻用户语音合并为轮次，不跨越已检测到的 AI 回答。一通录音只有一个自动结论，保留各层证据和时间点。双方轮次之间的自然停顿不会当作 AI 输出内部断音；无应答／迟答仍单独检查。
4. **人工处理例外**：默认显示异常、不确定结果和抽检的自动通过录音。相邻证据窗口合并，点击即可定位试听；一次填写整通结论，无需逐片段填写。

| 自动结论 | 条件及处理 |
|---|---|
| 自动通过 | 工程与模型完成、声道与检查范围已核实，当前策略未发现异常或未解决的不确定项；按摘要稳定抽检 |
| 自动异常 | 完整证据支持无应答、迟答或输出间隙超阈值；人工队列中按整通确认和处理 |
| 待人工复核 | 观察不足、噪声／语音判断冲突、未知声道、模型未完成、损坏文件或其他无法自动判断的情况 |

**检查范围仅为声学与应答时序。** 模型识别“有语音”，不判断答非所问、业务合规、事实正确性或用户语义是否真正说完；自动异常也不等于已经确定 LLM、TTS 或 RTP 的责任。默认合并间隔和阈值是可调整的工程策略，不能用合成素材证明实际漏检率。

## 筛选和参数

页面保留目录、文件名／路径片段检索，支持多关键词。可查看全部录音、自动通过、自动异常、待复核或抽检集合。筛选只改变显示，不缩小标签导出范围。未保存的表单在切换时保留；人工仍不确定的录音继续留在例外队列。

| 参数 | 默认值 | 含义 |
|---|---|---|
| `--timeout` | 5 秒 | 无应答／迟答时限；观察不足不会判通过 |
| `--turn-gap` | 0.8 秒 | 用户续说合并间隔，不跨 AI 已有回答 |
| `--output-gap` | 2 秒 | 输出长停顿阈值，结合语音／业务证据判断 |
| `--long-silence` | 10 秒 | 录音内部长时间无语音进入核对 |
| `--audit-percent` | 5 | 自动通过的稳定抽检百分比；小批次实际比例会波动 |
| `--hide-paths` | 关闭 | 页面隐藏来源录音路径，JSONL／汇总 CSV 保留溯源 |

## 产物和人工标签

- `assessment.jsonl`：一通一条，含模型身份、策略、分层证据、整通结论和执行错误。
- `run.json`：整通结论数量、自动判定比例、人工队列和抽检数量。建议试听秒数是证据窗口总长，不是实际人工耗时。
- `report.html`：自包含本地页面；`--include-audio` 额外生成原轨与分轨 WAV，整份报告可能包含敏感录音。
- `summary.csv`：录音级汇总。
- `recording-review.csv`：独立人工结论，默认空白；与旧 `review.csv` 不能混用。

人工判断为 `normal / abnormal / uncertain`，必须有复核人和带时区时间。身份由输入路径、音频、事件、策略、模型与执行状态共同确定；这些证据变更后拒绝套用旧标签。结果包迁移后的页面保留原身份。

```bash
voice-tools qa assess-check outputs/assessment-001/recording-review.csv \
  --results outputs/assessment-001/assessment.jsonl
voice-tools qa assess-evaluate outputs/assessment-001/recording-review.csv \
  --results outputs/assessment-001/assessment.jsonl --dataset-kind real \
  --out outputs/assessment-metrics.json
```

评估单列自动通过集合中已人工发现的异常、已复核覆盖率和机器未判定数量。没有对应标签时指标为 `null`；只复核异常队列不能估计自动通过的漏检率。校准需独立留出的真实样本，并同时抽查正常与异常。自动结论不会自动填成人工标签，也不直接晋升到旧黄金集。

退出码：`0` 完成且无异常／待复核（可含正常抽检）；`1` 有自动异常或待复核；`2` 参数／依赖／整体输入错误；`3` 部分文件或推理失败，检查保留产物。

## 任务迁移

`qa assess`、`assess-check`、`assess-evaluate` 可由任务 catalog 发现。依赖提前在执行机通过 `qa setup --component all` 准备，`qa.setup` 与 `qa.model-download` 不进入离线任务。执行端配置增加 `qa_model_dir`，与 NISQA 的 `model_dir` 分开；模型不会作为输入打包：

```json
{"schema_version":"1.0","environments":{},"qa_model_dir":"/models/autoqa","network_allowed":false}
```

任务预检会执行模型 doctor；`rules_only` 不要求模型。复查端从已校验 JSONL 重建整通页面，不执行结果包提供的 HTML，并只接入包内经过验证的 WAV。运行机需单独准备依赖与模型；本次不自动改变既有 Docker 镜像。
