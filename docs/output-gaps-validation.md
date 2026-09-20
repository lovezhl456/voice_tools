# 输出间隙 0.14.1 验证记录

日期：2026-09-20。当前集成基线：`92ad27f`（benchmark PR #25），合并提交 `726b2a4`；独立分支 `feat/v0.14.1-output-gaps`。初版基线为 `7e9e30d2356b0c39b2632aac8a0ecfd749193c51`，下列初版验收保留当时的结果。原 benchmark 工作区未修改。参考 hiccup 固定提交 `260c92e867203f192dd8d5c37879b4e624ec3f11` 的问题分类；实现、测试、示例和 UI 接入独立编写。

## 合并主线及 PR #26 审查修复后的验收

- macOS arm64 / Python 3.9.6：482 项全仓测试，0 失败，46 项可选环境测试跳过。
- Linux aarch64 / Python 3.11.16：原验收镜像、禁网且源码只读；482 项全仓测试，0 失败，28 项可选环境测试跳过。没有重新编译原生依赖，也没有拨打外部线路。
- 新增 8 项回归：旧事件边界与未对齐扩展、RTP 流覆盖首尾不足与混合区间、分组抓包替换/消失/摘要一致及默认不开启的开销、gaps → benchmark → 后续步骤的任务链与两套复查数据。
- 合并解析器重新生成 schema，保留 gaps、benchmark 及 SIP 1.0/1.1 合同；在源码目录外安装新 wheel，核对公共资源、benchmark.js、许可证、schema 与混合任务页面重建。
- 用真实 tshark 重新导出合成 PCAP 时序；新版规则处理 6 录音仍为 7 独立候选、1 聚集。gaps 与 benchmark 混合任务均记录 findings。
- Chromium 实际点击 benchmark 首声标记，音频从 0.1 秒推进到约 0.3 秒；gaps 右轨试听正常。导出/导入 2 条标签（1 条人工补标），CLI review-check 通过。390px 下任务页与嵌入页无横向溢出。
- 中文时序编辑器保留两套工具目录，字段修改和场景 JSON 下载通过；演示入口在 1440px/390px 下核对主链接及布局。唯一控制台资源错误是站点既有 favicon.ico 404。主入口现在指向已提交的指南/生成器，本机生成产物入口明确要求先部署。

4 条 P2 意见均已修复：旧事件过滤不依赖新扩展；每个间隙的 RTP 覆盖不足向上汇总为 partial；全部分组源 PCAP 分析前后摘要一致后才发布时序；演示入口附可执行部署说明。检测规则身份改为 output-gaps-2，旧规则标签需要重新复核。

本轮用 code-readability 复查相对新主线的最终差异及冲突调用点，明确旧/新事件信任条件和时序发布顺序，保留 benchmark 的 findings 回执校验。无额外全仓重构；上述全仓与页面验证针对修正后的代码。

## 初版环境与结果（合并主线前）

| 项目 | 实际验证 |
|---|---|
| macOS arm64 / Python 3.9.6 | 全仓 428 项，0 失败，38 项可选环境测试跳过 |
| Linux aarch64 / Python 3.11.16 | Docker 禁网、源码只读；全仓 428 项，0 失败，20 项可选环境测试跳过 |
| Python 3.12 / SoundFile | gaps 专项 37 项通过，含 provenance 开关前后 NISQA JSONL/CSV 逐字节一致（受控评分器） |
| 旧 QA 行为 | 40 组不同采样率、单/双轨、噪声/直流/静音输入；原 activity 与分块版本输出、QA 完整结果一致 |
| 合成 CLI | 6 文件、7 独立候选、1 聚集；allowed-wait 0、dead-air 1、micro 1、cluster 3、terminal 1、high-occupancy 1 |
| RTP | 实际 tshark 读取合成 PCAP；导出分片、显式映射并关联；缺片、摘要错、采集点错、时钟异常、序号与时间戳回绕测试通过 |
| NISQA | 同源摘要、右轨、跨片段重叠和未评分保留；旧结果降级、错摘要拒绝测试通过；示例无虚构模型分数 |
| 跨机 | macOS 打包 → Linux 安装 wheel 并执行 → 结果 ZIP 回 macOS 重建；6 录音的摘要、fingerprint、间隙区间与关联证据一致 |
| 大证据与中断 | 总时序超过 16 MiB、单片 <=8 MiB 可打包；原事件/评分文件迁移后摘要不变；缺片/损坏返回部分失败；受控 KeyboardInterrupt 保留已完成结果及复核页 |
| 安装包 | wheel 从源码目录之外安装；公共 HTML/JS/vendor/许可证齐全；schema 不导入模型；检测与可信任务页音频引用检查通过 |
| 浏览器 | Chromium / Playwright CLI 0.1.21；本地 nginx 8080，1440px 与 390px；独立页、任务复查页及旧 QA 页实际操作验证 |

初次基础 Linux 镜像缺少 node/tcpdump，使两项旧测试不能启动；仅为验收镜像补齐后重跑通过。验收镜像 `voice-tools-gaps-validation:0.14.1` 基于已有 `voice-tools-executor:0.13.1`，没有重新编译 PJSIP/ViSQOL，也未修改原镜像。模型、生产主机等可选集成测试仍按仓库条件跳过，不能把测试总数解释成全部外部系统已验证。

