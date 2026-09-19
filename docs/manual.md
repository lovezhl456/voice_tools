# voice_tools 2.0 · 人工使用手册

CLI 是主入口：录音体检、格式准备、质检、冻结与版本对比都通过命令运行，HTML 用于查看和人工试听标注。HOMER 继续使用[独立手册](homer.md)。

## 安装

Python 3.9+，CPU 运行，无需 API Key。仓库根目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
voice-tools --version
voice-tools --help
voice-tools audio --help
voice-tools qa --help
```

默认只依赖 NumPy；可选语音检测安装 `python -m pip install -e '.[vad]'`。格式转换需要本机 `ffmpeg` 和 `ffprobe`。PCM16 WAV 体检及同采样率复制无需 FFmpeg。也可用 `python -m voice_tools` 调用。

所有输出使用新目录或空目录。重复执行时换运行编号，不覆盖已有结果。

## 1. 体检与格式准备

```bash
voice-tools audio inspect data/raw --out outputs/inspect-001
voice-tools audio prepare data/raw --sample-rate 16000 --out data/prepared-001
```

体检生成 `report.html`、`results.jsonl`、`run.json`。内容包含格式、时长、采样率、声道，以及逐轨峰值、全通话中位能量、活动期间中位电平、静默比例、削波/DC 异常区间和双轨相似线索。长静默会降低全通话中位能量，请同时看 `active_median_dbfs`。相关性不能自动确认串音，能量活动也不等于语音。

支持 WAV、FLAC、MP3、M4A、OGG、G.711 A-law/μ-law WAV。裸音频必须明确编码、采样率和声道数：

```bash
voice-tools audio prepare data/call.pcm --raw-format s16le \
  --raw-sample-rate 8000 --raw-channels 1 --out data/prepared-raw-001
```

`--raw-format` 可用 s16le、alaw、mulaw；扩展名可用 .pcm/.raw/.alaw/.ulaw。三个 raw 参数须同时提供。只接受一个单/双声道音频流，多流或多声道不会被自动选择或混音。单轨不会复制成假双轨。

准备文件以编号/摘要命名，附 `.provenance.json`：输入/输出 SHA-256、声道顺序、采样率、时间映射、命令和 FFmpeg 版本。工具不归一化、不降噪、不裁剪。原录音不变。

当前最长 3600 秒，分析采样率 8–48 kHz，float32 解码样本上限 512 MiB；48 kHz 双声道约 23.3 分钟就会达到该上限。总内存还包含其他临时数据，更长文件应按可靠时间轴分段。

### 转换后的事件

PCM/无损/G.711 在起点和时长检查通过后可复制同名 `.events.json`；相对时间检查不代替事件的业务真实性。有损格式的编码延迟默认未核实，不复制事件。

人工将事件对齐到生成的 WAV 后，在事件对象内补充以下字段，继续保留实际的机会、讲话、接管等字段：

```json
{
  "alignment": {
    "audio_sha256": "生成WAV的64位SHA256",
    "reviewer": "实际核对人",
    "reviewed_at": "2026-09-19T14:00:00+08:00"
  }
}
```

不要把示例当作真实事件。转换时间映射未知、又缺少人工对齐记录时，QA 拒绝使用复制来的事件。没有事件仍能做低证据声学筛查。

## 2. 分析与人工复核

```bash
voice-tools qa analyze data/prepared-001 --out outputs/qa-001 \
  --include-audio --system-channel 1 --channels-verified --timeout 5
