# E2E 优先的测试精简

基线 `653288f`（PR #38 后主线），分支 `chore/v0.18.3-e2e-test-pruning`，交付 [PR #40](https://github.com/lovezhl456/voice_tools/pull/40)。迭代、分支、包元数据及 CLI schema 快照统一为 `0.18.3`；除版本标识外，业务逻辑不变。

## 最终去留

逐项审核 651 个 Python 方法和 54 个 Node 用例，最终删除 **19 项：17 个 Python 方法、2 个 Node 用例**。保留 634 个 Python 方法，原有 46 个安装包浏览器场景全部保留。

| 审查范围 | 原始项数 | 删除 | 保留 | 逐项证据 |
|---|---:|---:|---:|---|
| QA、检测、间隙 | 236 | 7 | 229 | [清单](test-pruning-qa.json) |
| SIP、抓包、会话、报告、时序、HOMER | 314 | 8 | 306 | [清单](test-pruning-sip.json) |
| 音频、模型、任务、latency、Studio、公共入口 | 155 | 4 | 151 | [清单](test-pruning-other.json) |
| 合计 | 705 | 19 | 686 | Python 包装器与 Node 用例分别登记，不能相加视为独立业务场景数 |

清单记录每个原始测试的 ID、去留、实际层级、具体故障或重复原因、覆盖引用。删除项的引用必须能说明同一入口、输入、分支、结果已被覆盖；保留项的引用说明已检查但仍有缺口的流程，不代表那个 E2E 能替代它。Python 原始／最终 AST 对账无遗漏、无新增方法，Node 按实际标题对账。

保留项包括 311 个有明确失败模式的隔离测试、336 个集成测试和 39 个 E2E。没有把普通函数调用改称 E2E。剩余删除项主要是已由真实流程覆盖的正常行为、页面结构检查、同一函数自比较及没有独立业务约定的默认值／数组顺序断言。

## 审查修复与删除门槛

