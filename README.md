# voice_tools · 语音工具导航

面向语音业务的工具集，统一安装，通过 `voice-tools <工具> <操作>` 使用。各工具独立维护功能、配置、输出和测试；从下面选择当前要解决的问题。

**0.2 文档入口：** [人工使用手册](docs/manual.md) · [大模型接入协议](docs/ai-usage.md) · [命令 schema](docs/cli-schema.json)。运行 `voice-tools schema` 发现参数，再用 `voice-tools --json …` 获取结构化结果。

## 工具列表

| 工具 | 适用场景 | 命令入口 | 使用文档 |
|---|---|---|---|
| **录音体检与准备** | 查看逐轨音频健康指标，转换为保留声道和时间映射的 PCM16 WAV | `voice-tools audio inspect / prepare` | [人工使用手册](docs/manual.md) |
| **双声道录音质检** | 批量筛查用户发言后 AI 无声或延迟输出的候选片段，生成试听报告与人工复核表 | `voice-tools qa` | [录音质检指南](docs/recording-qa.md) · [事件示例](examples/call.events.json) |
| **HOMER 7 CLI** | 按号码、Call-ID、时间等查询 SIP，追踪通话、导出报文并检查 UDP 风险线索 | `voice-tools homer` | [HOMER 完整手册](docs/homer.md) · [AI 调用约定](docs/homer/ai-usage.md) |
| **复核与版本评估** | 波形/单轨试听/标注，冻结时间轴，比较固定黄金集上的两个版本 | `voice-tools qa freeze / promote / evaluate / compare` | [人工手册](docs/manual.md) · [Agent 协议](docs/ai-usage.md) |

录音体检与质检在本机 CPU 上离线运行；HOMER 查询连接你配置的 HOMER 7 服务，保存的 trace 可以离线分析。所有工具都不需要 GPU 或在线模型。

## 安装与命令导航

Python 3.9+，建议使用独立虚拟环境。在仓库根目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .

voice-tools --help
voice-tools schema --tool qa
voice-tools audio --help
voice-tools qa --help
voice-tools homer --help
```

默认依赖 NumPy，用于录音质检；HOMER 工具自身仅用 Python 标准库。可选 CPU WebRTC VAD：`python -m pip install -e '.[vad]'`。也可用 `python -m voice_tools` 代替 `voice-tools`。

格式准备使用本机可选的 FFmpeg / FFprobe。原生 PCM16 WAV 体检无需它们。音频/QA 的 `--json` 放在工具名之前；HOMER 保留其原有 JSON 协议和退出码。

## 快速体验

### 双声道录音质检

```bash
# 生成 20 种合成场景，再输出 JSONL、CSV 和可离线试听的 HTML
voice-tools qa generate --out data/demo
voice-tools qa analyze data/demo --out outputs/demo --include-audio
open outputs/demo/review.html  # macOS；其他系统用浏览器打开
```

每次使用新的输出目录。合成声音只验证工程规则；真实录音需核实双方声道，结果仍需人工复核。输入格式、事件对齐、批处理和黄金集评估见[完整指南](docs/recording-qa.md)。

### HOMER 7 查询与分析

```bash
# 离线体验，无需服务器或凭据
voice-tools homer schema
voice-tools homer analyze --input examples/homer/sample-trace.json

# 连接自己的服务：将示例地址和账号替换为实际值
voice-tools homer init --url https://homer.example.net --username operator
voice-tools homer login
voice-tools homer doctor
voice-tools homer search --since 15m --caller 1001 --transport udp --all
```

已将原 `homer-cli / homerctl 1.0.0` 纳入本仓库，安装后也提供 `homerctl` 兼容命令。原来的 `HOMER_*` 环境变量、配置与令牌缓存继续适用；迁移说明见 [HOMER 手册](docs/homer.md#从独立版迁移)。

HOMER 的搜索退出码 **6** 表示返回了部分结果，应继续读取 stdout JSON。报文大小与重建 PCAP 只能提供风险线索，不能证明原始 IP 分片。支持的接口版本、全部参数和上线核对步骤见[完整手册](docs/homer.md)。

## 开发与新增工具

| 入口 | 内容 |
|---|---|
| [架构与扩展约定](docs/architecture.md) | 模块边界、命令注册、新增工具步骤 |
| [工具源码](src/voice_tools/tools) | `audio/`、`recording_qa/` 与 `homer/`，各自维护业务逻辑 |
| [测试](tests) | 录音场景、人工复核工作流、HOMER 模拟 HTTP 与统一入口回归 |
| [CI 模板](docs/ci.example.yml) | Python 3.9 / 3.12 / 3.13 测试模板，尚未启用 |
| [HOMER 整合来源](docs/homer/integration.md) | 原包校验值、迁移范围与保留的历史资料 |

```bash
python -m unittest discover -s tests -v
```

新增工具时提供独立模块、文档和测试，注册命令后在本页工具列表增加入口。详细操作放在各工具手册，首页持续作为导航。

真实录音、通话导出、凭据、虚拟环境和运行结果不提交；本地数据与输出放在忽略的 `data/`、`outputs/` 中。`--include-audio` 会将原录音复制到质检报告，分享时按原录音的权限处理。
