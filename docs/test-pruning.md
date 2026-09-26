# E2E 优先的测试精简

基线 `653288f`（PR #38 后主线），分支 `chore/v0.18.3-e2e-test-pruning`。迭代、分支和包元数据统一为 `0.18.3`；除版本标识外，业务逻辑不变。

## 去留结果

逐项审核 651 个 Python 方法和 54 个 Node 用例，删除 45 项：42 个 Python 方法及 3 个 Node 用例。保留的 Python 方法为 609 个。原有 46 个安装包浏览器场景全部保留。

| 并行审查范围 | 原始项数 | 删除 | 保留 | 逐项证据 |
|---|---:|---:|---:|---|
| QA、检测、间隙 | 236 | 14 | 222 | [清单](test-pruning-qa.json) |
| SIP、抓包、会话、报告、时序、HOMER | 314 | 20 | 294 | [清单](test-pruning-sip.json) |
| 音频、模型、任务、latency、Studio、公共入口 | 155 | 11 | 144 | [清单](test-pruning-other.json) |
| 合计 | 705 | 45 | 660 | Python/Node 包装器分别记录，不能相加作为独立业务场景数 |

清单记录每个原始测试的 ID、去留、实际测试层级、具体故障或重复原因及覆盖引用。`coverage_reference` 对删除项表示替代覆盖或相关流程，对保留项表示已检查但仍有缺口的流程；它不表示那些 E2E 已覆盖保留项。Python 清单与原始／最终 AST 对账，所有原始方法恰好有一条决策，没有新增方法；Node 按实际测试标题对账。

删除范围包括：重复安装包浏览器正常路径、重复真实 SIP 回环的模拟行为、页面占位标记与内部调用次数、同一函数的自比较、重复命令发现字段检查。对仍有独有失败断言的混合用例，只移除重复静态断言。

保留项包含 292 个有明确覆盖缺口的隔离测试、329 个集成测试及 39 个 E2E；这不是将全部旧单元测试重新命名为 E2E。保留原因包括损坏／篡改证据、事务回滚、边界时序、资源上限、进程退出清理和模型／下载失败。现有 E2E 的输入无法触发这些情况，不能以名称或代码行数作为删除理由。

## 独立复核与规则

交叉审查恢复了五项存在独有覆盖的旧测试：tcpdump 命令资源限制、author 工作台生成资源、初始化模板有效性、确定性音频摘要、安装器与发布包依赖一致性。分别核对了实际调用路径，未将 dumpcap、结果复核页面、另一份场景校验、单次生成或假 pip 成功当作替代证据。SIP 失败路径仍复用原夹具，已解除夹具对被删测试方法名的依赖。

[AGENTS.md](../AGENTS.md) 已加入用户要求的三条规则：代码完成后绝不补单元测试；强烈优先用 E2E 作为唯一机制并生成可验证、可重复产物；必须隔离测试时，先写下所有可能的失败方式，再写代码。测试指南和 PR 模板同步要求注明隔离例外及产物。

最终差异按 `code-readability` 核查：保留测试名称、断言意图、公共行为和夹具清理顺序，清理删除后的空行及无用导入。未修改业务接口、浏览器基线或可选专项开关，未新增跳过。

## PR 审查修复

