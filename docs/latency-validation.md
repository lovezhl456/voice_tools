# latency 0.15.1 验收记录

日期：2026-09-20。状态：实现及本地分层验收记录，PR 未合并。真实中文录音准确率、指定线路和生产容量不在本次合成验收范围。

## 固定来源与环境

- 主线冻结：`92ad27f`，已合入 benchmark PR #25。独立工作树 `feat/v0.15.1-latency`，保留并行 0.14.1 工作；最终收尾再次 fetch 主线仍为该提交。
- 上游提交、归档、补丁摘要以 [包内 manifest](../src/voice_tools/tools/latency/resources/manifest.json) 为准，补丁版本 1。原始源码和普通样本对照使用相同固定提交。
- macOS ARM64：主 CLI Python 3.9.6；外部引擎 Python 3.12.14 / NumPy 2.2.6。
- Linux：Docker 的 ARM64 原生虚拟化，Python 3.11，非 root 10001:10001；专用镜像基于原任务镜像 0.13.1。未验证物理 Linux、Intel Mac、Linux amd64 或 amd64 模拟性能。
- Linux 全仓测试使用另建的 validation 镜像补齐既有测试需要的 Node、procps、tcpdump；这些不是 latency 业务运行依赖，不放进生产镜像。

## 已执行的检查与结果

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

## 未作承诺

不承诺真实中文录音准确率、业务应答成功率、指定 FreeSWITCH/运营商线路、生产并发容量、跨时钟自动对齐或原生离线首次安装。不承诺 file://；浏览器实际验收通过本地 HTTP Range 服务。算法受上游能量门限和配对假设限制，串音、弱音、噪声、过长停顿都应结合原始录音复核。
