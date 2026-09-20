# latency 0.15.1 验收记录

日期：初版 2026-09-20，PR 评审修复 2026-09-21。状态：实现及本地分层验收记录，PR 未合并。真实中文录音准确率、指定线路和生产容量不在本次合成验收范围。

## 固定来源与环境

- 主线冻结：`92ad27f`，已合入 benchmark PR #25。独立工作树 `feat/v0.15.1-latency`，保留并行 0.14.1 工作；最终收尾再次 fetch 主线仍为该提交。
- 上游提交、归档、补丁摘要以 [包内 manifest](../src/voice_tools/tools/latency/resources/manifest.json) 为准，当前补丁版本 2。原始源码和普通样本对照使用相同固定提交。
- macOS ARM64：主 CLI Python 3.9.6；外部引擎 Python 3.12.14 / NumPy 2.2.6。
- Linux：Docker 的 ARM64 原生虚拟化，Python 3.11，非 root 10001:10001；专用镜像基于原任务镜像 0.13.1。未验证物理 Linux、Intel Mac、Linux amd64 或 amd64 模拟性能。
- Linux 全仓测试使用另建的 validation 镜像补齐既有测试需要的 Node、procps、tcpdump；这些不是 latency 业务运行依赖，不放进生产镜像。

## 初版补丁 1 的检查与结果

| 检查 | 预期及实测 |
| --- | --- |
| 冻结基线 | 固定提交导出副本：437 项，45 条件跳过，其余通过；不依赖可能被其他任务修改的工作树 |
| 最终回归 | Mac 449 项，45 条件跳过；Linux 449 项，28 条件跳过，其余通过。收尾补充崩溃/损坏环境后，14 项 latency 专项复核通过 |
| 旧 CLI/协议 | 所有原有工具的命令参数和数据版本合同逐项一致；新增 latency 合同、工具版本号为允许变化 |
| QA 固定样本 | 20 份样本的完整逐文件结构、黄金标签相关身份及机会一致，仅 tool_version 从 0.13.1 改为 0.15.1 |
| 业务算法 | QA/SIP/benchmark/NISQA/ViSQOL 共 39 个 Python 文件与冻结基线字节一致 |
| 六种采样率 | 8/16/24/32/44.1/48 kHz：0.5s 间隔及 61.5s 末段起点误差为 0，满足合成样本 ≤20ms；22.05kHz 明确拒绝 |
| 长样本 | 3600s / 8kHz，3591.5s 起点和 0.5s 延迟无累计漂移 |
| 正常上游对照 | 0.5、2、9.8s：除新增片段审计字段外，与原始上游结果完全一致 |
| 声明补丁 | 原始上游丢弃的 10、12s Human→AI 在适配版保留；37s AI→Human 保留；200ms AI 片段不崩溃 |
| 统计边界 | 无应答、重叠、全静音、零配对、重复声道均不足证据；空统计 null，零分母覆盖率 null；被后续片段替代仍计入分母 |
| 批量与身份 | 相同路径去重，不同路径同 PCM 保留并提示；部分文件失败仍保留成功数据；重名/路径迁移不改变摘要身份 |
| 迁移 | 打包后删除测试原素材目录，task check/run/collect/review 通过；HTML 中音频定位为包内 evidence/work/inputs 路径 |
| 恢复 | 缺依赖、缺文件、超时、SIGINT、SIGTERM 保留预期状态；进程组包括派生子进程被清理；ps 检查无残留；Docker stop 保留 interrupted 记录 |
| 外层超时 | 1s 步骤超时后，文件和步骤 interrupted，任务整体 failed，沿用旧任务语义，没有改状态规则 |
| 分发 | wheel/sdist 包含安装器、worker、补丁、manifest、MIT 和静态资源；干净 Python 3.9 wheel 环境可发现、检查和真实分析；从已安装 wheel 显式安装独立引擎成功 |
| Docker | 专用镜像非 root、禁网、只读系统分析 8 份合成样本成功；原镜像加载新主包而未装引擎时 doctor 明确返回 2；同架构 save/load 成功 |
| 浏览器 | 桌面及 390px 移动端，分页、CSV 下载、双轨和跳转试听通过；119 轮样本第二页跳转请求 401.7s，实际观测 401.820s 且正在播放；任务包内音频返回 206；旧 benchmark 结果包仍可显示双向时间轴和按指标定位音频，控制台无错误 |

合成测试信号为幅度充足、边界已知的双声道正弦片段；不等价于真实语音。20ms 仅是该验收条件下的时间轴标准。容器 save/load 在同一宿主的 Docker 环境演练，不声称已经验收另一台物理主机。

