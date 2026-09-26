# E2E 优先的测试精简

基线 `653288f`（PR #38 后主线），分支 `chore/v0.18.1-e2e-test-pruning`。仅调整测试及协作约定，产品包仍为 `0.18.1`，业务源码不变。

## 去留结果

逐项审核 651 个 Python 方法和 54 个 Node 用例，删除 53 项：50 个 Python 方法及 3 个 Node 用例。保留的 Python 方法为 601 个。原有 46 个安装包浏览器场景全部保留。

| 并行审查范围 | 原始项数 | 删除 | 保留 | 逐项证据 |
|---|---:|---:|---:|---|
| QA、检测、间隙 | 236 | 14 | 222 | [清单](test-pruning-qa.json) |
| SIP、抓包、会话、报告、时序、HOMER | 314 | 24 | 290 | [清单](test-pruning-sip.json) |
| 音频、模型、任务、latency、Studio、公共入口 | 155 | 15 | 140 | [清单](test-pruning-other.json) |
| 合计 | 705 | 53 | 652 | Python/Node 包装器分别记录，不能相加作为独立业务场景数 |

清单记录每个原始测试的 ID、去留、实际测试层级、具体故障或重复原因及覆盖引用。`coverage_reference` 对删除项表示替代覆盖或相关流程，对保留项表示已检查但仍有缺口的流程；它不表示那些 E2E 已覆盖保留项。Python 清单与原始／最终 AST 对账，所有原始方法恰好有一条决策，没有新增方法；Node 按实际测试标题对账。

删除范围包括：重复安装包浏览器正常路径、重复真实 SIP 回环的模拟行为、页面占位标记与内部调用次数、同一函数的自比较、重复 CLI 分发检查。对仍有独有失败断言的混合用例，只移除重复静态断言。

保留项包含 290 个有明确覆盖缺口的隔离测试、323 个集成测试及 39 个 E2E；这不是将全部旧单元测试重新命名为 E2E。保留原因包括损坏／篡改证据、事务回滚、边界时序、资源上限、进程退出清理和模型／下载失败。现有 E2E 的输入无法触发这些情况，不能以名称或代码行数作为删除理由。

## 独立复核与规则

交叉审查恢复了五项存在独有覆盖的旧测试：tcpdump 命令资源限制、author 工作台生成资源、初始化模板有效性、确定性音频摘要、安装器与发布包依赖一致性。分别核对了实际调用路径，未将 dumpcap、结果复核页面、另一份场景校验、单次生成或假 pip 成功当作替代证据。SIP 失败路径仍复用原夹具，已解除夹具对被删测试方法名的依赖。

[AGENTS.md](../AGENTS.md) 已加入用户要求的三条规则：代码完成后绝不补单元测试；强烈优先用 E2E 作为唯一机制并生成可验证、可重复产物；必须隔离测试时，先写下所有可能的失败方式，再写代码。测试指南和 PR 模板同步要求注明隔离例外及产物。

最终差异按 `code-readability` 核查：保留测试名称、断言意图、公共行为和夹具清理顺序，清理删除后的空行及无用导入。未修改业务接口、浏览器基线或可选专项开关，未新增跳过。

## 验证与复现

使用 Python 3.12、NumPy 2.3.5、soundfile 0.13.1 运行普通回归；真实 SIP 回环复用已有 Python 3.9／PJSUA2 环境，所有呼叫限制在 `127.0.0.1`。浏览器使用锁定的 Playwright 1.63.0／Chromium 1243。命令从工作树执行，`python` 指向相应依赖环境。

```bash
python scripts/check_review.py --out .artifacts/e2e-review
python scripts/check_detection.py --package-root .artifacts/e2e-review/package --out .artifacts/e2e-detection
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
| Python 默认全量 | 601 项：563 通过、38 个原有条件跳过；59.240 秒 |
| Studio Node | 23 通过 |
| Studio／CLI 兼容 | 9 场景，16 次成功命令、1 次预期拒绝 |
| 真实本机 SIP／时序 | 27 通过，74.194 秒 |

浏览器产物包括 wheel、合成录音、导出 CSV／JSON、截图、逐项结果、`verification.json`，位于上述 `.artifacts/e2e-*` 目录。安装包 SHA-256 为 `04dbca606fdfd71fcafdb4286483ed95b6e99f890fb9552bad8ac9182b5b6fe2`；检测运行器逐文件核对安装包与当前产品源码一致。附加 `.artifacts/test-pruning-verification.json` 汇总最终代码文件摘要、命令结果与产物摘要。本地产物不上传个人环境路径或业务录音。

这些结果验证合成输入、离线工具和本机回环，不代表真实运营商线路或业务录音准确率。真实 QA／NISQA 模型、latency 真实引擎和 ViSQOL 实评分未在本轮执行；相关原有专项仍保留。没有做相同负载的前后性能实验，不宣称提速比例。
