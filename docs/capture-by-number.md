# 按主叫或被叫抓包并按会话打包

`capture by-number` 在一台或多台服务器持续采集指定时长，结束后在各服务器检索号码、按 Call-ID 拆分 SIP/RTP/RTCP，再通过 SCP 下载会话 ZIP。本机只接收匹配会话的包；服务器原始分片与处理目录保留，便于检查或恢复。

## 使用

使用已有[主机清单](../examples/capture/hosts.esl.example.json)，配置 SSH、抓包接口、服务器地址、SIP 端口和 RTP 范围。只放一台主机即可单机采集。每台服务器须有 Python 3.9+、dumpcap、tshark、mergecap；FS 快照使用 fs_cli，ESL 可按 [2.0 指南](capture-v2.md) 配置。无需在服务器安装 voice_tools 或 NumPy，命令会上传本次所需的标准库 Python 模块。

```bash
# 先离线核对计划，不连接服务器
voice-tools capture by-number --inventory examples/capture/hosts.esl.example.json \
  --caller 1001 --seconds 300 --dry-run --out outputs/caller-plan

# 主叫 1001，持续 5 分钟；等待远端拆包，再下载
voice-tools --json capture by-number --inventory examples/capture/hosts.esl.example.json \
  --caller 1001 --seconds 300 --out outputs/caller-capture

# 被叫 1002 或 1003，持续 10 分钟
voice-tools capture by-number --inventory examples/capture/hosts.esl.example.json \
  --callee 1002 --callee 1003 --seconds 600 --out outputs/callee-capture

# 同时限制主叫、被叫：1001 呼叫 1002
voice-tools capture by-number --inventory examples/capture/hosts.esl.example.json \
  --caller 1001 --callee 1002 --seconds 300 --out outputs/pair-capture
```

号码为**精确字符串**。同一参数重复表示 OR，主叫与被叫之间为 AND，而且须在同一条可确定方向的观测上满足条件。保留 `+`、国家码及拨号前缀，不自动归一化，不使用正则或子串；如果现网同时出现 `138…` 和 `+86138…`，显式列出两个值。

方向依据为 FS 的 caller/callee 字段，或无 To-tag 的初始 SIP INVITE 的 From/To user。反向 BYE、re-INVITE 和响应不单独作为方向依据。To user 不一定等于号码改写后的 Request-URI；B2BUA 两侧分别按各自观测匹配。不会只因号码相同合并 Call-ID，也不会自动带入另一条桥接腿。

## 服务器执行与结果

号码不适合作为 tcpdump/dumpcap 的 IP/端口过滤条件。此模式先在服务器按清单限定的地址与 SIP/RTP 端口范围抓取，再根据本窗口信令和 FS 记录选择会话。因此原始暂存区会包含范围内其他会话；只有匹配后的会话 ZIP 下载到本地。持续采集期间开始的通话也可被匹配，不局限于启动时已有的通话。

```text
caller-capture/
  job.json                    # 每台主机的远端路径和参数，启动前增量保存
  number.json                 # 总体状态及每台主机的结果
  runtime.zip                 # 部署代码，不包含通话数据
  fs-a/
    host.json                 # 远端采集/处理状态与取回回执
    manifest.json             # ZIP 内清单的本地副本
    sessions.zip
  fs-b/
    ...
```

每台服务器的 ZIP 包含：

```text
manifest.json
sessions/<Call-ID 的 SHA-256>/session.json
sessions/<Call-ID 的 SHA-256>/0001.pcapng
```

同一采集点先合并分片后检索，通常每个 Call-ID 导出一个 PCAPNG。清单保留实际 Call-ID、命中号码、匹配依据、可用 UUID、每个文件的大小和 SHA-256，以及未导出数量。文件名不直接使用号码或 Call-ID。同一 Call-ID 出现在多台服务器时，仍保留在各自的主机包里。

客户端检查 ZIP 大小、SHA-256、条目路径、逐文件摘要和展开字节数；同时核对原任务号码条件、会话数量、内外 Call-ID/PCAP 清单及 partial 状态。验证成功才把 `.part` 改名为 `sessions.zip`。不会自动解压。

```bash
mkdir -p outputs/unpacked-fs-a
unzip outputs/caller-capture/fs-a/sessions.zip -d outputs/unpacked-fs-a

# 将下面 SESSION_DIRECTORY 替换为 manifest.json 中该会话的 directory
voice-tools report build \
  --session-export outputs/unpacked-fs-a/SESSION_DIRECTORY \
  --decode-rtp --out outputs/number-report
```

## 断线与停止

任务脱离 SSH 在服务器运行，采集和处理分别有限时。关闭本机命令或 SSH 断线后，服务器仍继续至限时结束。`job.json` 在第一次远端创建操作前记录路径；下载失败不会删除远端包。

