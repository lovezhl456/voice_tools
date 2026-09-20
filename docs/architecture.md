# 代码结构与扩展约定

## 选择

采用 **单仓库、单 Python 包、多个独立工具**。统一安装和命令入口；每个工具拥有自己的业务规则、配置、输出协议、测试和使用文档。当前提供录音体检与格式准备、录音质检与复核评估、HOMER 7 CLI、会话抓包、批量检索与媒体报告；本地会话索引使用 SQLite，不为尚未明确的工具预建空模块，不安装永久守护服务或引入插件框架；2.0 的远端采集 agent 是有明确截止时间的临时任务。[README](../README.md) 作为工具导航，详细说明由各工具手册承载。

```text
voice_tools/
├── pyproject.toml                 # 包、命令入口、可选依赖
├── src/voice_tools/
│   ├── cli.py                    # voice-tools 入口，只负责装配
│   ├── audio/
│   │   ├── io.py                 # WAV、采样率、声道、文件完整性
│   │   ├── activity.py           # 能量活动 / 可选 WebRTC VAD
│   │   ├── formats.py            # 可选 FFmpeg、格式探测和转换
│   │   ├── rtp.py                # G.711 payload / timestamp 重建
│   │   └── health.py             # 逐轨健康指标与波形摘要
│   ├── core/
│   │   ├── files.py              # JSON、摘要、输出目录保护
│   │   ├── command.py            # 机器调用 JSON 封套
│   │   ├── packets.py            # 明确同采集点分片合并与来源校验
│   │   └── schema.py             # 从解析器导出命令契约
│   └── tools/
│       ├── __init__.py            # 内置工具注册表
│       ├── audio/                 # audio inspect / prepare
│       ├── recording_qa/
│       │   ├── cli.py            # qa 子命令适配
│       │   ├── detector.py       # 应答窗口、排除与候选判定
│       │   ├── scenarios.py      # 可复现合成场景及独立预期
│       │   ├── batch.py          # 批量调度、逐文件错误隔离
│       │   ├── reports.py        # 此工具专属的可读报告
│       │   ├── review.py         # 人工标注、黄金集、评估
│       │   ├── workbench.py      # 复核页生成、声道预览与片段映射
│       │   ├── review.html       # 离线复核模板，脚本为 review.js
│       │   ├── dataset.py        # 冻结时间轴供人工核对
│       │   ├── compare.py        # 同样本、窗口和时限的版本比较
│       │   └── metrics.py        # 分层指标、拒判覆盖率与区间
│       ├── capture/              # SSH/fs_cli、限时抓包、SCP 和批量快照
│       ├── sessions/             # SQLite 会话索引、导出与 HOMER CLI 联动
│       ├── report/               # 多音频/PCAP 与会话证据 HTML 报告
│       └── homer/
│           ├── cli.py            # homer 命令适配，延迟导入客户端
│           └── client.py         # HOMER API、原参数和 JSON/退出码协议
├── tests/
│   ├── audio/                    # 公共音频能力
│   ├── recording_qa/             # 录音规则、工作流和回归
│   ├── capture/                  # 模拟 SSH、抓包边界与批量失败恢复
│   ├── sessions/                 # 多会话关联、PCAP 导出与 HOMER 联动
│   ├── report/                   # 多输入分析、HTML 与错误隔离
│   └── homer/                    # 模拟 HTTP、客户端与入口兼容回归
├── docs/
│   ├── architecture.md
│   ├── recording-qa.md
│   ├── homer.md                  # HOMER 完整手册与迁移说明
│   ├── homer/                    # AI 调用约定、来源和历史资料
│   └── ci.example.yml            # 安装与测试模板，启用时移至 .github/workflows/
└── examples/                     # 不含真实录音的配置示例
```

依赖方向：`cli → tools/<tool> → audio / core`。`audio` 和 `core` 不导入具体工具；工具之间不直接互相导入。报告中与“应答机会”有关的列属于录音质检，不提前抽象成所有工具的通用报告。