## 资源实测

| 环境与样本 | 引擎计算墙钟 | 引擎峰值 RSS |
| --- | ---: | ---: |
| macOS ARM64，16kHz / 484s / 60 个人声轮次 | 0.441s | 102.81 MiB |
| Linux ARM64 VM，相同样本，禁网非 root | 0.445s | 93.32 MiB |
| macOS ARM64，8kHz / 3600s，首尾合成片段 | 2.934s | 310.69 MiB |

计时从 worker 开始处理输入到结果准备完成，不含 CLI doctor、进程启动、最终 JSON 写入与复制；逐文件索引另存整个文件墙钟。RSS 是隔离引擎进程的实际峰值，不是机器容量结论。更密集片段、其他参数和真实录音可能不同。

## 复现

```sh
python scripts/latency_demo.py --out demo
voice-tools latency doctor --latency-dir /absolute/path/to/engine
voice-tools --json latency analyze demo/audio/normal.wav --system-channel right --latency-dir /absolute/path/to/engine --out results/one --include-audio
voice-tools --json latency batch demo/audio --system-channel right --latency-dir /absolute/path/to/engine --out results/batch --include-audio
VOICE_TOOLS_TEST_LATENCY_DIR=/absolute/path/to/engine PYTHONPATH=src python3 -m unittest discover -s tests/latency -v
VOICE_TOOLS_TEST_LATENCY_DIR=/absolute/path/to/engine PYTHONPATH=src python3 -m unittest discover -s tests -v
python scripts/serve_latency.py --root results --port 8088
```