open outputs/qa-001/review.html
```

只有受控通话验证角色后才使用 `--channels-verified`。0 为左、1 为右；未显式指定时读取事件映射，否则默认 1。CLI 显式映射与事件冲突会报错。

| 文件 | 用途 |
|---|---|
| review.html | 双轨波形、筛选、单轨试听、循环、快捷标注和 CSV 导入导出 |
| report.html | 完整静态报告、错误及交互页面入口 |
| results.jsonl | 逐录音结果、参数、摘要、事件快照 |
| run.json / summary.csv | 运行/文件统计 |
| review.csv | 空白人工复核表，含可选评估字段 |

`--include-audio` 复制原录音、左右轨试听副本，以及每个机会前 2 秒/后 1 秒的片段；片段 JSON 记录原始时间和声道映射。调整播放器区间不改变标注窗口或默认下载片段。

HTML 可离线打开；如果浏览器不能跳转或播放，使用支持 HTTP Range 的本地静态服务。保存完整报告目录及 audio/。`--hide-paths` 隐藏 HTML 来源绝对路径，JSONL 保留溯源；它不是对错误文本和用户元数据的自动脱敏。

复核步骤：

1. 选择机会，试听双轨或左右轨；橙色背景为标注窗口。
2. 选择人工判断，填写实际复核人和说明；需要分层评估时补充场景和来源组。
3. 点击“记下标注（待导出）”，此时仅保存在当前页面。
4. 点击“导出复核 CSV”，保管下载文件。离开有未导出改动的页面会提示。
5. 重新打开同一批次页面并导入 CSV 继续。错批次/摘要/窗口、重复行、半填标签和冲突修改会拒绝，原标签不会被部分覆盖。

快捷键：空格播放；1–5 选判断；左右键切换机会。输入框内不触发快捷键。“放弃表单修改”恢复本页上次记下的标签。

波形由本地内嵌的 WaveSurfer.js 绘制。两轨共享时间标尺，可点击定位、缩放或定位当前机会；青色边缘调整试听范围，琥珀色固定窗口保留标注边界。边缘获得焦点后可用左右键微调，Shift 加速。显示增益仅放大图形，左右轨同比例，不改变音量或分析结果。组件来源与验收见[波形组件说明](waveform-components.md)。

人工判断：missing 缺失、delayed 迟答、audible 有可听回答、exclude 无需应答、uncertain 不确定。波形有活动不证明有效回答。

## 3. 稳定时间轴与黄金集

参数可能改变自动识别的讲话区间。先冻结，再核对、分析和标注：

```bash
voice-tools qa freeze --results outputs/qa-001/results.jsonl --out data/frozen-001
# 人工核对或修订 frozen-001 中的事件：机会、讲话、排除、接管及退出时间。
voice-tools qa analyze data/frozen-001 --out outputs/frozen-qa-001 --include-audio
open outputs/frozen-qa-001/review.html
# 在此页面完成试听、导出复核CSV后执行：
voice-tools qa promote /实际下载目录/review.csv \
  --results outputs/frozen-qa-001/results.jsonl \
  --dataset-kind real --out data/golden/v1.json
```

冻结是算法快照，`timeline_reviewed: false`，不是人工黄金标签。核对后可记录 true；任何事件修改后都须重新分析和标注，旧 sample_id 不再适用。含失败录音、单声道或重复声道的批次不能冻结双方时间轴，先筛选有可靠双轨的录音。

真实录音使用 real，合成录音即使人工复核过也使用 synthetic。除报警外要抽查普通结果，否则无法估计漏检。

| 可选字段 | 含义 |
|---|---|
| first_audible_s | 相对整份录音的首次可听回答时间，须在观察窗口内；missing 不可填 |
| expected_response | true/false；false 只能搭配 exclude/uncertain |
| deadline_s / policy_id | 业务应答时限和策略版本；填时限须填版本 |
| scenario | 场景标签，多个以分号分隔，例如 quiet;noise |
| line_id | 线路或系统来源 |
| group_id / split | 通话/来源组，以及 calibration 或 validation；同组/同录音不得跨集合 |

新黄金集 schema 为 1.1，评估兼容原 1.0。字段可选，不填不伪造。回答时间、时限与人工迟答标签冲突时拒绝晋升。

## 4. 评估与版本对比

```bash
voice-tools qa evaluate data/golden/v1.json \
  --results outputs/frozen-qa-001/results.jsonl --out outputs/metrics-v1.json
voice-tools qa analyze data/frozen-001 --threshold-db -50 --out outputs/frozen-qa-002
voice-tools qa compare --baseline outputs/frozen-qa-001/results.jsonl \
  --candidate outputs/frozen-qa-002/results.jsonl --golden data/golden/v1.json \
  --out outputs/compare-001
```

比较要求相同录音/事件/机会集合、固定用户讲话区间、相同观察窗口和应答时限。可改能量门限或检测后端；改变 `--timeout` 会改变迟答口径，当前比较器拒绝混合口径。

输出逐条新增/消失/变类、人工判断、分场景/线路/样本集表现、无输出/迟答分类指标、每百个计分机会的误报、覆盖率及 95% Wilson 区间。

观察不足、证据不足与算法排除单列为 abstained，不算通过。主 precision/recall 针对可计分机会，同时看 recall_including_abstentions 和 scoring_coverage。区间按机会计算；同通话样本可能相关，不能代替按通话分组的业务验证。

## 5. 退出码与排障

| 退出码 | audio / qa 含义 |
|---|---|
| 0 | 完成，仍可包含候选 |
| 1 | 仅 analyze --fail-on-findings：存在候选 |
| 2 | 参数、整体输入、依赖或数据契约错误 |
| 3 | 部分文件失败，优先于候选退出码；成功结果保留 |

HOMER 有独立退出码，6 表示部分搜索结果，见 [HOMER 手册](homer.md)。

- 没有音频：换新目录加 --include-audio 分析。
- 窗口不匹配：使用同一冻结数据重新分析，不删掉不匹配行制造更好指标。
- 输出已存在：换新目录。
- 缺 FFmpeg：先确认 ffmpeg -version 和 ffprobe -version 可运行。
- 生产准确率未知：合成测试不能代替真实录音和独立人工标签。

大模型调用见 [Agent 协议](ai-usage.md)；`voice-tools schema --tool qa` 输出实际参数，前置 `--json` 避免解析中文提示。
