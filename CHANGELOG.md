# 版本记录与迭代规则

## 0.11.1 · ViSQOL 全参考音质评估

- 状态：实现与本地验收完成，待 PR 审阅，尚未合并交付。
- 工作分支：`feat/v0.11.1-visqol`；已同步主线 `14ed371`，保留 NISQA 和 SIP 批量功能。
- 范围：`voice-tools visqol doctor / score / batch`、固定版本 CPU 安装脚本、官方样本实战、下载/安装/使用文档与结构化结果。
- 本机已完成：M1 Max / 32 GiB 源码编译、语音/一般音频真实评分、独立 wheel 评分、批次部分失败与证据保留；上游 20 项一致性和 3 项 TFLite 测试通过。
- 主线合入后全仓 354 项，322 项执行通过、32 项可选测试跳过。最终 CSV 错误处理小修复后 17 项 ViSQOL 专项和重建 wheel 真实评分通过。
- 最终验证与资源记录见 [ViSQOL 本机实战](docs/visqol-local-validation.md)；Linux 与生产通话未验收。
- PR：[#18](https://github.com/lovezhl456/voice_tools/pull/18)，待审阅，尚未合并。

## 0.10.1 · NISQA 本地听感评分

- **状态：已随 [PR #17](https://github.com/lovezhl456/voice_tools/pull/17) 合入主线，合并提交 `14ed371`。** 以下保留该迭代的验收记录。
- 开发分支：`feat/v0.10.1-nisqa`，从已同步的 `origin/main`（`d44599c`）创建。
- `0.9.1` 已随 SIP 批量队列 PR #16 合入主线（`374456f`），本分支已同步该更新，新功能使用 `0.10.1`。
- 目标：可选 CPU NISQA 依赖、离线诊断、显式下载/哈希校验、按声道分段批量评分、JSON/JSONL/CSV 输出和 Linux/Mac 文档。权重、录音、环境和结果不入 Git。
- 已实现：`voice-tools nisqa download / doctor / analyze`、固定权重下载与 SHA-256 校验、逐声道流式分段评分、静音/短段证据不足状态、JSONL/CSV 结果，以及不包含模型的可选依赖安装包。
- 本地验证：Mac M1 Max / 32 GiB，官方权重真实推理成功；离线结果与 TorchMetrics 1.9.0 对齐。合入最新主线后全仓 337 项测试，317 项执行通过、20 项可选测试跳过；基础 wheel 在未安装 Torch 的环境中完成命令发现与诊断。
- 资源实测：预热后重复合成语音 286.017 秒，29 段评分，总墙钟 3.808 秒，子进程峰值 RSS 480.563 MiB。条件和限制见 [NISQA 验收记录](docs/nisqa-validation.md)，不是生产容量承诺。
- 尚未验证 Linux、真实通话准确度与生产负载；权重含非商业许可限制。详见 [安装使用文档](docs/nisqa.md)。

本文件是 voice_tools 的统一版本台账。规则自 2026-09-19 起使用；每次迭代在这里登记范围、状态和对应分支，交付时补充 PR／提交及验证结果。

## 版本规则

项目使用 `0.功能版本.修订版本`，例如 `0.8.1`。这是本项目的迭代规则，不按语义化版本的破坏性变更规则自动提升大版本。

| 位数 | 含义 | 规则 |
|---|---|---|
| 第一位 | 大版本，当前为 `0` | 只由项目所有者决定，开发者不得自行提升 |
| 中间位 | 功能版本 | 增加独立新功能时，在当前功能版本上加 `1`，末位重置为 `1` |
| 最后一位 | 修订版本 | 已有功能更新或修复时加 `1`；每个功能版本从 `1` 开始 |

- 全项目共用一个版本序列，功能版本不是某个模块的固定编号。修复旧模块也基于当前项目版本递增末位，不回退到旧版本。
- 一次迭代确定一个目标版本；开发、测试以及交付前发现的问题修复都属于该版本，不随每次提交递增。交付后新增更新或修复再递增。
- 同一迭代同时包含新功能和修复时，按新功能递增中间位，修复记录在同一版本内。
- 文档整理、补充历史记录和记录验证结果不单独消耗版本号；实际行为改变按上面的规则分类。
- 分支从最新 `main` 创建；并行迭代须先登记目标版本，合并前核对是否被其他已交付迭代占用，避免重复版本。
- 交付时保持包版本、CLI `--version`、当前机器接口文档及生成的 schema 中的 `tool_version` 一致。JSON 的 `schema_version`、第三方工具版本和历史报告独立管理，不机械替换。
- 状态区分“计划中”“开发中”“已交付”。不能把已规划功能或仅推送开发分支记成已交付，也不能用历史测试结果代替本次验证。

## 分支、PR 与标签

| 用途 | 命名示例 |
|---|---|
| 新功能分支 | `feat/v0.8.1-sip-assertions` |
| 已有功能更新 | `enhance/v0.8.2-assertion-report` |
| 问题修复 | `fix/v0.8.3-dtmf-detection` |
| 仅文档维护 | `docs/v0.7.1-version-record`，沿用对应版本 |
| PR 标题 | `[0.8.1] 增加 SIP 结构化测试断言` |
| 发布标签 | `v0.8.1`，只指向已交付提交 |

上述名称中的功能说明可以按任务调整；版本号必须与本台账一致。历史分支和提交保留原名，不为符合新规则改写历史。

所有改动均采用“工作分支 → 推送工作分支 → 创建以 `main` 为目标的 PR → 按授权合并”的流程，代码、文档及项目约定均适用。禁止直接修改、提交或推送 `main`，也禁止本地合并后直推 `main` 绕过 PR。创建 PR 不等于获得合并授权。

## 当前状态与编号迁移

- 当前已合入主线的基线为 **`0.10.1`**，对应 [PR #17](https://github.com/lovezhl456/voice_tools/pull/17) 合并提交 `14ed371`。
- 当前 ViSQOL 分支已同步 NISQA 与 SIP 批量功能，源码、CLI 和机器接口文档统一为 **`0.11.1`**；合并前不标为已交付。
- 本次功能交付目标：**`0.11.1`**，待 PR 验证及合并。
- 下表是根据现有 Git 合并历史重新编排的版本，不表示过去实际发布过这些版本号；建立台账时仓库没有发布标签。不补打历史发布标签，不改写旧报告中的原版本号。

## 历史迭代编排

| 版本 | 已合入的功能／更新 | Git 依据 |
|---|---|---|
| `0.1.1` | 多工具框架、双声道录音质检 | [PR #2](https://github.com/lovezhl456/voice_tools/pull/2) · `51712a4` |
| `0.2.1` | HOMER 查询工具与工具导航 | [PR #3](https://github.com/lovezhl456/voice_tools/pull/3) · `2d4c296` |
| `0.3.1` | 音频准备、人工复核、黄金集评估与机器接口 | [PR #4](https://github.com/lovezhl456/voice_tools/pull/4) · `781c506` |
| `0.4.1` | 会话抓包与 HOMER 关联 | [PR #5](https://github.com/lovezhl456/voice_tools/pull/5) · `5769508` |
| `0.4.2` | 抓包能力扩展：环形保留、ESL、媒体分析 | [PR #6](https://github.com/lovezhl456/voice_tools/pull/6) · `dda856c` |
| `0.5.1` | SIP 自动拨测与互通测试 | [PR #7](https://github.com/lovezhl456/voice_tools/pull/7) · `e641836` |
| `0.5.2` | 已有复核界面的波形、试听与标注更新 | [PR #8](https://github.com/lovezhl456/voice_tools/pull/8) · `2181b25` |
| `0.5.3` | SIP 生命周期与清理修复 | [PR #9](https://github.com/lovezhl456/voice_tools/pull/9) · `e67fdaf` |
| `0.5.4` | 抓包完整性与媒体分析修复 | [PR #10](https://github.com/lovezhl456/voice_tools/pull/10) · `6849b18` |
| `0.6.1` | 按主叫／被叫采集与会话包下载 | [PR #11](https://github.com/lovezhl456/voice_tools/pull/11) · `e523373` |
| `0.7.1` | SIP 用例编排工作台原型 | [PR #12](https://github.com/lovezhl456/voice_tools/pull/12) · `4d003a6` |
| `0.8.1` | SIP 结构化测试断言 | [PR #14](https://github.com/lovezhl456/voice_tools/pull/14) · `d44599c` |
| `0.9.1` | SIP 批量队列与 SIPp 压力测试 | [PR #16](https://github.com/lovezhl456/voice_tools/pull/16) · `374456f` |
| `0.10.1` | NISQA 本地听感评分 | [PR #17](https://github.com/lovezhl456/voice_tools/pull/17) · `14ed371` |

历史功能的验证范围以各 PR 和当时的验收记录为准；此表只记录功能归属与合入证据。

## 0.7.1 · sngrep 联动文档补充

- 文档分支：`docs/v0.7.1-sngrep-guide`；沿用当前已交付基线，不新增软件版本。
- 新增[联动快速说明](docs/sngrep-workflow.md)和[安装使用手册](docs/sngrep.md)，接入首页、人工手册及抓包文档导航。
- 推荐通过 PCAP 文件联动现有采集、sngrep 人工时序查看和媒体报告；不新增采集后端或修改运行行为。
- 验证：9 条离线转换/合并/sngrep 命令通过，完整样本的 52 包保持可读取；号码采集 dry-run、3 条 voice_tools 示例参数、14 个 Bash 代码块及新增文档的 11 处本地链接核对通过。终端中实际打开 PCAP 并进入时序窗口。
- 状态：已随 [PR #15](https://github.com/lovezhl456/voice_tools/pull/15) 合入（`600888e`）；Linux 安装、实时抓包及生产环境未验收。

## 0.8.1 · SIP 结构化测试断言

- **状态：已合入主线，PR #14，合并提交 `d44599c`。** 以下保留该迭代的验证记录，不作为 NISQA 本轮验收结果。
- 目标开发分支：`feat/v0.8.1-sip-assertions`。
- 参考 VoIP Patrol，增加可配置的应答码、接收 RTP、最小有效音频、预期 DTMF／音调断言。
- 每项断言输出预期值、实测值、证据和通过／失败／证据不足状态，并纳入整体结果及退出码。
- 接收 RTP 与接收录音有效音频分别取证；发出的 DTMF 不能作为接收到预期 DTMF 的证据；能量或音调符合预期不等于业务语义正确。
- 已实现：场景严格校验，PJSUA2 接收证据采集，五类断言（三态逐项结果），`assertions.json` 与总状态／退出码聚合，预期拒接测试及 0.8.1 版本迁移。详细边界见 [断言文档](docs/sip-assertions.md)。
- 本轮离线 SIP／指标／失败路径测试：49 项通过；样例校验、预演、版本／schema 一致性、文档链接和 diff 检查通过。
- 最终全仓验证：`VOICE_TOOLS_SIP_LOOPBACK=1 .venv/bin/python -m unittest discover -s tests -v`，**299 项全部通过，无跳过**，包含 **19 项真实本机 SIP 测试**。先前沙箱端口限制已通过用户授权的本机验证解除。
- 原生验证覆盖预期 486、实际接收 RTP、静音 RTP、无 RTP、无应答、重复 RFC4733 DTMF、SIP INFO DTMF、双频音调和既有生命周期回归。修正 SWIG 所有者对象生命周期读取，并让测试对端按对方 SDP 声明的 PT 回传 DTMF；修复后全仓复测通过。
- 尚未验证生产网关／运营商线路、NAT；本轮不修改或发布 HTML。合入 [PR #14](https://github.com/lovezhl456/voice_tools/pull/14)／`d44599c`。

## 0.9.1 · SIP 批量队列与 SIPp 压力测试

- **状态：已随 PR #16 合入主线（`374456f`）。**
- 目标分支：`feat/v0.9.1-sip-batch-sipp`。基于已合入主线的 `0.8.1` SIP 断言功能。
- 增加功能拨测批量队列、并发上限、端口隔离、逐项结果与中断清理。
- 可视化工作台生成批量执行文件及独立 SIPp 场景包，提供安装、使用和结果边界文档。
- 验证：284 项完整回归（11 跳过）、42 项受影响范围复验、14 项批量专项、22 项 Studio 核心、9 组 CLI 兼容场景通过；桌面 / 手机 UI 与本机 4 通并发 2 验收通过。RFC4733 原始发包受 macOS 权限限制，详见 [验收记录](docs/sip-batch-validation.md)。
- PR：[#16](https://github.com/lovezhl456/voice_tools/pull/16)，合并提交 `374456f`。以下是该迭代的验收记录，不作为本轮 NISQA 验收。

- 2026-09-20 main integration: 315 Python tests passed (0 skipped), 22 Studio tests and 9 CLI compatibility cases passed; native batch assertion pass/fail aggregation verified. See [validation](docs/sip-batch-validation.md).
