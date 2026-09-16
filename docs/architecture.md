# 代码结构与扩展约定

## 选择

采用 **单仓库、单 Python 包、多个独立工具**。统一安装、命令风格和结果封套；每个工具拥有自己的检测规则、配置、测试和使用文档。当前只实现录音质检，不为尚未明确的工具预建空模块，也不引入服务、数据库或插件框架。

```text
voice_tools/
├── pyproject.toml                 # 包、命令入口、可选依赖
├── src/voice_tools/
│   ├── cli.py                    # voice-tools 入口，只负责装配
│   ├── audio/
│   │   ├── io.py                 # WAV、采样率、声道、文件完整性
│   │   └── activity.py           # 能量活动 / 可选 WebRTC VAD
│   ├── core/
│   │   └── files.py              # JSON、摘要、输出目录保护
│   └── tools/
│       ├── __init__.py            # 内置工具注册表
│       └── recording_qa/
│           ├── cli.py            # qa 子命令适配
│           ├── detector.py       # 应答窗口、排除与候选判定
│           ├── scenarios.py      # 可复现合成场景及独立预期
│           ├── batch.py          # 批量调度、逐文件错误隔离
│           ├── reports.py        # 此工具专属的可读报告
│           └── review.py         # 人工标注、黄金集、评估
├── tests/
│   ├── audio/                    # 公共音频能力
│   └── recording_qa/             # 工具规则、工作流和回归
├── docs/
│   ├── architecture.md
│   ├── recording-qa.md
│   └── ci.example.yml            # 安装与测试模板，启用时移至 .github/workflows/
└── examples/                     # 不含真实录音的配置示例
```

依赖方向：`cli → tools/<tool> → audio / core`。`audio` 和 `core` 不导入具体工具；工具之间不直接互相导入。报告中与“应答机会”有关的列属于录音质检，不提前抽象成所有工具的通用报告。

## 新工具怎么加入

例如未来新增格式转换：增加 `tools/convert/{cli.py,service.py}`、`tests/convert/` 和 `docs/convert.md`，提供 `register(subparsers)` 后加入 `BUILTIN_TOOLS`。命令为 `voice-tools convert ...`，库调用直接调用 `service` 中的函数。只有确实被多个工具使用、且语义一致的能力才移入公共层。

VAD、降噪等重依赖放在可选 extra 中，并在使用时导入。`--help` 不应触发模型下载或加载。第一版支持 Python 3.9+，默认只依赖 NumPy，不需要 GPU、不访问在线模型。

## 稳定边界

- 命令：`voice-tools <tool> <action>`；录音质检使用 `qa generate / analyze / promote / evaluate`。
- 输出：含 `schema_version`、工具版本、输入文件 SHA-256、参数、结果和错误。具体业务字段由工具管理。
- 路径：真实录音、生成数据、报告均留在忽略的 `data/`、`outputs/`；禁止把真实录音或凭据提交到代码仓库。
- 批量任务：单条失败不使其他条目丢失；报告保留失败原因，进程以非零状态提示部分失败。
- 可复现：保存参数、内容摘要和版本，随机合成使用固定种子；检测结果不生成自己的测试预期。
- 兼容：破坏性数据格式变更升级 `schema_version`；旧格式应显式拒绝或迁移，不静默猜测。

## Issue #1 的交付边界

第一版以 PCM16 双声道 WAV 为输入，用户/AI 对应声道可配置。已有业务事件时对齐 AI 接管、应答机会、工具等待、打断和挂机；没有事件时从用户轨活动结束推导低证据候选，不把它等同于语义说完。

合成场景用于工程回归。人工标注需带录音摘要、机会 ID、复核人、时间及判断；通过校验才能晋升黄金集。能量或 VAD 都不能证明“AI 正确回答”，也不能从录音直接判断 LLM、TTS 或 RTP 的根因。

后续根据真实样本再增加 FFmpeg 格式适配、轻量 VAD 标定、日志适配器和人工标注界面。拆成多个包或服务的触发条件是独立发布、依赖冲突或运行边界确实不同，而不是工具数量增加。