号码包下载期间 Ctrl+C 会取消本次本地 SCP/SSH 传输，保留远端包，之后可重取；已经完成的其他主机结果保留。恢复前校验 job 中的时限和容量，避免损坏任务导致无界等待。

```bash
# 已完成任务重新下载；未完成则返回部分结果，不自动无限等待
voice-tools capture fetch-number --job outputs/caller-capture \
  --out outputs/caller-retry

# 等待远端采集与处理完成后下载
voice-tools capture fetch-number --job outputs/caller-capture --wait \
  --out outputs/caller-retry-wait

# 提前停止采集，随后仍拆包；结果按提前结束标 partial
voice-tools capture ring-stop --job outputs/caller-capture
```

`ring-stop` 只停止尚在进行的采集；已经进入拆包的任务受 `--processing-seconds` 限制。远端原始分片、索引及包不会自动删除。清单保存了 `/tmp/voice-tools-…` 路径，确认不再需要后由运维按实际留存制度清理。

## 限额与状态

| 参数 | 默认值 | 含义 |
|---|---:|---|
| `--seconds` | 300 | 每台主机的采集时长，各机尽力并发启动 |
| `--max-mib` | 512 | 每台主机原始分片总额度，最大 2048 MiB；达到额度会提前结束并标 partial |
| `--segment-seconds` | 60 | 分片间隔；此模式最多 2048 片 |
| `--bundle-mib` | 512 | 每台主机 ZIP 及其中未压缩内容的额度，2–2048 MiB |
| `--max-sessions` | 1000 | 每台主机最多导出的匹配 Call-ID 数，1–1000 |
| `--max-packets` | 1000000 | 索引包数限制，最大 5000000；达到上限标 partial |
| `--processing-seconds` | 1800 | 采集结束后的拆包/压缩总时限，30–86400 秒；超时终止处理及其子进程 |
| `--fs-snapshot-seconds` | 10 | FS 快照间隔，0 关闭或 5–30 秒；建议搭配 ESL 捕捉短通话 |

这些额度分别约束原始包与最终会话包，**不等于整个任务目录的总磁盘上限**。远端合并 PCAP、SQLite、tshark 临时文件及正在导出的单个会话还需要工作空间；建议按采集量预留并验证目标机资源。采集需要 sudo 时，分析阶段降回 SSH 用户。

导出会按匹配会话多次读取已停止的 PCAP；匹配会话很多时，处理成本随数量增加。应结合目标机 CPU/磁盘负载设置窗口、会话上限和处理时限。处理超时保留原始抓包，但尚未完成的 ZIP 不作为有效结果下载。

每包最多 50000 个条目，总清单最多 4 MiB，单会话清单最多 16 MiB。总条目/清单达到额度时保留已完成会话并标 partial；单会话清单超限则跳过该会话并记录错误。最多保存 64 条详细导出错误，`error_count` 记录总错误数。损坏的 FS 行汇总为 gap，其余有效观测继续索引，不回滚整个映射文件。

退出码 0 表示流程完成，3 表示某主机失败或结果 partial，2 表示参数错误。本机未启动采集前会检查远端依赖，某机依赖缺失保存在该机失败回执中，整体返回 3。无匹配会话仍有仅含清单的 ZIP，`matched_sessions=0`；没有任何可确认方向的观测时标 partial。超出会话数/打包额度时保留已完成会话，并列出 omitted 数量。一次主机失败不丢弃其他主机结果。

PCAP 只覆盖请求窗口，不能补回此前信令或之后的媒体；快照取样、短通话、加密 SIP、缺失初始 INVITE、媒体重绑定及端口复用均可能导致遗漏或候选关联。SIP TCP 重组需要的共享帧可能夹带邻近消息。SRTP 不解密。目标 FreeSWITCH 版本、实际负载与远端权限仍需现场验收。

## 本地验证

2026-09-19：在 main `6849b18` 上新增 16 项测试，覆盖精确角色匹配、跨分片拆包、FS 映射、无匹配、额度、损坏包、处理超时、多主机失败隔离及下载/启动回执丢失后的恢复。完整回归发现 259 项：248 项通过，11 项可选 PJSUA2 loopback 测试因环境条件未满足而跳过；最终号码测试 16 项全部通过。

部署 ZIP 的 Python 进程链在本地模拟 SSH/采集下实际运行，索引与拆包使用真实 tshark/mergecap。两会话 ZIP 解压后，经真实 `report build` 生成两个独立采集分析组，结果非 partial；CLI schema、124 条本地文档链接和 5 个示例报告资源引用检查通过。上述输入均为合成数据，未连接生产服务器。

同日深度检查新增 14 项反例和边界测试，完整回归共 273 项：262 项通过，11 项可选 PJSUA2 测试跳过。原版本生成的两会话包通过新校验器兼容性检查。复现、修复和剩余边界见[深度检查记录](capture-by-number-review.md)。