安装、独立前缀切换/回退、镜像离线导入按 [安装说明](latency-install.md)；迁移按 [集成协议](latency-integration.md)。跳过的既有测试主要依赖指定原生/模型/真实环境；不将条件跳过当作通过，不把静态检查代替音频播放。首次全量检查中的 schema 快照遗漏已修正，对应 benchmark 合同测试复跑通过。首次 Linux 全量失败是测试工具缺失，补齐后全量通过。说明书与合成演示已接入 [本地 8080 总导航](http://127.0.0.1:8080/latency-guide/)，桌面/移动端正式地址再次验证。

## PR #27 评审修复：补丁 2

主线重新 fetch 后仍为 `92ad27f`。本轮只改 latency 片段审计、安装资源和文档生成；没有修改共享任务模块或旧业务算法。沿用尚未发布的 0.15.1，旧环境和镜像保留；补丁 2 使用新前缀与单独镜像标签。

| 检查 | 本轮实测 |
| --- | --- |
| macOS ARM64 | 新建 Python 3.12.13 / NumPy 2.2.6 前缀，官方 wheel 哈希校验安装成功；16 项 latency 测试全部通过，无跳过 |
| Linux ARM64 | 从原基础镜像重新构建 patch2 专用镜像；Python 3.11，非 root、禁网、只读源码，16 项 latency 测试全部通过，无跳过 |
| 续说归属 | 人声 1–2s、4.8–6.3s，AI 4.5–4.9s：前段 outgoing overlap，续说 incoming overlap / outgoing unpaired；覆盖率 0/2，状态不足证据。再加 AI 7–8s，续说 outgoing paired，延迟 0.7s，覆盖率 1/2 |
| 补丁 1/2 对照 | 正常、长延迟、普通重叠、续说、续说后回复、短片段和静音共 7 组，除 segment_audit 外检测结果逐字段相等；本样本仅无后续回复的续说审计发生预期修正 |
| 哈希失败路径 | 离线提供同名同版本、不同字节的 wheel，完整安装器退出 2；保留 installing.json，不写 ready.json，释放安装锁。自动测试另验 pip 不接受该 wheel |
| Node 18 | 在 Linux Node 18.20.4 实际生成 5 个 HTML 页面、8 个固定提交链接；无 Git 的源目录通过显式 SHA 生成，分支名作为 revision 会被拒绝 |

说明书生成使用 Node 18+ 和可导入的 marked ESM 模块；在仓库内默认固定到当前 HEAD。源码包没有 Git 元数据时必须显式传入对应的完整 40 位提交 SHA：

```sh
# MARKED_MODULE 指向本机已安装的 marked 模块；有本地 node_modules 时可省略。
MARKED_MODULE=/absolute/path/to/marked/lib/marked.esm.js node scripts/build_latency_guide.mjs
# 从源码包生成：LATENCY_SOURCE_REVISION=<对应源码的完整提交SHA> MARKED_MODULE=... node scripts/build_latency_guide.mjs
```

生成前应先提交源码修复，保证页面链接指向已提交内容。构建后仅复制 `docs/latency-site/*.html` 到本地说明书目录；业务报告与音频仍保留在结果目录。该轮评审修复没有重新执行初版全仓测试、3600s 性能或镜像跨机导入，不把历史记录当作补丁 2 的重新验收。

## 同步最新 main：输出间隙 PR #26

2026-09-21 合入 `origin/main d4d073d`。9 个冲突文件涉及版本台账、架构/任务说明、schema、版本号、工具注册、任务复查和许可路径。保留 0.15.1，同时保留 main 的 gaps 注册、公共复核资源迁移及 latency 独立入口；schema 从合并后的 CLI 重新生成。QA/gaps/benchmark/SIP/NISQA/ViSQOL 业务模块及公共复核层与该主线没有差异。

- macOS 全仓 499 项：46 条件跳过，其余通过；Linux ARM64 全仓 499 项：28 条件跳过，其余通过。两侧均实际运行全部 17 项 latency 测试，包含新的混合任务回归。
- 新回归在同一个任务中依次运行 gaps → benchmark → latency，前两步 findings 不阻断后续步骤。打包后删除原素材，收集后再删除执行目录，三个工具的报告、音频和独立展示入口仍可复查；latency 正常样本保持 0.5s。
- 浏览器实测同一结果包的 gaps iframe 标注页、benchmark 时间轴和 latency 报告均能加载；三个入口实际试听分别观测到 1.206s、0.313s、1.846s 且正在播放。390px 移动端总览、步骤切换、latency 跳转正常，控制台无错误。
- 本轮只增加兼容测试并解决合并冲突；引擎补丁及摘要仍为 2，不需因为主线同步再创建新的引擎前缀。未重新测量 3600s 性能或生产容量。

## PR #27 复查页脚本注入修复

2026-09-21，在合入 `d4d073d` 后的 `11c8a36` 复现。向 `run.json` 的 `coverage.paired` 写入只设置 DOM 标记的 img/onerror 测试字符串，并更新清单中的大小及 SHA-256；54 个文件摘要校验全部通过，旧页面仍执行事件处理器。安全 JSON 嵌入不能代替后续 HTML 渲染处的类型校验和转义。

- 修复共享组件的配对数、分母、两个方向的轮数和去向计数；要求非负安全整数。覆盖率严格限制为 0–1 的有限数值或 null；非法值明确报错，合法 0/null 保持原有不足证据语义。展示文本转义，引擎和结果协议不变。
- macOS latency 与任务交付 37 项测试全部通过，无跳过。新增 Python 入口中包含 16 组 Node 渲染测试，检查所有 HTML 写入，包括错误前的中间写入；覆盖事件处理器载荷、字符串/布尔值/对象/数组、缺失字段、负数、小数、超大数和非有限数。该测试不模拟真实 DOM；在旧 JS 上 12 组失败，在修复后全部通过。
- Linux ARM64 现有 validation 镜像（Node 18）中，非 root、禁网、只读挂载源码，新增 2 项 Python 测试及其中 16 组 Node 测试全部通过。另一项 Python 测试验证摘要正常的结果包与独立报告都使用当前共享组件。
- 真实浏览器打开同一结果包重新生成的复查页：显示字段错误，无注入节点、无事件执行标记。四个原始漏洞字段分别在运行摘要/录音详情中注入，共 8 个真实 DOM 场景，全部拒绝。
- 正常结果的 0.500s 延迟、1/1 覆盖和反向零轮次保持一致；桌面与实际 375px 视口的任务切换、统计和跳转试听可用，窄视口页面无整体横向溢出。试听状态显示从 1.700s 开始，移动端播放器显示播放中；本轮未重新测量精确 seek 误差或 Range/206。

无引擎的自动回归命令（需 Node 18+，缺失时渲染测试会明确跳过）：

```sh
PYTHONPATH=src python3 -m unittest tests.latency.test_web -v
node --test-reporter=tap tests/latency/web_renderer.cjs src/voice_tools/tools/latency/web/latency.js
```

已生成报告需按[集成说明](latency-integration.md)更新，旧静态 JS 不会随工具升级自动修复。本轮未重跑全仓、打包构建或重建生产镜像；上述主线同步和引擎验收属于此前记录。

## 未作承诺

不承诺真实中文录音准确率、业务应答成功率、指定 FreeSWITCH/运营商线路、生产并发容量、跨时钟自动对齐或原生离线首次安装。不承诺 file://；浏览器实际验收通过本地 HTTP Range 服务。算法受上游能量门限和配对假设限制，串音、弱音、噪声、过长停顿都应结合原始录音复核。
