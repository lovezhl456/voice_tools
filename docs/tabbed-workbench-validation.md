# 0.19.1 多 Tab HTML 工作台验证

状态：实现、最终安装包浏览器验收与默认回归完成，待 PR 审阅和合并。基线 `e773ef7`；分支 `feat/v0.19.1-tabbed-workbench`。产品包为 0.19.1；最终 wheel SHA-256：`47acaafdaff9b93bc2b2d58054b70911c0b5c7f34311b201930ead29cdec6930`。

## 结果与复现

环境为 macOS ARM64、Python 3.9.6、项目锁定 Playwright 1.63.0 / Chromium；桌面 1440×1080、手机视口 390×844。移动验收为视口模拟。产品所有运行文件逐字节匹配已安装 wheel。

| 验证 | 结果 | 本地产物（Git 忽略） |
| --- | --- | --- |
| 公共 CLI、任务生成调用方选测 | 25 通过 | `.artifacts/tabbed-workbench/selected.log` |
| 安装包原复核 R01–R08 | 桌面/手机 16 通过，无失败、跳过或重试 | `.artifacts/pr41-review-fixes/review/` |
| 安装包检测/编辑/工作区/工作台 | D01–D05、E01–E06、W01–W05、H01–H04，桌面/手机 40 通过，无失败、跳过或重试 | `.artifacts/pr41-review-fixes/detection/` |
| 默认 Python discovery | 634 项，597 通过、37 原有条件跳过 | `.artifacts/pr41-review-fixes/python-full.log` |
| Studio Node | 23 通过 | `.artifacts/pr41-review-fixes/node-full.log` |
| Studio/CLI 兼容 | 9 场景，16 次成功 CLI 调用和 1 次预期拒绝，无呼叫 | `.artifacts/pr41-review-fixes/cli-compat.log` |

Python 跳过为：19 个 PJSUA2 回环、8 个可选本机媒体观测、7 个真实 latency 引擎、2 个真实整通模型、1 个真实 NISQA 模型。没有为本次功能新增跳过。构建依赖按 `pyproject.toml` 要求补齐 setuptools、wheel，普通音频服务测试补齐 soundfile；未下载模型。

复现时从仓库根目录、已准备开发依赖的 Python 环境执行，使用新的输出目录：

```bash
python scripts/check_review.py --out .artifacts/review-check
python scripts/check_detection.py --package-root .artifacts/review-check/package --out .artifacts/detect-check

env -u VOICE_TOOLS_SIP_LOOPBACK -u VOICE_TOOLS_NISQA_MODEL_DIR -u VOICE_TOOLS_NISQA_AUDIO \
  -u VOICE_TOOLS_TEST_LATENCY_DIR -u VOICE_TOOLS_TEST_QA_MODEL_DIR -u VOICE_TOOLS_TEST_QA_AUDIO \
  PYTHONPATH=src python -m unittest discover -s tests -v
node --test tests/studio/core.test.cjs
env -u VOICE_TOOLS_SIP_LOOPBACK -u VOICE_TOOLS_NISQA_MODEL_DIR -u VOICE_TOOLS_NISQA_AUDIO \
  -u VOICE_TOOLS_TEST_LATENCY_DIR -u VOICE_TOOLS_TEST_QA_MODEL_DIR -u VOICE_TOOLS_TEST_QA_AUDIO \
  PYTHONPATH=src python tests/studio/check_cli_compat.py
```

安装包实际用户入口为 `.artifacts/pr41-review-fixes/detection/site/moved-workbench/index.html`，合成音频来自仓库夹具。`site/workbench-evidence.json` 记录实际 CLI 命令、JSON 封套与每个复制文件的 SHA-256；`test-results/` 保留截图、导出复核、任务 JSON、CLI 导入/报告/重开证据；`verification.json` 和 `browser-results.json` 记录最终状态与逐项结果。

## 新工作台的能力与拒绝条件