[PR #39 的审查](https://github.com/lovezhl456/voice_tools/pull/39#discussion_r4109690890)指出五组缺失覆盖及版本、文档问题。修复后恢复 8 个原有方法，没有新写单元测试：

| 问题 | 修复及可执行证据 |
|---|---|
| QA 生成命令未实际执行 | 恢复 `test_json_generator_preserves_all_scenarios`；安装包 CLI 生成两批录音、比对 manifest 并实际分析 |
| QA schema 发现缺少保障 | 恢复 `test_schema_is_offline_and_covers_actual_p0_commands`；安装包 `schema --tool qa` 另保留 JSON 回执 |
| macOS TFLite 只剩拒绝路径 | 恢复 `test_tflite_patch_is_exact_and_idempotent`，验证合法文件成功修改及重试；不以此声称真实 ViSQOL 构建通过 |
| latency 显式路径优先级缺口 | 恢复 `test_normalized_inputs_and_precedence`，同一环境下同时设置显式路径和环境变量 |
| HOMER 独立入口未覆盖 | 恢复 schema／离线分析／错误／全局选项的 4 项入口测试；实际安装的 `homerctl` 与 `voice-tools homer` 对比通过 |
| 版本与已有台账倒序 | 版本、分支、包元数据及本文统一为 0.18.3；新 PR 接续 #39 并保留其审查链接 |
| 验收基线引用已删方法 | [复核基线](review-acceptance.md)指向现存 R05、实际安装包分析断言及保存的 `assessment.jsonl` |

两份清单同步修正分类与去留理由。直接创建输入文件不能替代 `qa generate` 的命令分发；已安装后端不能替代安装补丁；统一 CLI 也不能替代独立的 `homerctl` 入口。

## 验证与复现

使用 Python 3.12、NumPy 2.3.5、soundfile 0.13.1 运行普通回归；真实 SIP 回环复用已有 Python 3.9／PJSUA2 环境，所有呼叫限制在 `127.0.0.1`。浏览器使用锁定的 Playwright 1.63.0／Chromium 1243。命令从工作树执行，`python` 指向相应依赖环境。

```bash
python scripts/check_review.py --out .artifacts/v0.18.3-review
python scripts/check_detection.py --package-root .artifacts/v0.18.3-review/package --out .artifacts/v0.18.3-detection
env -u VOICE_TOOLS_SIP_LOOPBACK -u VOICE_TOOLS_NISQA_MODEL_DIR -u VOICE_TOOLS_NISQA_AUDIO -u VOICE_TOOLS_TEST_LATENCY_DIR -u VOICE_TOOLS_TEST_QA_MODEL_DIR PYTHONPATH="$PWD/src" python -m unittest discover -s tests -v
node --test tests/studio/core.test.cjs
PYTHONPATH="$PWD/src" python tests/studio/check_cli_compat.py
VOICE_TOOLS_SIP_LOOPBACK=1 PYTHONPATH="$PWD/src" python -m unittest tests.sip.test_loopback tests.benchmark.test_native -v
```

再次执行浏览器命令时改用新的产物目录；可通过 `PLAYWRIGHT_BROWSERS_PATH` 指定已下载的匹配浏览器。原生回环命令需使用能导入 PJSUA2 的解释器。

| 最终验证 | 结果 |
|---|---|
| 安装 wheel 复核 R01–R08，桌面／手机 | 16 通过，无失败、跳过、重试 |
| 安装 wheel 检测／编辑／工作区，桌面／手机 | 30 通过，无失败、跳过、重试 |
| Python 默认全量 | 609 项：571 通过、38 个原有条件跳过；60.817 秒 |
| Studio Node | 23 通过 |
| Studio／CLI 兼容 | 9 场景，16 次成功命令、1 次预期拒绝 |
| 安装包 CLI 入口 | 14 次调用通过；生成、分析、独立入口兼容及错误拒绝；195 份带摘要产物 |
| 真实本机 SIP／时序 | 同任务前轮 27 通过，74.194 秒；此轮相关测试／业务代码及原生环境未变，复用该证据 |

浏览器产物包括 wheel、合成录音、导出 CSV／JSON、截图、逐项结果、`verification.json`，位于上述 `.artifacts/v0.18.3-review` 与 `.artifacts/v0.18.3-detection` 目录。安装包 SHA-256 为 `8e643c990dc1fae9c5bb5eb6e57b7d38b50db827dd81527e3eefd8d52bbe6003`；检测运行器逐文件核对安装包与当前产品源码一致。附加 `.artifacts/v0.18.3-verification.json` 汇总最终代码文件摘要、命令结果与产物摘要。本地产物不上传个人环境路径或业务录音。

这些结果验证合成输入、离线工具和本机回环，不代表真实运营商线路或业务录音准确率。真实 QA／NISQA 模型、latency 真实引擎和 ViSQOL 实评分未在本轮执行；相关原有专项仍保留。没有做相同负载的前后性能实验，不宣称提速比例。

安装后入口的最小复现（复用上面的安装目录，产物目录必须新建）：

```bash
mkdir -p .artifacts/entrypoint-check
PYTHONPATH="$PWD/.artifacts/v0.18.3-review/package" .artifacts/v0.18.3-review/package/bin/voice-tools schema --tool qa > .artifacts/entrypoint-check/qa-schema.json
PYTHONPATH="$PWD/.artifacts/v0.18.3-review/package" .artifacts/v0.18.3-review/package/bin/voice-tools --json qa generate --out .artifacts/entrypoint-check/generated > .artifacts/entrypoint-check/generate.json
PYTHONPATH="$PWD/.artifacts/v0.18.3-review/package" .artifacts/v0.18.3-review/package/bin/voice-tools --json qa analyze .artifacts/entrypoint-check/generated --out .artifacts/entrypoint-check/analyzed --include-audio > .artifacts/entrypoint-check/analyze.json
PYTHONPATH="$PWD/.artifacts/v0.18.3-review/package" .artifacts/v0.18.3-review/package/bin/homerctl analyze --input examples/homer/sample-trace.json > .artifacts/entrypoint-check/homerctl.json
PYTHONPATH="$PWD/.artifacts/v0.18.3-review/package" .artifacts/v0.18.3-review/package/bin/voice-tools homer analyze --input examples/homer/sample-trace.json > .artifacts/entrypoint-check/homer.json
cmp .artifacts/entrypoint-check/homerctl.json .artifacts/entrypoint-check/homer.json
```

完整本轮入口回执和复现脚本保存在 `.artifacts/v0.18.3-cli/`，`verification.json` 记录 14 条实际命令、退出码及每份产物 SHA-256。HOMER 使用仓库离线样例；QA 使用固定种子的合成录音，不调用业务服务。QA manifest 应有 20 个场景，生成目录应有 20 个 WAV，分析结果为 20 个文件、0 个错误。
