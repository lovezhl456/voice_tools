# 0.15.2 目录与录音联动验收

日期：2026-09-21。关联 [issue #30](https://github.com/lovezhl456/voice_tools/issues/30)。基线：`c2bcdb1dbb289ca94b059b2575cdee3c2ef41470`；分支：`enhance/v0.15.2-directory-filter`。以下验证针对该基线加本 PR 的代码与测试差异；后续仅整理说明文档。

## 范围与实现

- QA 与输出间隙复核页共用 `file-filter.js`，按输入路径的直接父目录分组；支持无目录、POSIX 根目录、Windows 盘符和 UNC 路径。
- 目录同时限制录音选项和复核列表；相同文件名或检测身份不会绕过目录限制。“全部目录”保留原顺序与身份，切换目录保留仍有效的录音选择，其余恢复“全部录音”。
- 保留检测结果、证据、未标注条件和 CSV 合同；未记下的表单在切换目录时保留并提示，保存或放弃后恢复当前筛选下的详情。
- 共用 HTML 内嵌脚本，离线页面和任务结果复查沿用同一资源路径；长路径选择器限制在容器宽度内。
- 隐藏路径的页面继续仅接收文件名，不还原原目录。已有静态 HTML 需重新生成才具有新选择器。

## 自动检查

环境：macOS、Python 3.12.14、NumPy 2.3.5、Node 24.18.0。复用现有依赖环境，以 `PYTHONPATH="$PWD/src"` 指向本工作树，并核对 `voice_tools.__file__`；未安装模型或启用真实调用。

| 检查 | 本轮结果 |
|---|---|
| QA 检测／工作流／P0、新筛选测试、gaps 和 task 交付 | 62 项通过 |
| Python 默认 discovery | 503 项，467 通过、36 条件跳过；59.4 秒 |
| 新筛选 Node 行为测试（由 Python discovery 调用） | 7 项通过：默认行为、目录交集、选择保留／清除、同名及相同身份、路径形式、错误／空输入、路径文本 |
| Studio Node | 24 项通过 |
| Studio／CLI 兼容 | 9 场景通过，16 个成功命令及 1 个预期拒绝，无 SIP 呼叫 |
| JavaScript 语法与差异空白 | 通过 |
| CLI schema | 重新生成，除 `tool_version=0.15.2` 外与基线完全一致 |
| wheel | 无联网构建；共享模板、筛选脚本、两个业务脚本与源码逐字节一致；从解包后的 wheel 加载并生成页面通过 |

36 条件跳过为：可选 VAD 1 项、媒体观测回环 8 项、latency 真实引擎 7 项、NISQA 真实模型 1 项、PJSUA2 回环 19 项。未新增跳过；本次不改变这些后端。

默认回归命令（`python` 指向上述依赖环境）：

```bash
env -u VOICE_TOOLS_SIP_LOOPBACK -u VOICE_TOOLS_NISQA_MODEL_DIR -u VOICE_TOOLS_NISQA_AUDIO -u VOICE_TOOLS_TEST_LATENCY_DIR \
  PYTHONPATH="$PWD/src" python -m unittest discover -s tests -v
node --test tests/studio/core.test.cjs
env -u VOICE_TOOLS_SIP_LOOPBACK -u VOICE_TOOLS_NISQA_MODEL_DIR -u VOICE_TOOLS_NISQA_AUDIO -u VOICE_TOOLS_TEST_LATENCY_DIR \
  PYTHONPATH="$PWD/src" python tests/studio/check_cli_compat.py
```

## 浏览器验收

使用 Playwright 独立 Chromium 会话和本机 HTTP 服务，实际生成合成 QA／gaps 报告；桌面 1440×1000，手机视口 390×844。不是物理手机实测。

- QA 17 项、gaps 16 项、标注与 CSV 往返 17 项，共 50 项通过。
- 检查目录清单、全部目录、目录内全部录音、跨目录同名及相同身份、无目录文件、Windows 路径、无效录音选择清除、状态和证据组合、空结果及详情同步。
- 两个入口均实测未保存草稿切换、保存与放弃、只看未标注、音频播放、声道切换及试听起点设置。
- 实际下载 CSV、刷新后重新导入，保留中文与以 `@` 开头的标注；下载结果分别通过 `qa promote --dataset-kind synthetic` 和 `gaps review-check` 校验。手机视口下复验未标注空态和目录交互。
- 已查看长中文路径下的桌面／手机截图，无水平溢出；交互检查未出现 JavaScript 运行错误。

原始日志、合成素材、浏览器脚本、CSV、截图和 wheel 保留在本地任务证据目录 `voice-tools-issue30-evidence/`，不提交录音或机器路径。合成素材用于工程行为验收，不代表真实录音准确率。

## 可读性核查

按 `code-readability` Skill 检查本 PR 相对最新 `origin/main` 的差异，以及 `render_page`、QA workbench 和 gaps／task 复查调用点。把相同的目录提取、录音联动和匹配规则集中在一个小模块；两个业务脚本保留自身身份和筛选条件。补齐 gaps 详情协调逻辑，并展开本次涉及的保存／放弃步骤。未扩大到无关代码重构，未发现剩余的范围内问题。
