# 0.15.2 目录、录音联动与模糊检索验收

日期：2026-09-21。关联 [issue #30](https://github.com/lovezhl456/voice_tools/issues/30) 与 [PR #31](https://github.com/lovezhl456/voice_tools/pull/31)。主线基线：`c2bcdb1dbb289ca94b059b2575cdee3c2ef41470`；分支：`enhance/v0.15.2-directory-filter`。本轮在 `5b7c4f0` 目录选择实现上增加模糊检索，并重新验证最终代码；后续仅整理说明文档。初始目录功能的 50 项浏览器记录保留在该提交的本文版本中。

## 范围与实现

- QA 与输出间隙复核页共用 `file-filter.js`，按输入路径的直接父目录分组；支持无目录、POSIX 根目录、Windows 盘符和 UNC 路径。
- 目录同时限制录音选项和复核列表；相同文件名或检测身份不会绕过目录限制。“全部目录”保留原顺序与身份，切换目录保留仍有效的录音选择，其余恢复“全部录音”。
- “检索录音”对可见输入路径做大小写不敏感的子串匹配，统一正反斜线；空格分隔的多个关键词全部匹配。目录与检索共同限制选项和列表，清空检索恢复当前目录列表，输入法组合期间暂不刷新。
- 保留检测结果、证据、未标注条件和 CSV 合同；未记下的表单在切换目录时保留并提示，保存或放弃后恢复当前筛选下的详情。
- 共用 HTML 内嵌脚本，离线页面和任务结果复查沿用同一资源路径；长路径选择器限制在容器宽度内。
- 隐藏路径的页面继续仅接收文件名，不还原原目录。已有静态 HTML 需重新生成才具有新选择器。

## 自动检查

环境：macOS、Python 3.12.14、NumPy 2.3.5、Node 24.18.0。复用现有依赖环境，以 `PYTHONPATH="$PWD/src"` 指向本工作树，并核对 `voice_tools.__file__`；未安装模型或启用真实调用。

| 检查 | 本轮结果 |
|---|---|
| QA 工作流／P0、筛选测试、gaps 和 task 交付 | 52 项通过 |
| Python 最终默认 discovery | 503 项，466 通过、36 条件跳过、1 项进程清理权限错误；57.6 秒 |
| latency 契约组补跑 | 10 项全部通过，含发生权限错误的超时清理用例；复用其余全量结果，最终覆盖 467 项通过 |
| 筛选 Node 行为测试（由 Python discovery 调用） | 14 项通过：原有 7 项目录行为，以及片段／中文／大小写、多关键词／路径分隔符、目录交集、空结果／选择恢复、同身份隔离、字面特殊字符、中文组合事件 |
| Studio Node | 24 项通过 |
| Studio／CLI 兼容 | 9 场景通过，16 个成功命令及 1 个预期拒绝，无 SIP 呼叫 |
| JavaScript 语法与差异空白 | 通过 |
| CLI schema | 重新生成，除 `tool_version=0.15.2` 外与基线完全一致 |
| wheel | 无联网构建；共享模板、筛选脚本、两个业务脚本与源码逐字节一致；从解包后的 wheel 加载并生成页面通过 |

36 条件跳过为：可选 VAD 1 项、媒体观测回环 8 项、latency 真实引擎 7 项、NISQA 真实模型 1 项、PJSUA2 回环 19 项。未新增跳过；本次不改变这些后端。

最终全量中的 `test_timeout_kills_engine_and_descendant` 在 `os.killpg(..., SIGKILL)` 处出现一次 `PermissionError`。保留原断言单独补跑整个 `tests.latency.test_contract`，10 项通过；本次未修改该模块。全量与补跑日志分别为 `full-python-final.log`、`latency-recheck.log`。按照仓库的补跑规则复用其余已通过结果，没有以跳过掩盖失败。

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

- 本轮 QA 检索 15 项、gaps 检索 16 项、标注与 CSV 往返各 6 项、全程错误检查 1 项，共 44 项通过。
- 检查文件名片段、大小写、中文与多关键词、Windows 路径、目录交集、空白查询、无效录音选择清除、状态和证据组合、无匹配时的详情隐藏，以及从无候选录音切换后清除旧提示。
- 两个入口均实测实时检索时的草稿保留、提示、保存／放弃后的详情同步、只看未标注和 Escape 清空。
- 检索隐藏已标注录音时仍能导出完整标注，下载后刷新重新导入，保留中文与以 `@` 开头的标注；CSV 分别通过 `qa promote --dataset-kind synthetic` 和 `gaps review-check` 校验。
- 已查看桌面／手机检索截图，无水平溢出；无控制台错误、JavaScript 运行错误或 HTTP 错误。中文输入法的组合事件保护由自动测试覆盖，未声称物理输入法验收。

原始日志、浏览器脚本、CSV、截图和最终 wheel 保留在本地任务证据目录 `voice-tools-issue30-evidence/search/`，合成素材在其父目录；不提交录音或机器路径。合成素材用于工程行为验收，不代表真实录音准确率。

## 可读性核查

按 `code-readability` Skill 检查本 PR 相对最新 `origin/main` 的差异，以及共享页面和两个业务脚本的调用点。目录与关键词匹配共用一个规则，避免选项与队列不一致；检索复用各入口的筛选刷新及草稿协调函数，保留自身身份与业务条件。核对了组合事件、快捷键输入保护、清空与无匹配路径，未扩大到无关代码重构，未发现剩余的范围内问题。