- H01：真实 CLI 导出后整体移动目录；五个标签及资源可打开，桌面/手机没有横向溢出；输入保留、试听、离开自动暂停、键盘、刷新与浏览器后退通过。无页面 JavaScript 异常或失败 HTTP 资源。
- H02：在工作台内修改检测复核→导出 JSON→已安装 CLI 入库→重新导出报告与工作台→重开，标签与复核修订匹配。
- H03：`file://` 打开空工作台，缺数据状态明确；任务编排修改→导出→刷新→导入后内容恢复。
- H04：缺少入口、递归输出、符号链接、非空输出均由真实 CLI 退出 2 拒绝；既有文件保持原字节。模拟 HTTP 503 时显示失败，解除故障后重试恢复。
- 原有单页、报告生成与任务流程由原有安装包浏览器、Python、Node 和 CLI 兼容验证覆盖；新增工作台未进入任务执行 catalog。

首次浏览器轮次因新测试误用 `#audio` 而失败；核实共享播放器实际 ID 为 `#player` 后修正测试。最终 38 项完整重跑通过，产品 wheel 未变化；没有通过跳过或放松产品断言掩盖失败。首次构建环境 setuptools 58 不支持现有项目元数据，补齐声明的构建依赖后重新构建，未修改产品构建契约。

## 可读性核查与边界

按 `code-readability` 检查相对最新 `origin/main` 的最终差异与 CLI 注册、任务 catalog、HTML 资源打包、生成器和 browser runner 调用点。输入验证、构建与发布、标签选择与加载职责清晰；重试先移除旧 frame 并忽略旧加载回调，避免过期事件覆盖当前加载状态；输出先在同目录临时目录生成，完成后原子放置。未扩展重构无关模块。

本轮验证的是 HTML 生成、安装包资源和浏览器操作，使用合成音频及整通规则模式，未执行真实模型、外部设备、真实 SIP 线路或生产业务准确率验收。file 协议下完整音频/按需数据功能受浏览器限制，完整交互使用本机 HTTP；原页面的目录外链接不自动打包。范围与保留能力见[计划](tabbed-workbench-plan.md)和[用法](tabbed-workbench.md)。


## PR #41 审查修复最终验收

两项 P2 均已修复，版本仍为未合并的 0.19.1：

1. `core/schema.py` 对 `nargs='?'` / `'*'` 的位置参数按可省略语义导出 `required=false`；显式选项和必填位置参数保留 argparse 的声明。Python 3.9 与 3.12 实际执行完整 CLI schema，结果与快照完全一致。快照相对审查前仅改变 `detect serve inputs.required` 的 true→false。证据为 `.artifacts/pr41-review-fixes/schema-comparison.json` 和两份完整导出。
2. 初始化恢复标签时，对无 hash 的历史项使用 `replaceState` 写入真实标签，不增加新历史项。H01 先保存“使用说明”、无 hash 重开，再切任务、后退和前进，桌面/手机均恢复正确标签。

新增 W05 从已安装 wheel 获取公开 schema，与快照核对，再通过实际 CLI 首次启动、浏览器保存日常规则、停止服务、省略 inputs 重启，确认原录音目录、工作区名称与日常规则均恢复。schema.json、restore.json 和重开截图保存在对应 test-results/W05 目录。

回归有效性：产品修复前，用旧 wheel 跑 H01/W05 桌面版，两项均在对应缺陷断言处失败（`.artifacts/pr41-review-fixes/red/`）；修复后重建 wheel，R01–R08 共 16 项及 D/E/H/W 共 40 项全部通过，无失败/跳过/重试。最终 Python 634 项（597 通过、37 原有条件跳过）、Node 23 项、CLI 9 场景通过；无新增单元测试和跳过条件。当前文档顶部摘要和表格均为此最终 wheel 的结果。

按 `code-readability` 复查 schema 必填语义、标签初始化/历史事件顺序、工作区 E2E 子进程退出与重启，以及公开 snapshot/安装包调用点。没有改变原检测 CLI 的参数接受行为，没有重构无关模块。