`tools/nisqa/` 独立负责可选 CPU 听感评分：CLI 注册不导入运行库；`weights` 显式下载并校验固定 checkpoint；`backend` 延迟加载版本锁定的 TorchMetrics 模型；`service` 流式读取 WAV/FLAC、按显式声道分段并输出 JSONL/CSV。它只依赖公共 `core`，不导入其他工具，也不改变已有 QA 规则。权重和实际录音不随包分发，详见 [NISQA](nisqa.md)。

会话抓包位于 `tools/capture/`，负责 SSH/fs_cli、ESL、BPF、远端 dumpcap 环形/限时任务、冻结与 SCP（保留旧 tcpdump 后端）；联合媒体报告位于 `tools/report/`，复用公共音频读取/健康指标，并独立使用本机 tshark。两者通过版本化的 `capture.json` 和 PCAP 文件交换数据，不直接互相导入。详见[抓包与报告指南](capture-report.md)。

批量抓包产出 `batch.json`、各机 `host.json`、分片 PCAP 和 FS `events.jsonl`（兼容旧 `sessions.jsonl`）。`tools/sessions/` 建立不可变 SQLite 索引，通过文件协议读取这些产物，并通过原 `voice-tools homer` 子进程 CLI 查询 HOMER。会话导出的 `session.json` 和关联回执 `correlation.json` 可被报告工具读取；工具间仍不直接导入实现。详见[批量会话指南](batch-sessions-homer.md)。

2.0 新增 `capture/remote.py` 与 `esl.py` 可独立部署到远端，仅依赖标准库与主机命令。`sessions investigate` 通过公有 CLI 编排 capture/HOMER/index/export/report；SQLite 写入 user_version=2，读取兼容版本 1。公共 `audio/rtp.py` 不依赖具体工具。详见 [2.0 数据与执行边界](capture-v2.md)。

号码抓包由 `capture/numbers.py` 部署并取回，`capture/number_remote.py` 监督限时采集，通过独立 Python 模块进程启动 `sessions/number_bundle.py`。双方使用已停止的采集清单和公共 `core/capture_contract.py` 交换状态；采集工具不导入会话索引/导出的实现。远端运行时 ZIP 仅包含所需的标准库模块，拆包使用目标机 tshark/mergecap。输出协议见[号码抓包](capture-by-number.md)。

SIP 自动拨测位于 `tools/sip/`，通过 `voice-tools sip` 和 `voice-sip` 接入。离线校验/素材层与 PJSUA2 原生子进程分离；`runner.py` 管理有上限的单通话策略，`pcap.py` 用 tshark 和 G.711 解码准备素材，`sipp.py` 管理独立回放包。接收录音与本地播放源重建使用不同产物名称和证据类型。详细契约见 [SIP Agent 文档](sip-ai.md)，使用说明见 [SIP 手册](sip.md)。

## 新工具怎么加入

新增工具时，增加 `tools/<tool>/{cli.py,service.py}`、对应测试与文档，提供 `register(subparsers)` 后加入 `BUILTIN_TOOLS`。命令为 `voice-tools <tool> ...`，库调用直接调用 `service` 中的函数。只有确实被多个工具使用、且语义一致的能力才移入公共层。

通常在 `register` 中声明 argparse 参数并设置 `run(args)`。接入已有独立 CLI 时，可在工具子解析器上设置 `run_argv(argv)`：顶层识别工具名后，将后续参数原样交给它，并保留其退出码。HOMER 使用此方式保留 JSON 参数错误、全局选项在动作前后的位置和独立帮助；适配器只负责转发，不复制业务逻辑。工具名紧跟 `voice-tools` 或 `voice-tools --json`，顶层的 `--help`、`--version` 仍属于工具集。`voice-tools schema` 从当前解析器生成参数声明；音频与 QA 的机器调用约定见 [Agent 接入协议](ai-usage.md)。