[PR #39](https://github.com/lovezhl456/voice_tools/pull/39)的七条意见先恢复了八个原有方法；本轮对 PR #40 的十三项发现及剩余删除项整体复核，又恢复 25 个 Python 方法和 1 个 Node 用例。恢复的方法与原始主线实现一致，没有编写代码后再补新的单元测试。

| 本轮问题 | 完成的修复 |
|---|---|
| CLI schema 快照版本落后 | 从实际 CLI 重生成 `docs/cli-schema.json`，与运行时逐字段相等，唯一版本变化为 0.18.1 → 0.18.3 |
| detect、ViSQOL 发现及 `--json homer` 入口缺失覆盖 | 恢复命令级检查，另执行安装包的实际发现命令并保存 JSON 回执 |
| `qa.assess-evaluate` 文件输出及执行端路径边界 | 恢复 QA／latency catalog 检查；实际 pack/run 生成 `steps/evaluate/output.json`，实际 pack 拒绝任务内模型／引擎路径 |
| 多主机异常与整体 partial 状态 | 恢复外层异常隔离和整体状态检查，区分 host 内部失败字典与 future 直接抛错 |
| 单次 dumpcap、非默认 SIP 端口、早期 SDP、RTP 独立包数 | 恢复各自路径和精确结果断言，不能用其他后端、默认端口或已完整 tags 的输入替代 |
| 默认缓存目录和直流偏移指标 | 恢复 XDG 路径和 DC 健康指标检查，保留可见诊断及真实配置选择 |
| 剩余混合用例中的独有断言 | 保留依赖缺失诊断、PCM16 精确读写、静音总量、缺证据状态、发送旁证、步骤回执、旧媒体解绑、场景读写契约、最小配置归一化及特殊文件名原文显示 |

已发现的缺口通过受控故障注入核对：相关保留用例仍可通过，被删旧用例能发现错误；恢复后对五类 SIP 故障和四项任务／发现保护重新做定向故障注入，均能捕获。实验只作用于临时源码副本或内存补丁，未修改产品代码。没有据此宣称所有 E2E 都在全部 mutation 下运行通过。

[AGENTS.md](../AGENTS.md) 保留用户要求的三条规则：代码完成后绝不补单元测试；强烈优先用 E2E 并生成可验证、可重复产物；必须隔离测试时，先写下所有可能失败方式。另明确：不能因为断言涉及常量、schema 或命令参数就删除；不同入口／后端／输入不能视为等价覆盖；混合用例保留独有断言；证据不足时保留并说明，不以删除数量为目标。

[复核验收基线](review-acceptance.md) 继续使用实际安装包的 R05：先由夹具运行实际分析并断言 `NEEDS_REVIEW`，再在浏览器验证原因。旧输出的未知延迟等独有细节另由恢复的规则检查保护。

## 最终验证

普通回归使用 Python 3.12、NumPy 2.3.5、soundfile 0.13.1；浏览器使用 Playwright 1.63.0／Chromium 1243。沿用同一个已构建的 0.18.3 wheel；检测运行器逐文件确认安装包与最终产品源码一致，无需重复构建。

| 验证 | 结果与边界 |
|---|---|
| Python 默认全量 | 634 项：596 通过、38 个原有条件跳过；62.395 秒 |
| Studio Node | 23 通过，无跳过 |
| Studio／CLI 兼容 | 9 场景：16 次成功命令、1 次预期拒绝 |
| 安装包复核 R01–R08 | 桌面／手机 16 项通过，无失败、跳过或重试 |
| 安装包检测／编辑／工作区 | 桌面／手机 30 项通过，无失败、跳过或重试 |
| 实际安装 CLI 与任务包 | 23 次调用通过，238 份摘要产物；包括发现命令、生成／分析、独立 HOMER 入口、文件输出及 runtime 路径拒绝 |
| 原生 SIP／时序 | 复用同任务前轮 27 项真实本机回环通过的证据；业务逻辑、原生测试和环境未变，本轮未重复呼叫 |
| 清单／文档／可读性 | 逐项身份、引用、链接、生成快照、最终差异与 `code-readability` 核查；独立复核无剩余具体阻断 |

Python 的专项跳过不表示真实模型验收。真实 QA／NISQA 模型、latency 真实引擎、ViSQOL 实评分及生产线路／业务准确率未在本轮执行；相关专项保留。未做同负载性能对比，不宣称提速比例。

## 复现与产物

以下命令从工作树根目录执行，`python` 指向已具备相应依赖的解释器。每次使用新的产物目录；浏览器准备见[测试指南](testing.md)。可通过 `PLAYWRIGHT_BROWSERS_PATH` 使用已有匹配浏览器。

```bash
python scripts/check_review.py --out .artifacts/review-final
python scripts/check_detection.py --package-root .artifacts/review-final/package --out .artifacts/detect-final
env -u VOICE_TOOLS_SIP_LOOPBACK -u VOICE_TOOLS_NISQA_MODEL_DIR -u VOICE_TOOLS_NISQA_AUDIO -u VOICE_TOOLS_TEST_LATENCY_DIR -u VOICE_TOOLS_TEST_QA_MODEL_DIR PYTHONPATH="$PWD/src" python -m unittest discover -s tests -v
node --test tests/studio/core.test.cjs
PYTHONPATH="$PWD/src" python tests/studio/check_cli_compat.py
```

若复用既有 wheel，将 `--wheel <wheel-path>` 传给 `check_review.py`。本轮产物位于 `.artifacts/pr40-repair-review`、`.artifacts/pr40-repair-detection` 和 `.artifacts/pr40-repair-cli`，包含 wheel、合成录音、分析 JSONL、导出 CSV／JSON、截图、命令、退出码与 SHA-256。安装包摘要沿用 `.artifacts/v0.18.3-review/verification.json` 中记录的值；最终 `.artifacts/pr40-repair-verification.json` 汇总提交及产物摘要。产物仅保存在本地，不上传个人环境路径或业务录音。

安装后的实际发现命令示例（先执行上面的安装包流程，输出目录必须新建）：

```bash
mkdir -p .artifacts/discovery-check
PYTHONPATH="$PWD/.artifacts/review-final/package" .artifacts/review-final/package/bin/voice-tools schema > .artifacts/discovery-check/schema.json
PYTHONPATH="$PWD/.artifacts/review-final/package" .artifacts/review-final/package/bin/voice-tools --json detect schema > .artifacts/discovery-check/detect-schema.json
PYTHONPATH="$PWD/.artifacts/review-final/package" .artifacts/review-final/package/bin/voice-tools --json detect catalog > .artifacts/discovery-check/detect-catalog.json
PYTHONPATH="$PWD/.artifacts/review-final/package" .artifacts/review-final/package/bin/voice-tools schema --tool visqol > .artifacts/discovery-check/visqol-schema.json
PYTHONPATH="$PWD/.artifacts/review-final/package" .artifacts/review-final/package/bin/voice-tools --json homer schema > .artifacts/discovery-check/homer-schema.json
```

完整的 23 条本轮入口命令、任务定义、复现脚本与文件摘要在 `.artifacts/pr40-repair-cli/`。HOMER 使用仓库离线样例；QA 使用固定种子合成录音；任务检查模型／引擎路径时在打包阶段即拒绝，不调用业务服务。
