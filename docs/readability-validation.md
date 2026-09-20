# R1/R2 可读性整理验证

日期：2026-09-20。基线为获取最新 `origin/main` 后创建分支时的 `9c1d91f3c501b9048a09b18670b69a57c7c76b48`，分支为 `style/v0.12.1-readability-r1-r2`。

本次只整理源码布局，沿用版本 `0.12.1`。未改公共接口、运行依赖、业务逻辑或执行顺序。用户尚未批准后续函数拆分、命名与结构重构，因此不纳入本轮。

## 改动范围和等价检查

| 范围 | 操作 | 结果 |
| --- | --- | --- |
| `docs/sip-studio/{app,core,batch,batch-core}.js` | 展开密集语句、缩进与换行 | AST 等价 |
| `src/voice_tools/tools/recording_qa/review.js` | 同上 | AST 等价 |
| `tests/studio/core.test.cjs` | 同上，便于阅读已有测试 | AST 等价，未增删测试 |
| `src/voice_tools/tools/capture/remote.py` | 展开 28 个分号分隔点 | AST 等价 |
| `src/voice_tools/tools/sessions/workflow.py` | 展开 9 个分号分隔点 | AST 等价 |
| `src/voice_tools/audio/rtp.py` | 展开 13 个分号分隔点 | AST 等价 |

JavaScript 使用临时 Prettier 3.6.2（100 列、单引号、无尾逗号、禁用嵌入语言格式化）整理；没有给仓库或全局安装依赖。用 Acorn 8.15.0 比较基线与最终文件，忽略源码位置及普通字面量的拼写差异，保留字符串值、模板的 raw/cooked 值并检查注释。Python 用标准库 `ast` 比较无位置属性的语法树，并检查字符串与注释 token 一致。

最终复查还核对了捕获进程的停止／等待／释放顺序、会话步骤的保存与失败返回顺序、RTP 的去重及补静音顺序。没有移动调用、改变条件或增删异常处理。`git diff --check` 通过。排版展开使行数明显增加；HTML 模板字符串保持原样。

## 已执行的回归检查

| 检查 | 结果 |
| --- | --- |
| Studio Node 单元测试 | 24/24 通过 |
| capture、sessions、report/RTP 相关 Python 测试 | 45/45 通过 |
| recording_qa Python 测试 | 37/37 通过 |
| Studio 与实际 CLI 兼容 | 9/9 场景通过：8 个有效导出各完成 validate + dry-run；缺失素材按预期 exit 2 |
| 浏览器下载的 `scenario.json` | 实际 CLI validate 通过 |
| 浏览器下载的 `queue.json` | 实际 CLI batch dry-run 通过，2 个任务状态均为 planned |

合计 **106 项测试，另有 9 个 CLI 兼容场景**，没有跳过。Python 使用项目已有 3.9.6 环境；原生包工具使用已有 Wireshark 安装。命令如下：

```bash
node --test tests/studio/core.test.cjs
PYTHONPATH=src python tests/studio/check_cli_compat.py
PYTHONPATH=src python -m unittest tests.capture.test_v2 tests.test_capture_v2_review tests.sessions.test_v2 tests.report.test_v2 -v
PYTHONPATH=src python -m unittest discover -s tests/recording_qa -t . -v
```

## 浏览器验收

使用已有 Playwright 和 Chrome 的独立无头会话，预览当前工作分支的 Studio，以及由当前代码生成的录音复核页。录音素材为 20 份合成录音，包含 19 个应答机会。

- **Studio 28 项检查通过**：连续编辑、实际 JSON 下载与字段检查、对话框关闭和焦点恢复、点击／键盘排序、真实 HTML 拖放、撤销／重做、错误导入保留旧草稿、有效导入／刷新恢复、离线预演边界、批量队列导出、SIPp 模式切换、WAV 格式拒绝／登记／实际播放，以及手机添加、排序和各页导航。
- **录音复核 16 项检查通过**：多条列表选择与滚动、键盘焦点、筛选、实际音频播放、声道／波形操作、人工标注、真实 CSV 下载与重新导入、未标注筛选空态、无应答机会空态、手机选择、横向溢出，以及运行错误／外部请求检查。
- 桌面 `1440×1000`、Studio 手机 `375×812`、复核手机 `390×844` 的截图已人工查看；页面未出现横向溢出，选中项、波形、表单和导航可见。手机为 Chrome 视口模拟。
- 两组页面均无运行异常、HTTP 资源错误或外部网络请求。首轮 MCP 浏览器连接中断后使用独立会话继续；未把中断调用计为通过。

复核页首轮播放检查在普通 Python 静态预览服务中无法完成音频 seek；更换为支持 HTTP Range 的临时服务后，复核流程全部重新执行通过。该调整只在测试目录，不属于产品代码。

本机脚本、原始日志、下载文件、AST 结果和截图保存在本次任务的 `voice-tools-readability-review-2026-09-20/implementation/` 目录，不作为跨机器 CI 套件提交。正式源代码和验证说明通过 PR 交付，未替换现有 8080 站点。

## 验证边界

未发起真实 SIP 呼叫、SSH 采集或生产网络任务；未验证物理手机、Safari/Firefox、真实线路、生产负载或主观音质。播放通过只代表浏览器媒体播放状态和时间推进正常。没有机械重跑全仓及无关原生 SIP 套件，既有功能限制仍以各模块文档为准。