## 命令和产物核对

[使用文档](output-gaps.md) 中的合成生成、report --rtp-timeline、gaps 批量、schema、review-check、task pack/run/collect/review 已实际执行。`scripts/gaps_demo.py` 的 expected.json 是独立写定的规则预期。

复现基础检测：

```bash
python scripts/gaps_demo.py --out outputs/gaps-validation
voice-tools report build --pcap-group demo-edge outputs/gaps-validation/synthetic.pcap --rtp-port 16000 --rtp-timeline --out outputs/gaps-validation/media
voice-tools gaps analyze outputs/gaps-validation/calls --evidence outputs/gaps-validation/evidence.json --include-audio --out outputs/gaps-validation/review
voice-tools gaps review-check outputs/gaps-validation/review/review.csv --results outputs/gaps-validation/review/results.jsonl
voice-tools schema --tool gaps
python -m unittest discover -s tests -v
```

- 默认 analyze 完成返回 0，有候选不自动返回失败；同一文件加 --fail-on-findings 返回 1。
- 批次加显式 --events 返回 2；evidence-wrong.json 返回 3，声学候选仍在。
- evidence-nisqa.json 关联未评分片段；evidence-legacy.json 展示 legacy_summary / unverified。
- task run 命令遵守原任务工具退出规则，正常完成时返回 0；内部 gaps 步骤记录 exit_code=1 和 findings，不把它算执行崩溃。
- results.jsonl、summary.csv、run.json、report.html、review.html、review.csv 与文档名称一致。docs/cli-schema.json 从真实解析器导出，无手改参数声明。
- NISQA --provenance 参数/schema、服务分段输出兼容已验证。本轮未下载权重或新增真实模型推理；实际评分使用方法沿用 NISQA 专项文档，不用合成 fixture 冒充模型运行。

## 页面验收

独立复核页实际测试：播放时间推进、单轨切换、循环 1.0–1.2 秒区间、缩放、保存自动判断、人工补标、导出两条 CSV、刷新后导入、公式前缀文本往返。导出的 0.6–0.9 秒 WAV 为双轨，帧数与下载的原时间映射一致；CLI review-check 确认 2 标签、其中 1 人工新增。

任务复查页从 Linux 返回的包重建；播放地址解析到步骤下的 data/audio，实际播放推进；同一 CSV 可导入导出并通过 CLI 复核。页面仅执行本机安装资源，未使用包内 HTML。旧 QA 页面单轨试听和旧标签 CSV 导出/再导入通过；旧 freeze/promote/evaluate/compare 由原回归测试覆盖。

390px 下独立页、任务页无页面横向溢出。浏览器未发现应用 JS 异常；本机根 favicon.ico 缺失产生 404，不影响页面或音频资源。最初 Python 简易 HTTP 预览服务不支持所需 Range，导致 seek 无法完成；正式本地 nginx 下播放/定位/循环通过，使用指南明确此服务要求。

本地入口：[输出中途间隙](http://127.0.0.1:8080/voice-output-gaps/)。总导航维护源和运行副本均增加唯一入口，发布只含合成数据。

## 资源记录

macOS arm64、Python 3.9.6、16 kHz 双轨 PCM16；每 10 秒含 1 秒合成间隙；不带旁证、不复制试听音频。每次独立进程，耗时不含生成 WAV，峰值 RSS 包含生成过程。

| 时长 | WAV 大小 | 分析与生成报告耗时 | 峰值 RSS | 候选 |
|---|---:|---:|---:|---:|
| 10 分钟 | 38,400,044 bytes | 0.273 秒 | 150,700,032 bytes（约 144 MiB） | 60 |
| 30 分钟 | 115,200,044 bytes | 1.129 秒 | 393,986,048 bytes（约 376 MiB） | 180 |

音频读取沿用项目最大时长/解码额度；活动、短断音窗口和分轨写入分块处理，结果/事件/包数有显式额度。此记录只说明该机该输入的资源表现，不是吞吐基准或容器内存保证；高采样率、旁证数量及浏览器导出长片段会增加开销。

## 最终审查与边界

按 code-readability Skill 检查新增检测、事件裁剪、包回绕、标签身份、路径迁移、页面适配和默认调用点。将 JSON 对象读取/非有限数字校验命名，明确只对已验证角色过滤，规范化检测身份，保留中断日志，移除无效引用和未使用导入；修复任务页音频目录与返回链接。原默认 QA 阈值、旧标签/黄金集、NISQA 行列和默认 report 分析保持兼容。

未验证：真实录音检出率/误报率、自然语音/背景音乐下的阈值校准、真实业务等待日志完整性、生产采集点/终端播放根因、多机时钟漂移修正。WebRTC VAD 是可选活动后端，不代表本轮已完成真人数据评测。真实录音与生产线路效果必须单独验收。

已合入 benchmark 主线共享入口并完成上述回归；本功能仍通过文件合同衔接 SIP/benchmark，没有修改原工作区。生产线路与真实录音验收边界保持不变。