新增工具后同步维护 README 工具列表、完整手册、示例与测试；首页只保留用途和快速入口。`--help` 应清楚列出可用工具与操作。

VAD、降噪等重依赖放在可选 extra 中，并在使用时导入。`--help` 不应触发模型下载或加载。第一版支持 Python 3.9+，默认只依赖 NumPy，不需要 GPU、不访问在线模型。

## 稳定边界

- 命令：`voice-tools <tool> <action>`；音频体检与准备使用 `audio inspect / prepare`；录音质检使用 `qa generate / analyze / freeze / promote / evaluate / compare`；HOMER 使用 `homer search / trace / message / export / analyze` 等，并保留 `homerctl` 兼容入口。
- 输出：各工具管理自己的格式和退出码。录音质检记录 `schema_version`、工具版本、输入 SHA-256、参数和结果；HOMER 沿用 `schema_version=1`、`cli_version=1.0.0` 的 JSON 协议，退出码 6 的部分结果仍写入 stdout。不得把一种工具的退出码解释套用到另一种工具。
- 路径：真实录音、生成数据、报告均留在忽略的 `data/`、`outputs/`；禁止把真实录音或凭据提交到代码仓库。
- 批量任务：单条失败不使其他条目丢失；报告保留失败原因，进程以非零状态提示部分失败。
- 可复现：保存参数、内容摘要和版本，随机合成使用固定种子；检测结果不生成自己的测试预期。
- 兼容：破坏性数据格式变更升级 `schema_version`；旧格式应显式拒绝或迁移，不静默猜测。
- 连接：录音质检离线运行；HOMER 在线动作只请求操作者配置的 HOMER API，`schema` 和保存 trace 的 `analyze --input` 可离线运行。HOMER 配置、认证缓存和 `HOMER_*` 环境变量保持原有位置与含义。

## Issue #1 的交付边界

第一版以 PCM16 双声道 WAV 为输入，用户/AI 对应声道可配置。已有业务事件时对齐 AI 接管、应答机会、工具等待、打断和挂机；没有事件时从用户轨活动结束推导低证据候选，不把它等同于语义说完。

合成场景用于工程回归。人工标注需带录音摘要、机会 ID、复核人、时间及判断；通过校验才能晋升黄金集。能量或 VAD 都不能证明“AI 正确回答”，也不能从录音直接判断 LLM、TTS 或 RTP 的根因。

0.2 增加可选 FFmpeg 格式适配、离线复核界面与固定时间轴上的版本评估。后续根据真实样本再做轻量 VAD 标定和日志适配。拆成多个包或服务的触发条件是独立发布、依赖冲突或运行边界确实不同，而不是工具数量增加。

## ViSQOL 可选后端

`tools/visqol` 独立维护 doctor/score/batch；仅依赖公共 audio/core，不导入其他工具。原生 ViSQOL 作为可选本地子进程，安装步骤在 `scripts/install_visqol.py`，不进入主包依赖。输入快照、官方 JSON、命令参数、二进制/模型校验值和逐对失败状态组成可追溯记录；顶层 CLI 的 JSON 协议保持一致。

## 输出间隙与公共复核层（0.14.1）

`tools/gaps/{cli,service,detector,evidence,review,reports}.py` 管理独立检测、旁证和标签。`core/output_events.py` 只负责共享的输出事件合同；`core/rtp_timeline.py` 负责受限时序分片。`core/review/` 提供 HTML 壳、播放/波形 JS、WaveSurfer 资源和 gap 展示适配器，QA 仍保留自己的 review.js 与业务解释。依赖为 QA/gaps/task → core/review → 本地静态资源，公共层不调用检测器或其他工具；RTP/NISQA 通过文件合同交换。默认 QA、report、NISQA 不自动启用新分析，SIP/benchmark 本轮不修改。详见 [合同](output-gaps-contract.md)。
