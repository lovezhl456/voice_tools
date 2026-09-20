# R3/R4 可读性重构验证

日期：2026-09-20。开始修改前已获取最新 `origin/main`，从已合入 R1/R2 的 `374576cad6b72679e3d67973c60c67abdbce14bb` 创建 `refactor/v0.12.1-readability-r3-r4`。本轮只调整内部职责，版本沿用 `0.12.1`。

## 修改范围

- `sessions/store.py`：把 `build()` 中的输入收集和同源分片合并分别提取为 `_collect_inputs()`、`_merge_pcap_groups()`。`build()` 保留输出目录管理、逐来源 SAVEPOINT／ROLLBACK／commit 以及汇总输出。
- `nisqa/service.py`：提取 `_evaluate_channel_segment()`，负责有限值检查、RMS／时长门槛、调用 scorer 和片段级错误记录。`analyze()` 保留参数与依赖校验、流式读取、声道顺序、时间偏移、文件级错误和输出统计。
- 新增 8 项会话输入行为测试、5 项 NISQA 行为测试。未引入框架、运行依赖或新的公开接口。

同一规范化路径仍按原遍历顺序覆盖元数据，不改变其来源位置；合并成功的来源仍追加到末尾，失败仍逐文件处理。NISQA 保留 float32 声道副本、float64 RMS 运算、门槛比较精度，以及错误优先于证据不足的退出码规则。可选依赖仍在创建输出前检查，包括空录音输入。

## 回归测试

| 范围 | 当前结果 | 覆盖要点 |
| --- | --- | --- |
| `tests/sessions` | 27/27 通过 | 输入优先级、清单／摘要校验、缺口警告、失败回退、部分行回滚、Call-ID 分组、跨分片 IP/TCP 重组及下游导出 |
| `tests/report/test_v2.py` | 9/9 通过 | 索引／导出与媒体报告的相关回归 |
| NISQA service + CLI | 20/20 通过 | 双声道、静音／短尾／空文件、非有限值、模型返回错误、分段失败后继续、门槛边界、输入与依赖错误先于输出 |

合计 **56 项测试全部通过，没有跳过**。13 项新增测试也在原实现上验证通过，确保其描述已有行为。会话／报告测试使用已有 Python 3.9.6 和 Wireshark 工具；NISQA 使用已有 Python 3.12.14、NumPy 2.5.3、SoundFile 0.13.1 环境及注入的 FakeScorer。

另在未安装 SoundFile 的 Python 3.9.6 基础环境重跑 4 项 NISQA CLI 测试，均通过，确认发现命令和缺少依赖时的报告仍可使用；这 4 项不重复计入上述 56 项。

```bash
# 使用已有环境，并让 tshark / mergecap 位于 PATH。
PYTHONPATH=src python -m unittest discover -s tests/sessions -t . -v
PYTHONPATH=src python -m unittest tests.report.test_v2 -v
# 使用已具备 NISQA 音频读取依赖的环境。
PYTHONPATH=src python -m unittest tests.nisqa.test_service tests.nisqa.test_cli -v
```

## 原实现与修改后的对照

同一进程加载基线提交和当前两个模块，使用相同输入、参数和输出路径，分别运行后比较；临时输出由脚本独占并在两次运行之间清理。

- **会话索引 10 组一致**：混合 PCAP／HOMER／FS 输入、重复路径覆盖、成功合并与来源顺序、合并失败回退、来源中途损坏与事件缺口、清单覆盖与映射文件缺失、摘要不符、无效清单，以及 IP/TCP 跨文件重组。比较完整摘要、`index.json` 文本和 SQLite 的 sources／observations／metadata 有序记录。
- **NISQA 10 组一致**：双声道及短尾、RMS／时长精确边界、非整数分段帧数、静音／低能量／空文件／短段、短段非有限值、评分失败后恢复、文件损坏后恢复、声道选择错误、采样率错误和无效输入。比较结果摘要、退出码、JSONL／CSV／run.json 的全部字节，以及 scorer 的调用顺序、样本字节摘要、形状、dtype 和采样率。
- NISQA 对照固定了计时器读数，仅让运行耗时字段可重复比较；这不是性能测量。

另用 AST 核对：两个公开入口的参数和默认值不变；提取前后的输入收集语句一致；从 SQLite 初始化到事务处理及摘要返回的语句一致；片段评估主体在局部变量 `row` 改名为 `result` 后一致；两个模块的其他原有函数未变。整个模块因提取函数而发生结构变化，不声称完整 AST 等价。

最终差异复查和 `git diff --check` 通过。本机对照脚本、原始日志与结构检查结果保存在本次任务的 `voice-tools-readability-review-2026-09-20/implementation-r3-r4/` 目录；这些本机验证脚本不作为跨机器 CI 套件提交。

## 验证边界

未改变网页文件，因此没有新增浏览器验收。没有真实 SSH／HOMER／SIP 业务访问；会话相关测试使用合成离线 PCAP 和本地测试 HTTP 服务。NISQA 模型加载与推理后端未改，本轮没有下载权重或运行真实模型评分，也没有将 FakeScorer 对照当成模型质量或性能验证。
